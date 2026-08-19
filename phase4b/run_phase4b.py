"""Phase 4B campaign: Part A fallback engines, then Part B LemmaBridge.

Part A and Part B are deliberately separate. Part A only changes which solver
receives the unchanged formula when LKK falls back. Part B only adds clauses
that an independent locked solver has certified as implied. Part B never runs
unless Part A finished and every correctness gate passed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark.harness import machine_metadata, sha256_file
from phase2.flow import CapacityInstance, exhaustive_box_test, flow_box_test
from phase3.common import benchmark_cnf, direct_run, solver_infos, tree_digest, write_csv
from phase3.generator import BenchmarkRequest, generate_request
from phase4a.run_phase4a import capacity_instance
from phase4b import lemmas as lemma_tools

DEFINITE = {"SATISFIABLE", "UNSATISFIABLE"}
P2 = Path("results/phase2/20260816Tphase2validation02Z")
P3 = Path("results/phase3/20260816Tphase3benchmark01Z")
P4A = Path("results/phase4a/20260816Tphase4anative04Z")
P2_DIGEST = "dba6b4579fa6ee07bd55564c796b2e70f37633028bfa571c5002dc8a7e4c56e7"
P3_DIGEST = "3efa0ca3f95a96e0a11b149470fb075882e61cc3ccb0b05e1af63a34e9f97a15"
FLAGS = "g++ -std=c++17 -O3 -DNDEBUG -Wall -Wextra; CaDiCaL: -O3 -DNDEBUG -DNCONTRACTS -DNTRACING"
NATIVE = "/opt/solvers/bin/lkk-native4b"
KISSAT = "/opt/solvers/bin/kissat"

TIMEOUT = 5.0
PAR2 = 2 * TIMEOUT * 1000
VALIDATION_TIMEOUT = 30.0
LEMMA_VERIFY_TIMEOUT = 5.0
LEMMA_VERIFY_BUDGET_SECONDS = 600.0
MAX_LEMMAS = 2000
MAX_LEMMA_LENGTH = 12
HARVEST_BUDGET = 2000000
FLOW_BUDGET = 200000

# Modes A, B and C of Part A. "lkk_none" is the structural-only mode that
# reports UNKNOWN instead of falling back.
HYBRID_MODES = {
    "lkk_cadical": ["--fallback", "cadical"],
    "lkk_kissat": ["--fallback", "kissat", "--kissat", KISSAT],
    "lkk_none": ["--fallback", "none"],
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def par2(runtime_ms: float, status: str) -> float:
    return float(runtime_ms) if status in DEFINITE else PAR2


def native_run(repo: Path, cnf: Path, sha: str, seed: int, timeout: float, log: Path,
               extra: list[str], witness: Path | None = None) -> dict[str, Any]:
    log.parent.mkdir(parents=True, exist_ok=True)
    tfile = log.with_suffix(".time")
    command = ["/usr/bin/time", "-f", "%M", "-o", str(tfile),
               "/usr/bin/timeout", "--signal=KILL", str(timeout),
               NATIVE, "--cnf", str(cnf), "--seed", str(seed), "--sha256", sha,
               "--derived-budget", "5000", "--fallback-timeout", str(timeout),
               "--kissat-log", str(log.with_suffix(".kissat.log")),
               "--kissat-tmp", str(log.with_suffix(".serialized.cnf")), *extra]
    if witness is not None:
        witness.parent.mkdir(parents=True, exist_ok=True)
        command += ["--witness", str(witness)]
    start = time.perf_counter_ns()
    with log.open("wb") as stream:
        completed = subprocess.run(command, cwd=repo, stdout=stream, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, check=False, timeout=timeout + 10)
    wall = (time.perf_counter_ns() - start) / 1e6
    timed = completed.returncode in {124, 137}
    result: dict[str, Any] = {"runtime_ms": wall, "timed_out": timed, "exit_code": completed.returncode,
                              "status": "TIMEOUT" if timed else "ERROR", "peak_rss_kb_external": None}
    if tfile.exists():
        values = [x for x in tfile.read_text().splitlines() if x.strip().isdigit()]
        result["peak_rss_kb_external"] = int(values[-1]) if values else None
    if not timed:
        lines = log.read_text(errors="replace").splitlines()
        payload = next((json.loads(x) for x in reversed(lines) if x.startswith("{")), None)
        if payload:
            result.update(payload)
            result["runtime_ms"] = wall
            result["status"] = payload["final_result"]
    result["raw_log"] = log.relative_to(repo).as_posix()
    return result


# --------------------------------------------------------------------------
# Correctness gates
# --------------------------------------------------------------------------

def flow_campaign(repo: Path, out: Path) -> tuple[int, int]:
    """5,000 fresh FlowBoxTest/exhaustive cross-checks against this binary."""
    batch = out / "logs" / "flow_batch.txt"
    batch.parent.mkdir(parents=True, exist_ok=True)
    expected = []
    lines = []
    for offset in range(5000):
        seed = 800000 + offset
        instance = capacity_instance(seed)
        exhaustive, _, _ = exhaustive_box_test(instance)
        python = flow_box_test(instance)
        lines.append(
            f"{seed}|{','.join(map(str, instance.demands))}|{','.join(map(str, instance.capacities))}|"
            + ";".join(",".join(map(str, r)) for r in instance.reachable)
        )
        expected.append((seed, instance, exhaustive, python))
    batch.write_text("\n".join(lines) + "\n")
    completed = subprocess.run([NATIVE, "--flow-batch", str(batch)], cwd=repo, text=True,
                               capture_output=True, check=True)
    (out / "logs" / "native_flow_batch.log").write_text(completed.stdout)
    native = {int(r["seed"]): r for r in csv.DictReader(completed.stdout.splitlines())}
    mismatches = 0
    for seed, instance, exhaustive, python in expected:
        row = native[seed]
        if not (exhaustive == python.result == row["flow_result"]):
            mismatches += 1
            (out / "failures" / f"flow_{seed}.json").parent.mkdir(parents=True, exist_ok=True)
            (out / "failures" / f"flow_{seed}.json").write_text(json.dumps(
                {"seed": seed, "demands": instance.demands, "capacities": instance.capacities,
                 "reachable": instance.reachable, "exhaustive": exhaustive,
                 "python_flow": python.result, "native_flow": row["flow_result"]}, indent=2) + "\n")
    return len(expected), mismatches


def correctness_campaign(repo: Path, out: Path, cases: list[dict[str, Any]],
                         solvers: list[Any]) -> tuple[list[dict[str, Any]], int]:
    """Every mode must agree with both locked solvers on every definite answer.

    This is also where a false structural refutation would surface: an instance
    the harvested sub-model calls UNSAT while the locked solvers call it SAT.
    """
    rows: list[dict[str, Any]] = []
    failures = 0
    for case in cases:
        path = Path(case["path"])
        sha = sha256_file(path)
        stem = case["stem"]
        answers: dict[str, str] = {}
        for solver in solvers:
            direct = direct_run(solver, benchmark_cnf(path, repo), 1, VALIDATION_TIMEOUT,
                                out / "logs" / "correctness" / f"{stem}__{solver.name}.log")
            answers[solver.name] = direct["status"]
        expected = case.get("expected_result", "TO_VALIDATE")
        definite = {v for v in answers.values() if v in DEFINITE}
        if expected == "TO_VALIDATE" and len(set(answers.values())) == 1 and len(definite) == 1:
            expected = next(iter(definite))
        mode_results = {}
        for mode, extra in HYBRID_MODES.items():
            run = native_run(repo, path, sha, 1, VALIDATION_TIMEOUT,
                             out / "logs" / "correctness" / f"{stem}__{mode}.log", extra,
                             out / "logs" / "witnesses" / f"{stem}__{mode}.json")
            mode_results[mode] = run
        lemma_run = native_run(repo, path, sha, 1, VALIDATION_TIMEOUT,
                               out / "logs" / "correctness" / f"{stem}__lemma_probe.log",
                               ["--fallback", "none", "--lemmas", "on", "--generate-only",
                                "--max-lemmas", str(MAX_LEMMAS),
                                "--max-lemma-length", str(MAX_LEMMA_LENGTH),
                                "--harvest-budget", str(HARVEST_BUDGET),
                                "--flow-budget", str(FLOW_BUDGET),
                                "--lemma-out", str(out / "logs" / "lemmas" / f"{stem}.json")])
        # A structural refutation from harvested families must never contradict
        # a locked solver.
        structural_unsat = bool(lemma_run.get("structural_unsat_detected"))
        structural_conflict = structural_unsat and expected == "SATISFIABLE"
        definite_answers = {mode: r["status"] for mode, r in mode_results.items()
                            if r["status"] in DEFINITE}
        disagreement = sorted({*definite_answers.values(), *definite} - {expected}) if expected in DEFINITE else []
        passed = not structural_conflict and not disagreement
        rows.append({
            "campaign": case["campaign"], "instance": path.name, "family": case.get("family", ""),
            "expected_result": expected,
            "cadical_result": answers.get("cadical"), "kissat_result": answers.get("kissat"),
            "lkk_cadical_result": mode_results["lkk_cadical"]["status"],
            "lkk_kissat_result": mode_results["lkk_kissat"]["status"],
            "lkk_none_result": mode_results["lkk_none"]["status"],
            "lkk_none_structural": mode_results["lkk_none"].get("structural_result"),
            "harvest_boxes": lemma_run.get("harvest_boxes"),
            "harvest_resources": lemma_run.get("harvest_resources"),
            "structural_unsat_detected": structural_unsat,
            "structural_refutation_conflict": structural_conflict,
            "lemma_candidates": lemma_run.get("lemma_candidates"),
            "definite_mismatch": bool(disagreement),
            "passed": passed,
        })
        write_csv(out / "correctness.csv", rows)
        if not passed:
            failures += 1
            (out / "failures").mkdir(parents=True, exist_ok=True)
            (out / "failures" / f"correctness_{stem}.json").write_text(json.dumps(
                {"row": rows[-1], "modes": {k: v for k, v in mode_results.items()},
                 "lemma_probe": lemma_run}, indent=2, default=str) + "\n")
    return rows, failures


# --------------------------------------------------------------------------
# Part A
# --------------------------------------------------------------------------

def part_a(repo: Path, out: Path, instances: list[dict[str, Any]], solvers: list[Any],
           seeds: tuple[int, ...], tag: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    variants = ["cadical", "kissat", "lkk_cadical", "lkk_kissat", "lkk_none"]
    for index, item in enumerate(instances):
        path = Path(item["path"])
        sha = item["cnf_sha256"]
        for seed in seeds:
            rotation = (index + seed) % len(variants)
            for variant in variants[rotation:] + variants[:rotation]:
                name = f"{tag}_{index + 1:03d}__{path.stem}__{variant}__seed{seed}"
                log = out / "logs" / "part_a" / f"{name}.log"
                if variant in HYBRID_MODES:
                    run = native_run(repo, path, sha, seed, TIMEOUT, log, HYBRID_MODES[variant],
                                     out / "logs" / "witnesses" / f"{name}.json")
                else:
                    run = direct_run(next(s for s in solvers if s.name == variant),
                                     benchmark_cnf(path, repo), seed, TIMEOUT, log)
                rows.append({
                    "set": tag, "instance": path.name, "family": item["family"],
                    "scale": item["scale"], "variant": item.get("variant", ""), "seed": seed,
                    "solver": variant, "runtime_ms": run["runtime_ms"], "status": run["status"],
                    "timed_out": run["timed_out"],
                    "par2_ms": par2(run["runtime_ms"], run["status"]),
                    "structural_result": run.get("structural_result", ""),
                    "fallback_used": run.get("fallback_used", ""),
                    "fallback_engine": run.get("fallback_engine", variant),
                    "fallback_ms": run.get("fallback_ms", ""),
                    "fallback_serialize_ms": run.get("fallback_serialize_ms", ""),
                    "fallback_solver_self_ms": run.get("fallback_solver_self_ms", ""),
                    "fallback_overhead_ms": run.get("fallback_overhead_ms", ""),
                    "parse_ms": run.get("parse_ms", ""), "gate_ms": run.get("gate_ms", ""),
                    "recovery_ms": run.get("recovery_ms", ""), "registry_ms": run.get("registry_ms", ""),
                    "flow_ms": run.get("flow_ms", ""), "witness_ms": run.get("witness_ms", ""),
                    "conflicts": run.get("fallback_conflicts", run.get("conflicts")),
                    "decisions": run.get("fallback_decisions", run.get("decisions")),
                    "propagations": run.get("fallback_propagations", run.get("propagations")),
                    "peak_rss_kb": run.get("peak_rss_kb_external", run.get("peak_rss_kb")),
                    "raw_log": run.get("raw_log", ""),
                })
                write_csv(out / "fallback_comparison.csv", rows)
    return rows


def phase_timings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {k: r[k] for k in ("set", "instance", "family", "scale", "seed", "solver", "parse_ms",
                           "gate_ms", "recovery_ms", "registry_ms", "flow_ms", "witness_ms",
                           "fallback_ms", "fallback_serialize_ms", "fallback_solver_self_ms",
                           "fallback_overhead_ms", "runtime_ms", "structural_result",
                           "fallback_used", "peak_rss_kb")}
        for r in rows if r["solver"] in HYBRID_MODES
    ]


def summarize_part_a(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["instance"], row["solver"])].append(row)
    order: list[tuple[str, str, str, str]] = []
    for row in rows:
        key = (row["set"], row["instance"], row["family"], str(row["scale"]))
        if key not in order:
            order.append(key)
    summary = []
    for tag, instance, family, scale in order:
        entry: dict[str, Any] = {"set": tag, "instance": instance, "family": family, "scale": scale}
        for solver in ("cadical", "kissat", "lkk_cadical", "lkk_kissat", "lkk_none"):
            runs = grouped[(instance, solver)]
            entry[f"{solver}_par2_ms"] = statistics.mean(r["par2_ms"] for r in runs) if runs else ""
            entry[f"{solver}_timeouts"] = sum(1 for r in runs if r["status"] not in DEFINITE)
        hybrid = grouped[(instance, "lkk_cadical")]
        entry["fallback_used"] = hybrid[0]["fallback_used"] if hybrid else ""
        kissat_runs = grouped[(instance, "lkk_kissat")]
        overheads = [float(r["fallback_overhead_ms"]) for r in kissat_runs
                     if str(r["fallback_overhead_ms"]) not in ("", "None")]
        serials = [float(r["fallback_serialize_ms"]) for r in kissat_runs
                   if str(r["fallback_serialize_ms"]) not in ("", "None")]
        entry["kissat_process_overhead_ms"] = statistics.mean(overheads) if overheads else ""
        entry["kissat_serialize_ms"] = statistics.mean(serials) if serials else ""
        if entry["lkk_cadical_par2_ms"] and entry["lkk_kissat_par2_ms"]:
            entry["cadical_over_kissat_fallback"] = (
                entry["lkk_cadical_par2_ms"] / entry["lkk_kissat_par2_ms"]
            )
            entry["kissat_fallback_faster"] = entry["lkk_kissat_par2_ms"] < entry["lkk_cadical_par2_ms"]
        summary.append(entry)
    return summary


# --------------------------------------------------------------------------
# Part B
# --------------------------------------------------------------------------

def generate_and_verify(repo: Path, out: Path, instances: list[dict[str, Any]],
                        kissat: Any) -> tuple[list[dict[str, Any]], dict[str, Path], int, list[dict[str, Any]]]:
    """Generate candidates, verify each independently, and write accepted files."""
    verification: list[dict[str, Any]] = []
    accepted_files: dict[str, Path] = {}
    structural: list[dict[str, Any]] = []
    refuted = 0
    work = out / "logs" / "verification"
    work.mkdir(parents=True, exist_ok=True)
    for item in instances:
        path = Path(item["path"])
        stem = path.stem
        document_path = out / "lemmas" / f"{stem}.json"
        document_path.parent.mkdir(parents=True, exist_ok=True)
        run = native_run(repo, path, item["cnf_sha256"], 1, VALIDATION_TIMEOUT,
                         out / "logs" / "lemma_generation" / f"{stem}.log",
                         ["--fallback", "none", "--lemmas", "on", "--generate-only",
                          "--max-lemmas", str(MAX_LEMMAS),
                          "--max-lemma-length", str(MAX_LEMMA_LENGTH),
                          "--harvest-budget", str(HARVEST_BUDGET),
                          "--flow-budget", str(FLOW_BUDGET),
                          "--lemma-out", str(document_path)])
        if not document_path.exists():
            continue
        document = lemma_tools.load(document_path)
        replay_start = time.perf_counter_ns()
        proven, replay_errors = lemma_tools.replay_database(path, document)
        family_supported, family_errors = lemma_tools.verify_families(document, proven)
        replay_ms = (time.perf_counter_ns() - replay_start) / 1e6
        structural_check = lemma_tools.verify_structural_unsat(document)
        structural.append({
            "instance": path.name, "family": item["family"], "scale": item["scale"],
            "generator_structural_unsat": bool(run.get("structural_unsat_detected")),
            "generator_reason": run.get("structural_unsat_reason", ""),
            "independent_result": structural_check["result"],
            "rebuilt_boxes": structural_check["rebuilt_boxes"],
            "rebuilt_resources": structural_check["rebuilt_resources"],
            "maximum_flow": structural_check.get("maximum_flow", ""),
            "total_demand": structural_check.get("total_demand", ""),
            "expected_result": item.get("expected_result", ""),
            "replay_errors": len(replay_errors), "family_errors": len(family_errors),
            "replay_ms": replay_ms,
        })
        if replay_errors or family_errors:
            (out / "failures").mkdir(parents=True, exist_ok=True)
            (out / "failures" / f"replay_{stem}.json").write_text(json.dumps(
                {"replay_errors": replay_errors[:50], "family_errors": family_errors[:50]},
                indent=2) + "\n")
        accepted: list[str] = []
        budget_start = time.perf_counter()
        for lemma in document["lemmas"]:
            clause = lemma_tools.as_clause(lemma["clause"])
            origin = lemma["origin"]
            if time.perf_counter() - budget_start > LEMMA_VERIFY_BUDGET_SECONDS:
                # Unreached candidates are never injected; they are recorded so
                # the accounting stays complete.
                verification.append({
                    "instance": path.name, "family": item["family"], "lemma_id": lemma["id"],
                    "clause": " ".join(map(str, clause)), "length": len(clause), "origin": origin,
                    "support": " ".join(map(str, lemma["support"])),
                    "structural_check": "not_reached", "structural_ok": "",
                    "implication_verdict": "BUDGET_EXHAUSTED", "implication_ms": 0,
                    "verification_solver": kissat.name, "accepted": False, "verification_log": "",
                })
                continue
            if origin == lemma_tools.CONDITIONAL_ORIGIN:
                structurally_ok, structural_reason = lemma_tools.verify_conditional(document, lemma)
            elif origin in lemma_tools.STRUCTURAL_ORIGINS:
                structurally_ok = clause in proven and clause in family_supported
                structural_reason = "family_clause_replayed" if structurally_ok else "family_not_proven"
            else:
                structurally_ok = clause in proven
                structural_reason = "resolvent_replayed" if structurally_ok else "resolvent_not_replayed"
            log = work / f"{stem}__lemma{lemma['id']:05d}.log"
            check = lemma_tools.implication_check(
                path, clause, Path(kissat.executable), kissat.args, LEMMA_VERIFY_TIMEOUT, work, log)
            ok = structurally_ok and check["verdict"] == "UNSATISFIABLE"
            if check["verdict"] == "SATISFIABLE":
                refuted += 1
                (out / "failures").mkdir(parents=True, exist_ok=True)
                (out / "failures" / f"refuted_{stem}_lemma{lemma['id']}.json").write_text(
                    json.dumps({"instance": path.name, "lemma": lemma, "check": check},
                               indent=2) + "\n")
            if ok:
                accepted.append(" ".join(map(str, clause)))
            verification.append({
                "instance": path.name, "family": item["family"], "lemma_id": lemma["id"],
                "clause": " ".join(map(str, clause)), "length": len(clause), "origin": origin,
                "support": " ".join(map(str, lemma["support"])),
                "structural_check": structural_reason, "structural_ok": structurally_ok,
                "implication_verdict": check["verdict"], "implication_ms": check["runtime_ms"],
                "verification_solver": kissat.name, "accepted": ok,
                "verification_log": check["log"],
            })
        if verification:
            write_csv(out / "lemma_verification.csv", verification)
        accepted_path = out / "lemmas" / f"{stem}.accepted.txt"
        accepted_path.write_text("\n".join(accepted) + ("\n" if accepted else ""))
        accepted_files[path.name] = accepted_path
    if structural:
        write_csv(out / "structural_refutations.csv", structural)
    return verification, accepted_files, refuted, structural


def part_b_ablation(repo: Path, out: Path, instances: list[dict[str, Any]], solvers: list[Any],
                    accepted_files: dict[str, Path], seeds: tuple[int, ...]) -> tuple[list, list]:
    rows: list[dict[str, Any]] = []
    stats: list[dict[str, Any]] = []
    for index, item in enumerate(instances):
        path = Path(item["path"])
        accepted = accepted_files.get(path.name)
        for seed in seeds:
            variants = ["cadical", "kissat", "lkk_cadical", "lkk_kissat", "lemma_cadical"]
            rotation = (index + seed) % len(variants)
            for variant in variants[rotation:] + variants[:rotation]:
                name = f"{index + 1:03d}__{path.stem}__{variant}__seed{seed}"
                log = out / "logs" / "part_b" / f"{name}.log"
                if variant == "lemma_cadical":
                    extra = ["--fallback", "cadical", "--lemmas", "on",
                             "--max-lemmas", str(MAX_LEMMAS),
                             "--max-lemma-length", str(MAX_LEMMA_LENGTH),
                             "--harvest-budget", str(HARVEST_BUDGET),
                             "--flow-budget", str(FLOW_BUDGET)]
                    if accepted is not None:
                        extra += ["--accepted-lemmas", str(accepted)]
                    run = native_run(repo, path, item["cnf_sha256"], seed, TIMEOUT, log, extra)
                elif variant in HYBRID_MODES:
                    run = native_run(repo, path, item["cnf_sha256"], seed, TIMEOUT, log,
                                     HYBRID_MODES[variant])
                else:
                    run = direct_run(next(s for s in solvers if s.name == variant),
                                     benchmark_cnf(path, repo), seed, TIMEOUT, log)
                rows.append({
                    "instance": path.name, "family": item["family"], "scale": item["scale"],
                    "seed": seed, "arm": variant, "runtime_ms": run["runtime_ms"],
                    "status": run["status"], "par2_ms": par2(run["runtime_ms"], run["status"]),
                    "lemma_generation_ms": run.get("lemma_generation_ms", ""),
                    "lemma_candidates": run.get("lemma_candidates", ""),
                    "lemmas_injected": run.get("lemmas_injected", ""),
                    "lemma_literals_injected": run.get("lemma_literals_injected", ""),
                    "fallback_ms": run.get("fallback_ms", ""),
                    "peak_rss_kb": run.get("peak_rss_kb_external", run.get("peak_rss_kb")),
                    "raw_log": run.get("raw_log", ""),
                })
                stats.append({
                    "instance": path.name, "family": item["family"], "seed": seed, "arm": variant,
                    "conflicts": run.get("fallback_conflicts", run.get("conflicts")),
                    "decisions": run.get("fallback_decisions", run.get("decisions")),
                    "propagations": run.get("fallback_propagations", run.get("propagations")),
                    "restarts": run.get("fallback_restarts", run.get("restarts")),
                    "learned": run.get("fallback_learned", ""),
                    "irredundant": run.get("fallback_irredundant", ""),
                    "redundant": run.get("fallback_redundant", ""),
                    "ticks": run.get("fallback_ticks", ""),
                    "solve_ms": run.get("fallback_ms", run["runtime_ms"]),
                    "status": run["status"],
                })
                write_csv(out / "lemma_ablation.csv", rows)
                write_csv(out / "cdcl_statistics.csv", stats)
    return rows, stats


def lemma_deltas(ablation: list[dict[str, Any]], stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-instance change of lemma_cadical against the no-lemma CaDiCaL fallback."""
    def mean(values: list[Any]) -> float | str:
        numeric = [float(v) for v in values if str(v) not in ("", "None", "-1")]
        return statistics.mean(numeric) if numeric else ""

    by_arm: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in stats:
        by_arm[(row["instance"], row["arm"])].append(row)
    par_by_arm: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in ablation:
        par_by_arm[(row["instance"], row["arm"])].append(row["par2_ms"])
    seen: list[str] = []
    for row in ablation:
        if row["instance"] not in seen:
            seen.append(row["instance"])
    deltas = []
    for instance in seen:
        base = by_arm[(instance, "lkk_cadical")]
        lemma = by_arm[(instance, "lemma_cadical")]
        if not base or not lemma:
            continue
        entry: dict[str, Any] = {"instance": instance, "family": base[0]["family"]}
        for metric in ("conflicts", "decisions", "propagations"):
            before, after = mean([r[metric] for r in base]), mean([r[metric] for r in lemma])
            entry[f"{metric}_no_lemmas"] = before
            entry[f"{metric}_with_lemmas"] = after
            entry[f"{metric}_change_pct"] = (
                (after - before) / before * 100 if before not in ("", 0) and after != "" else ""
            )
        before = statistics.mean(par_by_arm[(instance, "lkk_cadical")])
        after = statistics.mean(par_by_arm[(instance, "lemma_cadical")])
        entry["par2_no_lemmas_ms"] = before
        entry["par2_with_lemmas_ms"] = after
        entry["runtime_change_pct"] = (after - before) / before * 100 if before else ""
        injected = [r["lemmas_injected"] for r in ablation
                    if r["instance"] == instance and r["arm"] == "lemma_cadical"]
        entry["lemmas_injected"] = mean(injected)
        deltas.append(entry)
    return deltas


