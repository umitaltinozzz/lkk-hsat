"""Phase 5 campaign: standard and real-world benchmark evaluation.

Stages, in order, each of which can be resumed independently:

  0  verify predecessors and the corpus manifest by hash
  1  telemetry pass  -- LKK front-end only, no solving; produces the strata
  2  calibration     -- choose the timeout, on the calibration split only
  3  selector fit    -- thresholds fitted on the calibration split only
  4  correctness     -- every completed definite answer must agree
  5  evaluation      -- modes A-E over the evaluation split
  6  ablation        -- variants A-F over a representative subset
  7  report          -- metrics, plots, RESULTS.md

The calibration and evaluation splits are disjoint and assigned deterministically
by hashing the instance's own SHA-256, so no tuning can see the test set.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from benchmark.harness import machine_metadata, sha256_file
from phase3.common import tree_digest, write_csv
from phase5 import classify as classifier
from phase5 import plots
from phase5 import selector as selector_tools

DEFINITE = {"SATISFIABLE", "UNSATISFIABLE"}
NATIVE = "/opt/solvers/bin/lkk-native5"
FLAGS = "g++ -std=c++17 -O3 -DNDEBUG -Wall -Wextra; CaDiCaL: -O3 -DNDEBUG -DNCONTRACTS -DNTRACING"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_of(cnf_sha256: str, calibration_fraction: float) -> str:
    """Deterministic, content-addressed split; independent of any measurement."""
    bucket = int(hashlib.sha256(("split:" + cnf_sha256).encode()).hexdigest()[:8], 16)
    return "calibration" if (bucket % 10000) < calibration_fraction * 10000 else "evaluation"


def par2(runtime_ms: float, status: str, timeout_ms: float, multiplier: int = 2) -> float:
    return float(runtime_ms) if status in DEFINITE else timeout_ms * multiplier


# --------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------

def satlib_trailer_start(cnf: Path) -> int | None:
    """Byte offset of SATLIB's trailer, or None if the file does not have one.

    SATLIB appends a `%` line and a lone `0` after the last clause. Neither is
    DIMACS: CaDiCaL stops at the `%` with "expected digit or '-'" and, if only
    the zero is present, both solvers count it as an extra clause and abort with
    "too many clauses". The trailer carries no part of the formula, so removing
    it cannot change an answer -- but leaving it in place cost the first attempt
    at this campaign every baseline measurement on 420 of 1192 instances.
    """
    offset = 0
    with cnf.open("rb") as stream:
        for raw in stream:
            text = raw.strip()
            if text.startswith(b"%"):
                return offset
            offset += len(raw)
    return None


def sanitized_for_baseline(cnf: Path, scratch: Path) -> Path:
    """The same formula with the trailer removed, or the original if there is none."""
    cut = satlib_trailer_start(cnf)
    if cut is None:
        return cnf
    scratch.parent.mkdir(parents=True, exist_ok=True)
    with cnf.open("rb") as src, scratch.open("wb") as dst:
        dst.write(src.read(cut))
    return scratch


def direct_run(executable: str, args: Iterable[str], cnf: Path, seed: int,
               timeout: float, log: Path, repo: Path) -> dict[str, Any]:
    log.parent.mkdir(parents=True, exist_ok=True)
    time_path = log.with_suffix(".time")
    # Only the baselines need this; our own parser handles the trailer directly.
    cnf = sanitized_for_baseline(cnf, log.with_suffix(".baseline.cnf"))
    rendered = [a.format(seed=seed, cnf=str(cnf)) for a in args]
    command = ["/usr/bin/time", "-f", "%M", "-o", str(time_path),
               "/usr/bin/timeout", "--signal=KILL", str(timeout), executable, *rendered]
    start = time.perf_counter_ns()
    with log.open("wb") as stream:
        completed = subprocess.run(command, cwd=repo, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT,
                                   timeout=timeout + 30, check=False)
    runtime = (time.perf_counter_ns() - start) / 1e6
    timed_out = completed.returncode in {124, 137}
    status = "TIMEOUT" if timed_out else {10: "SATISFIABLE", 20: "UNSATISFIABLE"}.get(
        completed.returncode, "UNKNOWN")
    result = {"runtime_ms": runtime, "status": status, "timed_out": timed_out,
              "exit_code": completed.returncode, "raw_log": log.relative_to(repo).as_posix()}
    result.update(_parse_solver_stats(log))
    result["peak_rss_kb"] = _rss(time_path)
    return result


def _rss(path: Path) -> int | None:
    if not path.exists():
        return None
    values = [x.strip() for x in path.read_text(errors="replace").splitlines()
              if x.strip().isdigit()]
    return int(values[-1]) if values else None


def _parse_solver_stats(log: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {"conflicts": None, "decisions": None,
                             "propagations": None, "restarts": None}
    if not log.exists():
        return stats
    for line in log.read_text(errors="replace").splitlines():
        if not line.startswith("c "):
            continue
        body = line[2:].strip()
        for key in stats:
            if body.startswith(key + ":") or body.startswith(key + " "):
                for token in body.replace(":", " ").split()[1:]:
                    try:
                        stats[key] = int(token)
                        break
                    except ValueError:
                        continue
    return stats


def native_run(repo: Path, cnf: Path, sha: str, seed: int, timeout: float, log: Path,
               fallback: str, stop_after: str, config: dict[str, Any],
               selector_params: dict[str, int] | None = None,
               telemetry_only: bool = False, witness: Path | None = None) -> dict[str, Any]:
    log.parent.mkdir(parents=True, exist_ok=True)
    time_path = log.with_suffix(".time")
    command = ["/usr/bin/time", "-f", "%M", "-o", str(time_path),
               "/usr/bin/timeout", "--signal=KILL", str(timeout),
               NATIVE, "--cnf", str(cnf), "--seed", str(seed), "--sha256", sha,
               "--derived-budget", str(config["native"]["derived_clause_budget"]),
               # Fitted on the calibration split by phase5/tune.py, never on the
               # evaluation split; see the provenance block in config.json.
               "--family-check-budget", str(config["native"]["family_check_budget"]),
               "--fallback", fallback, "--stop-after", stop_after,
               "--fallback-timeout", str(timeout),
               "--kissat", config["kissat"]["executable"],
               "--kissat-tmp", str(log.with_suffix(".serialized.cnf")),
               "--kissat-log", str(log.with_suffix(".kissat.log"))]
    # Phase 6. Off by default so a rerun of the Phase 5 configuration reproduces
    # the Phase 5 numbers; see the provenance block in config.json.
    if config["native"].get("atleast_boxes"):
        command.append("--atleast-boxes")
    if telemetry_only:
        command.append("--telemetry-only")
    if witness is not None:
        witness.parent.mkdir(parents=True, exist_ok=True)
        command += ["--witness", str(witness)]
    if selector_params:
        command += ["--selector-min-boxes", str(selector_params["min_boxes"]),
                    "--selector-min-clauses", str(selector_params["min_clauses"]),
                    "--selector-min-signal", str(selector_params["min_signal"])]
    start = time.perf_counter_ns()
    with log.open("wb") as stream:
        completed = subprocess.run(command, cwd=repo, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT,
                                   timeout=timeout + 30, check=False)
    runtime = (time.perf_counter_ns() - start) / 1e6
    timed_out = completed.returncode in {124, 137}
    result: dict[str, Any] = {"runtime_ms": runtime, "timed_out": timed_out,
                              "exit_code": completed.returncode,
                              "status": "TIMEOUT" if timed_out else "ERROR"}
    if not timed_out:
        lines = log.read_text(errors="replace").splitlines()
        payload = next((json.loads(x) for x in reversed(lines) if x.startswith("{")), None)
        if payload:
            result.update(payload)
            result["runtime_ms"] = runtime
            result["status"] = payload["final_result"]
    result["peak_rss_kb_external"] = _rss(time_path)
    result["raw_log"] = log.relative_to(repo).as_posix()
    return result


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------

def load_corpus(config: dict[str, Any], repo: Path, verify: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative in config["manifests"]:
        path = repo / relative
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if row.get("status") != "OK":
                    continue
                cnf = repo / row["path"] if not row["path"].startswith("/") else Path(row["path"])
                if not cnf.exists():
                    continue
                row["path"] = cnf.as_posix()
                rows.append(row)
    if verify:
        for row in rows:
            actual = sha256_file(Path(row["path"]))
            if actual != row["cnf_sha256"]:
                raise RuntimeError(f"corpus hash mismatch for {row['instance']}: "
                                   f"manifest {row['cnf_sha256']} vs actual {actual}")
    return rows


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------

def stage_telemetry(repo: Path, out: Path, corpus: list[dict[str, Any]],
                    config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    timeout = float(config["telemetry_timeout_seconds"])
    for index, item in enumerate(corpus):
        log = out / "logs" / "telemetry" / f"{index+1:05d}_{Path(item['path']).stem}.log"
        run = native_run(repo, Path(item["path"]), item["cnf_sha256"], 1, timeout, log,
                         "none", "flow", config, telemetry_only=True)
        stratum, why = classifier.classify(run)
        rows.append({
            "instance": item["instance"], "source": item["source"], "set": item["set"],
            "domain": item["domain"], "path": item["path"], "cnf_sha256": item["cnf_sha256"],
            "declared_result": item.get("declared_result", ""),
            "split": split_of(item["cnf_sha256"], float(config["calibration_fraction"])),
            "stratum": stratum, "stratum_reason": why,
            "telemetry_status": run.get("status"),
            "variables": run.get("variables"), "clauses": run.get("clauses"),
            "parse_ms": run.get("parse_ms"), "gate_ms": run.get("gate_ms"),
            "gate_decision": run.get("gate_decision"), "gate_reason": run.get("gate_reason"),
            "gate_monotone_positive": run.get("gate_monotone_positive"),
            "gate_monotone_negative": run.get("gate_monotone_negative"),
            "gate_mirror_pairs": run.get("gate_mirror_pairs"),
            "benefit_gate_ms": run.get("benefit_gate_ms"),
            "benefit_gate_decision": run.get("benefit_gate_decision"),
            "recovery_ms": run.get("recovery_ms"), "recovery_status": run.get("recovery_status"),
            "recovery_derived": run.get("recovery_derived"),
            "registry_ms": run.get("registry_ms"), "registry_status": run.get("registry_status"),
            "registry_reason": run.get("registry_reason"),
            "boxes_found": run.get("boxes_found"), "capacity_groups": run.get("capacity_groups"),
            "flow_ms": run.get("flow_ms"), "flow_nodes": run.get("flow_nodes"),
            "flow_edges": run.get("flow_edges"),
            "flow_total_demand": run.get("flow_total_demand"),
            "flow_maximum": run.get("flow_maximum"),
            "structural_result": run.get("structural_result"),
            "front_end_ms": run.get("total_end_to_end_ms"),
            "peak_rss_kb": run.get("peak_rss_kb_external"),
        })
        write_csv(out / "benchmark_manifest.csv", rows)
    write_csv(out / "strata_summary.csv", classifier.summarize(rows))
    return rows


def stage_calibration(repo: Path, out: Path, telemetry: list[dict[str, Any]],
                      config: dict[str, Any]) -> dict[str, Any]:
    """Pick the timeout that yields a usable solved/timeout mix, not the one that
    maximises any difference. Only calibration-split instances are touched.

    Each instance is measured once at the largest timeout in the grid. What a
    smaller timeout would have produced follows from the runtime that was
    actually observed, so the grid costs one pass rather than one pass per
    candidate timeout.
    """
    pool = [r for r in telemetry if r["split"] == "calibration"]
    pool = _stratified_sample(pool, int(config["calibration_max_instances"]))
    grid = sorted(float(t) for t in config["calibration_timeout_grid"])
    ceiling = grid[-1]
    measured: list[dict[str, Any]] = []
    for index, item in enumerate(pool):
        for mode in ("A_cadical", "B_kissat"):
            spec = config["modes"][mode]
            solver = config[spec["solver"]]
            log = (out / "logs" / "calibration" /
                   f"{index+1:04d}_{Path(item['path']).stem}__{mode}.log")
            run = direct_run(solver["executable"], solver["args"], Path(item["path"]),
                             1, ceiling, log, repo)
            measured.append({"instance": item["instance"], "stratum": item["stratum"],
                             "domain": item["domain"], "mode": mode,
                             "measured_at_timeout_s": ceiling,
                             **{k: run[k] for k in ("runtime_ms", "status", "timed_out")}})
        write_csv(out / "calibration_runs.csv", measured)
    rows = []
    for timeout in grid:
        for run in measured:
            solved = (run["status"] in DEFINITE
                      and float(run["runtime_ms"]) <= timeout * 1000)
            rows.append({"timeout_s": timeout, "instance": run["instance"],
                         "stratum": run["stratum"], "domain": run["domain"],
                         "mode": run["mode"], "runtime_ms": run["runtime_ms"],
                         "status": run["status"] if solved else "TIMEOUT",
                         "timed_out": not solved,
                         "derived_from_timeout_s": ceiling})
    write_csv(out / "calibration.csv", rows)
    chosen = _choose_timeout(rows, config)
    (out / "calibration_choice.json").write_text(json.dumps(chosen, indent=2) + "\n")
    return chosen


def _choose_timeout(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """Smallest timeout whose solved fraction is within 2 points of the largest."""
    by_timeout: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_timeout[float(row["timeout_s"])].append(row)
    summary = []
    for timeout in sorted(by_timeout):
        runs = by_timeout[timeout]
        solved = sum(1 for r in runs if r["status"] in DEFINITE)
        summary.append({"timeout_s": timeout, "runs": len(runs), "solved": solved,
                        "solved_fraction": solved / len(runs) if runs else 0.0})
    best = max(summary, key=lambda s: s["solved_fraction"])
    for entry in summary:
        if entry["solved_fraction"] >= best["solved_fraction"] - 0.02:
            return {"chosen_timeout_seconds": entry["timeout_s"], "grid": summary,
                    "rule": "smallest timeout within 2 points of the best solved fraction"}
    return {"chosen_timeout_seconds": best["timeout_s"], "grid": summary,
            "rule": "best solved fraction"}


def _stratified_sample(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Deterministic round-robin over strata, so no stratum is crowded out."""
    if limit <= 0 or len(rows) <= limit:
        return list(rows)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(rows, key=lambda r: r["cnf_sha256"]):
        buckets[row["stratum"]].append(row)
    picked: list[dict[str, Any]] = []
    while len(picked) < limit and any(buckets.values()):
        for stratum in sorted(buckets):
            if buckets[stratum] and len(picked) < limit:
                picked.append(buckets[stratum].pop(0))
    return picked