# --------------------------------------------------------------------------
# Benchmark sets
# --------------------------------------------------------------------------

def phase3_instances() -> list[dict[str, Any]]:
    items = []
    for row in csv.DictReader((P3 / "instances.csv").open(newline="", encoding="utf-8")):
        path = P3 / "cnfs" / row["instance"]
        if sha256_file(path) != row["cnf_sha256"]:
            raise RuntimeError(f"Phase 3 CNF hash mismatch: {path.name}")
        items.append({**row, "path": str(path), "scale": int(row["scale"])})
    return items


EXPANDED = [
    *[BenchmarkRequest("exact_r_allocation", s, 0, "phase4b") for s in (7, 9, 11, 13)],
    *[BenchmarkRequest("overlapping_hall_violation", s, 0, "phase4b") for s in (6, 8, 9, 10, 11)],
    *[BenchmarkRequest("capacity2_violation", s, 0, "phase4b") for s in (3, 4, 5, 6, 7, 8)],
    *[BenchmarkRequest("satisfiable_capacity_control", s, 0, "phase4b") for s in (8, 16, 24, 32)],
    *[BenchmarkRequest("hidden_pigeonhole", s, n, f"phase4b_noise{n}")
      for s in (7, 8, 9, 10) for n in (0, 100)],
    *[BenchmarkRequest("random_3sat", s, 0, "uniform") for s in (150, 400, 800)],
    *[BenchmarkRequest("random_3sat", s, 0, "planted_sat") for s in (150, 400, 800)],
]