def stage_selector_fit(repo: Path, out: Path, telemetry: list[dict[str, Any]],
                       config: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Measure both fallbacks on the calibration split, then fit the thresholds."""
    pool = [r for r in telemetry if r["split"] == "calibration"]
    pool = _stratified_sample(pool, int(config["calibration_max_instances"]))
    cases = []
    rows = []
    for index, item in enumerate(pool):
        measured = {}
        for mode, fallback in (("C_lkk_cadical", "cadical"), ("D_lkk_kissat", "kissat")):
            log = (out / "logs" / "selector_fit" /
                   f"{index+1:04d}_{Path(item['path']).stem}__{mode}.log")
            run = native_run(repo, Path(item["path"]), item["cnf_sha256"], 1, timeout, log,
                             fallback, "flow", config)
            measured[fallback] = run
            rows.append({"instance": item["instance"], "stratum": item["stratum"],
                         "mode": mode, "runtime_ms": run["runtime_ms"],
                         "status": run["status"]})
        telemetry_view = {k: item[k] for k in selector_tools.PERMITTED_FEATURES if k in item}
        telemetry_view["registry_status"] = item.get("registry_status", "")
        cases.append({"telemetry": telemetry_view,
                      "cadical": measured["cadical"], "kissat": measured["kissat"]})
        write_csv(out / "selector_fit_runs.csv", rows)
    fitted = selector_tools.fit(cases, config["selector"]["search_grid"], timeout * 1000)
    payload = {"parameters": fitted["best"]["parameters"], "calibration": fitted["best"],
               "form": config["selector"]["form"],
               "fitted_on": "calibration split only", "instances": len(cases)}
    (out / "selector_config.json").write_text(json.dumps(payload, indent=2) + "\n")
    write_csv(out / "selector_grid.csv",
              [{**t["parameters"], **{k: v for k, v in t.items() if k != "parameters"}}
               for t in fitted["trials"]])
    return payload


def stage_evaluation(repo: Path, out: Path, telemetry: list[dict[str, Any]],
                     config: dict[str, Any], timeout: float,
                     selector_params: dict[str, int], limit: int) -> list[dict[str, Any]]:
    pool = [r for r in telemetry if r["split"] == "evaluation"]
    if limit:
        pool = _stratified_sample(pool, limit)
    modes = list(config["modes"])
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(pool):
        for seed in config["evaluation_seeds"]:
            rotation = (index + seed) % len(modes)
            for mode in modes[rotation:] + modes[:rotation]:
                spec = config["modes"][mode]
                name = f"{index+1:05d}_{Path(item['path']).stem}__{mode}__seed{seed}"
                log = out / "logs" / "evaluation" / f"{name}.log"
                if spec["kind"] == "direct":
                    solver = config[spec["solver"]]
                    run = direct_run(solver["executable"], solver["args"],
                                     Path(item["path"]), seed, timeout, log, repo)
                else:
                    run = native_run(repo, Path(item["path"]), item["cnf_sha256"], seed,
                                     timeout, log, spec["fallback"], spec["stop_after"],
                                     config, selector_params,
                                     witness=out / "logs" / "witnesses" / f"{name}.json")
                rows.append(_result_row(item, mode, seed, run, timeout))
                write_csv(out / "standard_results.csv", rows)
    return rows


def _result_row(item: dict[str, Any], mode: str, seed: int, run: dict[str, Any],
                timeout: float) -> dict[str, Any]:
    return {
        "instance": item["instance"], "source": item["source"], "set": item["set"],
        "domain": item["domain"], "stratum": item["stratum"], "split": item["split"],
        "cnf_sha256": item["cnf_sha256"], "declared_result": item.get("declared_result", ""),
        "mode": mode, "seed": seed,
        "status": run["status"], "runtime_ms": run["runtime_ms"],
        "par2_ms": par2(run["runtime_ms"], run["status"], timeout * 1000),
        "timed_out": run.get("timed_out"),
        "peak_rss_kb": run.get("peak_rss_kb_external", run.get("peak_rss_kb")),
        "conflicts": run.get("fallback_conflicts", run.get("conflicts")),
        "decisions": run.get("fallback_decisions", run.get("decisions")),
        "propagations": run.get("fallback_propagations", run.get("propagations")),
        "restarts": run.get("fallback_restarts", run.get("restarts")),
        "parse_ms": run.get("parse_ms"), "gate_ms": run.get("gate_ms"),
        "recovery_ms": run.get("recovery_ms"), "registry_ms": run.get("registry_ms"),
        "flow_ms": run.get("flow_ms"), "witness_ms": run.get("witness_ms"),
        "structural_result": run.get("structural_result"),
        "fallback_used": run.get("fallback_used"),
        "fallback_engine": run.get("fallback_engine"),
        "selector_choice": run.get("selector_choice"),
        "selector_reason": run.get("selector_reason"),
        "fallback_ms": run.get("fallback_ms"),
        "fallback_serialize_ms": run.get("fallback_serialize_ms"),
        "fallback_overhead_ms": run.get("fallback_overhead_ms"),
        "total_end_to_end_ms": run.get("total_end_to_end_ms", run["runtime_ms"]),
        "raw_log": run.get("raw_log", ""),
    }


def stage_ablation(repo: Path, out: Path, telemetry: list[dict[str, Any]],
                   config: dict[str, Any], timeout: float,
                   selector_params: dict[str, int]) -> list[dict[str, Any]]:
    pool = _stratified_sample([r for r in telemetry if r["split"] == "evaluation"],
                              int(config["ablation_max_instances"]))
    rows: list[dict[str, Any]] = []
    variants = list(config["ablation_variants"])
    for index, item in enumerate(pool):
        for seed in config["ablation_seeds"]:
            rotation = (index + seed) % len(variants)
            for variant in variants[rotation:] + variants[:rotation]:
                spec = config["ablation_variants"][variant]
                log = (out / "logs" / "ablation" /
                       f"{index+1:05d}_{Path(item['path']).stem}__{variant}__seed{seed}.log")
                run = native_run(repo, Path(item["path"]), item["cnf_sha256"], seed, timeout,
                                 log, spec["fallback"], spec["stop_after"], config,
                                 selector_params)
                rows.append({**_result_row(item, variant, seed, run, timeout),
                             "stop_after": spec["stop_after"]})
                write_csv(out / "ablation.csv", rows)
    return rows


def stage_correctness(out: Path, results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Every definite answer for an instance must agree, across all modes."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        grouped[row["instance"]].append(row)
    rows: list[dict[str, Any]] = []
    failures = 0
    for instance, runs in sorted(grouped.items()):
        answers = {r["mode"]: r["status"] for r in runs}
        definite = {v for v in answers.values() if v in DEFINITE}
        declared = runs[0].get("declared_result", "")
        conflict = len(definite) > 1
        against_declared = bool(declared in DEFINITE and definite and declared not in definite)
        passed = not conflict and not against_declared
        rows.append({
            "instance": instance, "source": runs[0]["source"], "domain": runs[0]["domain"],
            "stratum": runs[0]["stratum"], "declared_result": declared,
            "distinct_definite_answers": " ".join(sorted(definite)) or "none",
            "modes_solved": sum(1 for v in answers.values() if v in DEFINITE),
            "modes_run": len(answers),
            **{f"answer_{m}": answers.get(m, "") for m in sorted(answers)},
            "conflicting_answers": conflict,
            "disagrees_with_declared": against_declared,
            "passed": passed,
        })
        if not passed:
            failures += 1
            (out / "failures").mkdir(parents=True, exist_ok=True)
            (out / "failures" / f"correctness_{instance}.json").write_text(
                json.dumps({"instance": instance, "runs": runs}, indent=2, default=str) + "\n")
    write_csv(out / "correctness.csv", rows)
    return rows, failures


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def solver_summary(results: list[dict[str, Any]], timeout_ms: float) -> list[dict[str, Any]]:
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        by_mode[row["mode"]].append(row)
    solved_everywhere = _commonly_solved(results)
    out = []
    for mode in sorted(by_mode):
        runs = by_mode[mode]
        solved = [r for r in runs if r["status"] in DEFINITE]
        common = [r for r in solved if r["instance"] in solved_everywhere]
        geo = 0.0
        if common:
            geo = math_exp(sum(math_log(max(float(r["runtime_ms"]), 0.001))
                               for r in common) / len(common))
        out.append({
            "mode": mode, "runs": len(runs), "solved": len(solved),
            "timeouts": sum(1 for r in runs if r["status"] == "TIMEOUT"),
            "unknown": sum(1 for r in runs if r["status"] not in DEFINITE
                           and r["status"] != "TIMEOUT"),
            "par2_total_ms": sum(float(r["par2_ms"]) for r in runs),
            "par2_mean_ms": statistics.mean(float(r["par2_ms"]) for r in runs) if runs else 0,
            "median_runtime_ms": statistics.median(
                float(r["runtime_ms"]) for r in solved) if solved else "",
            "geomean_commonly_solved_ms": geo,
            "commonly_solved": len(common),
            "peak_rss_kb_median": statistics.median(
                [float(r["peak_rss_kb"]) for r in runs if r.get("peak_rss_kb")] or [0]),
        })
    return out


def math_log(x: float) -> float:
    import math
    return math.log(x)


def math_exp(x: float) -> float:
    import math
    return math.exp(x)


def _commonly_solved(results: list[dict[str, Any]]) -> set[str]:
    by_instance: dict[str, dict[str, str]] = defaultdict(dict)
    for row in results:
        by_instance[row["instance"]][row["mode"]] = row["status"]
    modes = {row["mode"] for row in results}
    return {instance for instance, answers in by_instance.items()
            if len(answers) == len(modes) and all(v in DEFINITE for v in answers.values())}


def structural_direct_solves(telemetry: list[dict[str, Any]],
                             results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline: dict[tuple[str, str], dict[str, Any]] = {}
    for row in results:
        baseline[(row["instance"], row["mode"])] = row
    rows = []
    for item in telemetry:
        if item.get("structural_result") != "UNSAT":
            continue
        cad = baseline.get((item["instance"], "A_cadical"), {})
        kis = baseline.get((item["instance"], "B_kissat"), {})
        lkk = baseline.get((item["instance"], "C_lkk_cadical"), {})
        rows.append({
            "instance": item["instance"], "source": item["source"], "domain": item["domain"],
            "stratum": item["stratum"],
            "boxes": item.get("boxes_found"), "resources": item.get("capacity_groups"),
            "total_demand": item.get("flow_total_demand"),
            "maximum_flow": item.get("flow_maximum"),
            "flow_nodes": item.get("flow_nodes"), "flow_edges": item.get("flow_edges"),
            "structural_solve_ms": item.get("front_end_ms"),
            "witness_ms": lkk.get("witness_ms", ""),
            "lkk_runtime_ms": lkk.get("runtime_ms", ""),
            "cadical_runtime_ms": cad.get("runtime_ms", ""),
            "cadical_status": cad.get("status", ""),
            "kissat_runtime_ms": kis.get("runtime_ms", ""),
            "kissat_status": kis.get("status", ""),
        })
    return rows


def selector_results(results: list[dict[str, Any]], timeout_ms: float,
                     fitted: dict[str, Any]) -> list[dict[str, Any]]:
    by_instance: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in results:
        by_instance[row["instance"]][row["mode"]] = row
    rows = []
    for instance, modes in sorted(by_instance.items()):
        cad, kis, sel = (modes.get("C_lkk_cadical"), modes.get("D_lkk_kissat"),
                         modes.get("E_lkk_selector"))
        if not (cad and kis and sel):
            continue
        c, k, s = (float(cad["par2_ms"]), float(kis["par2_ms"]), float(sel["par2_ms"]))
        best = min(c, k)
        rows.append({
            "instance": instance, "stratum": cad["stratum"], "domain": cad["domain"],
            "selector_choice": sel.get("selector_choice", ""),
            "selector_reason": sel.get("selector_reason", ""),
            "lkk_cadical_par2_ms": c, "lkk_kissat_par2_ms": k, "selector_par2_ms": s,
            "oracle_par2_ms": best, "regret_ms": s - best,
            "picked_faster": abs(s - best) < 1e-9,
            "harmful": s > best + 1e-9, "tie": abs(c - k) < 1e-9,
            "selector_ms": sel.get("selector_ms", ""),
        })
    return rows


def cactus_data(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_mode: dict[str, list[float]] = defaultdict(list)
    for row in results:
        if row["status"] in DEFINITE:
            by_mode[row["mode"]].append(float(row["runtime_ms"]))
    rows = []
    for mode, values in sorted(by_mode.items()):
        for index, value in enumerate(sorted(values), start=1):
            rows.append({"mode": mode, "solved_rank": index, "runtime_ms": value})
    return rows


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", type=Path, default=Path("phase5/config.json"))
    parser.add_argument("--evaluation-limit", type=int, default=0,
                        help="cap the evaluation split (0 = use all of it)")
    parser.add_argument("--skip-hash-verify", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate setup and print the plan without measuring")
    args = parser.parse_args()
    repo = Path.cwd().resolve()
    out = args.output.resolve()
    config = json.loads(args.config.read_text(encoding="utf-8"))

    corpus = load_corpus(config, repo, verify=not args.skip_hash_verify and not args.dry_run)
    before = {p: tree_digest(repo / p) for p in config["immutable_predecessors"]
              if (repo / p).exists()}
    for path, expected in config["immutable_predecessors"].items():
        if expected and before.get(path, {}).get("tree_sha256") != expected:
            raise RuntimeError(f"immutable predecessor mismatch: {path}")

    if args.dry_run:
        by_source: dict[str, int] = defaultdict(int)
        for row in corpus:
            by_source[row["source"]] += 1
        modes, variants = len(config["modes"]), len(config["ablation_variants"])
        calibration = min(len(corpus), int(config["calibration_max_instances"]))
        evaluation = args.evaluation_limit or int(len(corpus) * (1 - config["calibration_fraction"]))
        plan = {
            "corpus_instances": len(corpus), "by_source": dict(by_source),
            "predecessors_verified": sorted(before),
            "planned_runs": {
                "telemetry": len(corpus),
                "calibration": calibration * 2,
                "selector_fit": calibration * 2,
                "evaluation": evaluation * modes * len(config["evaluation_seeds"]),
                "ablation": min(evaluation, int(config["ablation_max_instances"])) * variants,
            },
        }
        plan["planned_runs"]["total"] = sum(plan["planned_runs"].values())
        print(json.dumps(plan, indent=2))
        return 0

    if out.exists() and any(out.iterdir()):
        raise RuntimeError("non-empty output directory")
    for directory in (out, out / "logs", out / "failures", out / "plots"):
        directory.mkdir(parents=True, exist_ok=True)

    metadata: dict[str, Any] = {
        "phase": 5, "run_id": args.run_id, "started_utc": now(),
        "machine": machine_metadata(), "python": sys.version, "compiler_flags": FLAGS,
        "native_binary_sha256": sha256_file(Path(NATIVE)),
        "native_image_id": os.environ.get("LKK_NATIVE_IMAGE_ID"),
        "cadical_commit": "c60730422e758ef1cebe7aeddf2dda31c996bf04",
        "kissat_commit": "8af8e56f174b778aef3aa45af9f739b2a5f492c2",
        "predecessors_before": before, "config": config,
        "corpus_instances": len(corpus),
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    telemetry = stage_telemetry(repo, out, corpus, config)
    calibration = stage_calibration(repo, out, telemetry, config)
    timeout = float(calibration["chosen_timeout_seconds"])
    fitted = stage_selector_fit(repo, out, telemetry, config, timeout)
    results = stage_evaluation(repo, out, telemetry, config, timeout,
                               fitted["parameters"], args.evaluation_limit)
    correctness, failures = stage_correctness(out, results)
    if failures:
        metadata.update({"stopped_after_correctness": True, "correctness_failures": failures,
                         "completed_utc": now()})
        (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        return 2

    ablation = stage_ablation(repo, out, telemetry, config, timeout, fitted["parameters"])
    write_csv(out / "solver_summary.csv", solver_summary(results, timeout * 1000))
    write_csv(out / "selector_results.csv",
              selector_results(results, timeout * 1000, fitted) or [{"instance": "none"}])
    write_csv(out / "structural_direct_solves.csv",
              structural_direct_solves(telemetry, results) or [{"instance": "none"}])
    write_csv(out / "cactus_data.csv", cactus_data(results) or [{"mode": "none"}])
    write_csv(out / "phase_timings.csv",
              [{k: r.get(k) for k in ("instance", "mode", "seed", "parse_ms", "gate_ms",
                                      "recovery_ms", "registry_ms", "flow_ms", "witness_ms",
                                      "fallback_ms", "fallback_serialize_ms",
                                      "fallback_overhead_ms", "total_end_to_end_ms",
                                      "structural_result", "fallback_used", "peak_rss_kb")}
               for r in results if r["mode"].startswith(("C_", "D_", "E_"))])
    domain_rows = [r for r in results if r["domain"] not in ("random_control",
                                                             "competition_main")]
    write_csv(out / "domain_results.csv", domain_rows or [{"instance": "none"}])

    from phase5.report import render
    written = _draw_plots(out, results, telemetry, timeout * 1000)
    (out / "RESULTS.md").write_text(
        render(args.run_id, telemetry, results, ablation, correctness, calibration,
               fitted, out, timeout), encoding="utf-8")

    after = {p: tree_digest(repo / p) for p in before}
    immutable = all(before[p]["tree_sha256"] == after[p]["tree_sha256"] for p in before)
    metadata.update({"completed_utc": now(), "predecessors_after": after,
                     "predecessors_immutable": immutable,
                     "chosen_timeout_seconds": timeout,
                     "selector_parameters": fitted["parameters"],
                     "evaluation_runs": len(results), "ablation_runs": len(ablation),
                     "correctness_cases": len(correctness), "correctness_failures": failures,
                     "plots": written})
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"run_id": args.run_id, "instances": len(telemetry),
                      "evaluation_runs": len(results), "correctness_failures": failures,
                      "immutable": immutable}))
    return 0 if immutable else 2


def _draw_plots(out: Path, results: list[dict[str, Any]], telemetry: list[dict[str, Any]],
                timeout_ms: float) -> list[str]:
    by_mode: dict[str, list[float]] = defaultdict(list)
    per_instance: dict[str, dict[str, float]] = defaultdict(dict)
    for row in results:
        if row["status"] in DEFINITE:
            by_mode[row["mode"]].append(float(row["runtime_ms"]))
        per_instance[row["instance"]][row["mode"]] = float(row["runtime_ms"])
    scatters = {}
    for name, (x_mode, y_mode) in {"lkk_vs_cadical": ("A_cadical", "E_lkk_selector"),
                                   "lkk_vs_kissat": ("B_kissat", "E_lkk_selector")}.items():
        pairs = [(v[x_mode], v[y_mode], k) for k, v in per_instance.items()
                 if x_mode in v and y_mode in v]
        scatters[name] = {"pairs": pairs, "x_name": x_mode, "y_name": y_mode}
    structural = {r["instance"] for r in telemetry
                  if r["stratum"] in ("lkk_direct_structural_unsat",
                                      "cardinality_exact_r_heavy",
                                      "matching_hall_like",
                                      "hidden_cardinality_recovered")}
    speedups = []
    for instance, modes in per_instance.items():
        if instance in structural and "E_lkk_selector" in modes and "A_cadical" in modes:
            best = min(modes.get("A_cadical", timeout_ms), modes.get("B_kissat", timeout_ms))
            if modes["E_lkk_selector"] > 0:
                speedups.append(best / modes["E_lkk_selector"])
    no_structure = {r["instance"] for r in telemetry
                    if r["stratum"] == "no_useful_detected_structure"}
    overheads = [modes["E_lkk_selector"] - modes["A_cadical"]
                 for instance, modes in per_instance.items()
                 if instance in no_structure and "E_lkk_selector" in modes
                 and "A_cadical" in modes]
    return plots.write_all(out / "plots", by_mode, scatters, speedups, overheads, timeout_ms)


if __name__ == "__main__":
    raise SystemExit(main())