def expanded_instances(out: Path) -> list[dict[str, Any]]:
    items = []
    for index, request in enumerate(EXPANDED):
        path = out / "cnfs" / f"{index + 1:03d}_{request.family}_scale{request.scale}_{request.variant}.cnf"
        info = generate_request(request, 51000 + index, path)
        items.append({**info, "path": str(path), "scale": int(info["scale"])})
    return items


# --------------------------------------------------------------------------

def report(out: Path, run_id: str, part_a_summary: list[dict[str, Any]],
           verification: list[dict[str, Any]], deltas: list[dict[str, Any]],
           structural: list[dict[str, Any]], counts: dict[str, Any]) -> None:
    from phase4b.report import render

    (out / "RESULTS.md").write_text(
        render(run_id, part_a_summary, verification, deltas, structural, counts), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--skip-flow-campaign", action="store_true")
    parser.add_argument("--limit", type=int, default=0,
                        help="plumbing check only: cap each benchmark set to N instances")
    args = parser.parse_args()
    repo = Path.cwd().resolve()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError("non-empty output directory")
    for directory in (out, out / "logs", out / "failures", out / "lemmas", out / "cnfs"):
        directory.mkdir(parents=True, exist_ok=True)

    before = {"phase1": tree_digest(Path("results/phase1/20260816Tphase1baseline04Z")),
              "phase2": tree_digest(P2), "phase3": tree_digest(P3), "phase4a": tree_digest(P4A)}
    if before["phase2"]["tree_sha256"] != P2_DIGEST or before["phase3"]["tree_sha256"] != P3_DIGEST:
        raise RuntimeError("immutable predecessor mismatch")

    config = json.loads(Path("phase3/config.json").read_text())
    solvers = solver_infos(config)
    kissat = next(s for s in solvers if s.name == "kissat")
    metadata = {
        "phase": "4B", "run_id": args.run_id, "started_utc": now(),
        "predecessors_before": before, "machine": machine_metadata(), "python": sys.version,
        "compiler_flags": FLAGS,
        "cadical_commit": "c60730422e758ef1cebe7aeddf2dda31c996bf04",
        "kissat_commit": "8af8e56f174b778aef3aa45af9f739b2a5f492c2",
        "native_binary_sha256": sha256_file(Path(NATIVE)),
        "native_image_id": os.environ.get("LKK_NATIVE_IMAGE_ID"),
        "benchmark_timeout_seconds": TIMEOUT,
        "validation_timeout_seconds": VALIDATION_TIMEOUT,
        "lemma_verification_timeout_seconds": LEMMA_VERIFY_TIMEOUT,
        "lemma_verification_budget_seconds_per_instance": LEMMA_VERIFY_BUDGET_SECONDS,
        "lemma_limits": {"max_lemmas": MAX_LEMMAS, "max_lemma_length": MAX_LEMMA_LENGTH,
                         "harvest_check_budget": HARVEST_BUDGET, "flow_call_budget": FLOW_BUDGET},
        "seeds": [1, 2, 3],
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    accepted = phase3_instances()
    expanded = expanded_instances(out)
    if args.limit:
        accepted, expanded = accepted[: args.limit], expanded[: args.limit]
        metadata["limited_to_instances"] = args.limit
    all_instances = [{**i, "campaign": "phase3", "stem": f"p3__{Path(i['path']).stem}"} for i in accepted]
    all_instances += [{**i, "campaign": "expanded", "stem": f"ex__{Path(i['path']).stem}"} for i in expanded]

    flow_cases, flow_mismatches = (0, 0)
    if not args.skip_flow_campaign:
        flow_cases, flow_mismatches = flow_campaign(repo, out)
    correctness, correctness_failures = correctness_campaign(repo, out, all_instances, solvers)
    if flow_mismatches or correctness_failures:
        metadata.update({"stopped_before_performance": True, "flow_mismatches": flow_mismatches,
                         "correctness_failures": correctness_failures, "completed_utc": now()})
        (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        return 2

    rows = part_a(repo, out, accepted, solvers, (1, 2, 3), "accepted14")
    rows += part_a(repo, out, expanded, solvers, (1,), "expanded")
    write_csv(out / "fallback_comparison.csv", rows)
    write_csv(out / "phase_timings.csv", phase_timings(rows))
    summary = summarize_part_a(rows)
    write_csv(out / "part_a_summary.csv", summary)
    write_csv(out / "boundary_sweep.csv", [r for r in rows if r["set"] == "expanded"])

    # Part B only looks at instances that actually reach the fallback, since a
    # structurally solved instance has nothing to transfer.
    eligible = [i for i in all_instances
                if any(r["instance"] == Path(i["path"]).name and r["solver"] == "lkk_cadical"
                       and str(r["fallback_used"]).lower() in ("true", "")
                       for r in rows)]
    verification, accepted_files, refuted, structural = generate_and_verify(repo, out, eligible, kissat)
    if refuted:
        metadata.update({"stopped_before_performance": True, "refuted_lemmas": refuted,
                         "completed_utc": now()})
        (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        return 3
    ablation, stats = part_b_ablation(repo, out, eligible, solvers, accepted_files, (1, 2, 3))
    deltas = lemma_deltas(ablation, stats)
    write_csv(out / "lemma_delta.csv", deltas or [{"instance": "none"}])
    if not verification:
        write_csv(out / "lemma_verification.csv",
                  [{"instance": "none", "lemma_id": "", "origin": "", "accepted": ""}])

    counts = {
        "flow_cases": flow_cases, "flow_mismatches": flow_mismatches,
        "correctness_cases": len(correctness), "correctness_failures": correctness_failures,
        "accepted_instances": len(accepted), "expanded_instances": len(expanded),
        "eligible_instances": len(eligible),
        "lemmas_generated": len(verification),
        "lemmas_accepted": sum(1 for r in verification if r["accepted"]),
        "lemmas_refuted": refuted,
        "lemmas_unverified": sum(1 for r in verification if r["implication_verdict"] != "UNSATISFIABLE"),
        "part_a_runs": len(rows), "part_b_runs": len(ablation),
    }
    report(out, args.run_id, summary, verification, deltas, structural, counts)

    after = {k: tree_digest(Path(v["root"])) for k, v in before.items()}
    immutable = all(before[k]["tree_sha256"] == after[k]["tree_sha256"] for k in before)
    metadata.update({"completed_utc": now(), "predecessors_after": after,
                     "predecessors_immutable": immutable, **counts})
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"run_id": args.run_id, **counts, "immutable": immutable}))
    return 0 if immutable else 2


if __name__ == "__main__":
    raise SystemExit(main())
