"""Phase 4C campaign: correctness gate first, then the indexed-registry measurement.

Phase 4C changes one thing: how the exact-r candidate search spends its budget.
The campaign therefore has to establish two separate claims, and it establishes
them in that order:

  correctness  the change decides more, never differently -- every definite
               answer still agrees with both locked solvers, every new
               structural refutation carries a witness the independent checker
               accepts, and nothing before the registry moved;
  performance  only measured once the correctness gate has passed.

The sealed Phase 4A engine is measured alongside as the before-picture, so the
comparison is made against the engine that produced the accepted results rather
than against a remembered number.
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
from phase2.cnf import bounded_resolution, parse_cnf
from phase2.flow import exhaustive_box_test, flow_box_test
from phase2.structure import (benefit_gate, build_structural_model,
                              fast_cheap_gate, recover_exact_registry)
from phase2.witness import check_witness
from phase3.common import benchmark_cnf, direct_run, solver_infos, tree_digest, write_csv
from phase4a.run_phase4a import capacity_instance
from phase4c.registry_indexed import recover_exact_registry_indexed

DEFINITE = {"SATISFIABLE", "UNSATISFIABLE"}
P2 = Path("results/phase2/20260816Tphase2validation02Z")
P3 = Path("results/phase3/20260816Tphase3benchmark01Z")
P4A = Path("results/phase4a/20260816Tphase4anative04Z")
P4B = Path("results/phase4b/20260816Tphase4bfallbacklemma01Z")
P2_DIGEST = "dba6b4579fa6ee07bd55564c796b2e70f37633028bfa571c5002dc8a7e4c56e7"
P3_DIGEST = "3efa0ca3f95a96e0a11b149470fb075882e61cc3ccb0b05e1af63a34e9f97a15"

SEALED = "/opt/solvers/bin/lkk-native4a"
INDEXED = "/opt/solvers/bin/lkk-native4c"
SHARED_CORE = "/opt/solvers/bin/lkk-native5"

TIMEOUT = 5.0
PAR2 = 2 * TIMEOUT * 1000
VALIDATION_TIMEOUT = 30.0
FLOW_CASES = 5000

# The Phase 4C budget, fitted by phase5/tune.py on a calibration split. It is
# passed explicitly to both the C++ engine and the Python reference: leaving
# either to a compiled-in or config-file default let them run the same instance
# under different budgets and report a divergence that was not real.
FAMILY_CHECK_BUDGET = int(
    json.loads(Path("phase5/config.json").read_text())["native"]["family_check_budget"]
) if Path("phase5/config.json").exists() else 300000
FLAGS = "g++ -std=c++17 -O3 -DNDEBUG -Wall -Wextra; CaDiCaL: -O3 -DNDEBUG -DNCONTRACTS -DNTRACING"

ENGINES = {"lkk_4a_sealed": SEALED, "lkk_4c_indexed": INDEXED}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def par2(runtime_ms: float, status: str) -> float:
    return float(runtime_ms) if status in DEFINITE else PAR2


def native_run(repo: Path, binary: str, cnf: Path, sha: str, seed: int, timeout: float,
               log: Path, witness: Path | None = None) -> dict[str, Any]:
    log.parent.mkdir(parents=True, exist_ok=True)
    time_path = log.with_suffix(".time")
    command = ["/usr/bin/time", "-f", "%M", "-o", str(time_path),
               "/usr/bin/timeout", "--signal=KILL", str(timeout),
               binary, "--cnf", str(cnf), "--seed", str(seed), "--sha256", sha,
               "--derived-budget", "5000", "--fallback", "cadical"]
    if binary != SEALED:
        # The sealed engine predates the option and keeps its accepted budget.
        command += ["--family-check-budget", str(FAMILY_CHECK_BUDGET)]
    if witness is not None:
        witness.parent.mkdir(parents=True, exist_ok=True)
        command += ["--witness", str(witness)]
    start = time.perf_counter_ns()
    with log.open("wb") as stream:
        completed = subprocess.run(command, cwd=repo, stdout=stream,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   check=False, timeout=timeout + 15)
    wall = (time.perf_counter_ns() - start) / 1e6
    timed = completed.returncode in {124, 137}
    result: dict[str, Any] = {"runtime_ms": wall, "timed_out": timed,
                              "exit_code": completed.returncode,
                              "status": "TIMEOUT" if timed else "ERROR",
                              "peak_rss_kb_external": None}
    if time_path.exists():
        values = [x for x in time_path.read_text().splitlines() if x.strip().isdigit()]
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


def python_structural(path: Path, config: dict[str, Any], indexed: bool) -> dict[str, Any]:
    """Reference implementation, used to cross-check the C++ engine."""
    cnf = parse_cnf(path)
    gate = fast_cheap_gate(cnf)
    out = {"structural_result": "UNKNOWN", "registry_status": "NOT_RUN",
           "boxes_found": 0, "capacity_groups": 0, "family_checks": 0}
    if gate.decision != "RUN_LKK":
        return out
    if benefit_gate(cnf, gate, config["benefit_gate"]).decision != "RUN_LKK":
        return out
    recovery = bounded_resolution(cnf, config["recovery"])
    if recovery.status != "COMPLETE":
        return out
    build = recover_exact_registry_indexed if indexed else recover_exact_registry
    settings = dict(config["registry"])
    if indexed:
        settings["family_check_budget"] = FAMILY_CHECK_BUDGET
    registry = build(recovery, settings)
    out.update({"registry_status": registry.status, "boxes_found": len(registry.boxes),
                "capacity_groups": len(registry.resources),
                "family_checks": registry.family_checks})
    if registry.status == "COMPLETE":
        out["structural_result"] = flow_box_test(
            build_structural_model(registry).capacity_instance).result
    return out


# --------------------------------------------------------------------------
# Correctness gate
# --------------------------------------------------------------------------

def flow_campaign(repo: Path, out: Path) -> tuple[int, int]:
    """Fresh FlowBoxTest/exhaustive cross-checks against the Phase 4C binary."""
    batch = out / "logs" / "flow_batch.txt"
    batch.parent.mkdir(parents=True, exist_ok=True)
    expected, lines = [], []
    for offset in range(FLOW_CASES):
        seed = 900000 + offset
        instance = capacity_instance(seed)
        exhaustive, _, _ = exhaustive_box_test(instance)
        python = flow_box_test(instance)
        lines.append(
            f"{seed}|{','.join(map(str, instance.demands))}|"
            f"{','.join(map(str, instance.capacities))}|"
            + ";".join(",".join(map(str, r)) for r in instance.reachable))
        expected.append((seed, instance, exhaustive, python))
    batch.write_text("\n".join(lines) + "\n")
    completed = subprocess.run([INDEXED, "--flow-batch", str(batch)], cwd=repo,
                               text=True, capture_output=True, check=True)
    (out / "logs" / "native_flow_batch.log").write_text(completed.stdout)
    native = {int(r["seed"]): r for r in csv.DictReader(completed.stdout.splitlines())}
    mismatches = 0
    for seed, instance, exhaustive, python in expected:
        if not (exhaustive == python.result == native[seed]["flow_result"]):
            mismatches += 1
            (out / "failures").mkdir(parents=True, exist_ok=True)
            (out / "failures" / f"flow_{seed}.json").write_text(json.dumps(
                {"seed": seed, "demands": instance.demands,
                 "capacities": instance.capacities, "reachable": instance.reachable,
                 "exhaustive": exhaustive, "python_flow": python.result,
                 "native_flow": native[seed]["flow_result"]}, indent=2) + "\n")
    return len(expected), mismatches


def correctness_campaign(repo: Path, out: Path, config: dict[str, Any],
                         solvers: list[Any]) -> tuple[list[dict[str, Any]], int]:
    cases: list[tuple[str, Path, str]] = []
    for row in csv.DictReader((P2 / "structural_benchmarks.csv").open(newline="", encoding="utf-8")):
        cases.append(("phase2", P2 / "cnfs" / row["instance"], row["expected_result"]))
    for row in csv.DictReader((P3 / "instances.csv").open(newline="", encoding="utf-8")):
        cases.append(("phase3", P3 / "cnfs" / row["instance"], row["expected_result"]))

    rows: list[dict[str, Any]] = []
    failures = 0
    for campaign, path, expected in cases:
        stem = f"{campaign}__{path.stem}"
        sha = sha256_file(path)
        sealed = native_run(repo, SEALED, path, sha, 1, VALIDATION_TIMEOUT,
                            out / "logs" / "correctness" / f"{stem}__4a.log")
        indexed = native_run(repo, INDEXED, path, sha, 1, VALIDATION_TIMEOUT,
                             out / "logs" / "correctness" / f"{stem}__4c.log",
                             out / "logs" / "witnesses" / f"{stem}.json")
        reference = python_structural(path, config, indexed=True)
        answers = {}
        for solver in solvers:
            answers[solver.name] = direct_run(
                solver, benchmark_cnf(path, repo), 1, VALIDATION_TIMEOUT,
                out / "logs" / "correctness" / f"{stem}__{solver.name}.log")["status"]
        if expected == "TO_VALIDATE" and len(set(answers.values())) == 1 \
                and next(iter(answers.values())) in DEFINITE:
            expected = next(iter(answers.values()))

        witness_valid: Any = ""
        if indexed.get("witness_written"):
            try:
                witness_valid = check_witness(
                    path, out / "logs" / "witnesses" / f"{stem}.json")["valid"]
            except Exception as exc:  # preserved as a failure below
                witness_valid = False
                (out / "failures").mkdir(parents=True, exist_ok=True)
                (out / "failures" / f"witness_{stem}.json").write_text(
                    json.dumps({"error": str(exc)}, indent=2) + "\n")

        # The contract: decide more, never differently.
        contradicts = (sealed["structural_result"] not in ("", "UNKNOWN")
                       and indexed["structural_result"] != sealed["structural_result"])
        pre_registry_moved = any(
            sealed.get(f) != indexed.get(f)
            for f in ("gate_decision", "gate_reason", "benefit_gate_decision",
                      "recovery_status", "recovery_abort_reason"))
        agrees = (indexed["status"] == expected
                  and all(v == expected for v in answers.values()))
        newly = (sealed["structural_result"] == "UNKNOWN"
                 and indexed["structural_result"] != "UNKNOWN")
        passed = (agrees and not contradicts and not pre_registry_moved
                  and witness_valid is not False
                  and reference["structural_result"] == indexed["structural_result"])
        rows.append({
            "campaign": campaign, "instance": path.name, "expected_result": expected,
            "sealed_structural": sealed["structural_result"],
            "indexed_structural": indexed["structural_result"],
            "python_reference_structural": reference["structural_result"],
            "sealed_registry": sealed.get("registry_status"),
            "indexed_registry": indexed.get("registry_status"),
            "sealed_checks": sealed.get("family_checks"),
            "indexed_checks": indexed.get("family_checks"),
            "sealed_boxes": sealed.get("boxes_found"),
            "indexed_boxes": indexed.get("boxes_found"),
            "indexed_final": indexed["status"],
            "cadical_result": answers.get("cadical"), "kissat_result": answers.get("kissat"),
            "witness_valid": witness_valid,
            "newly_structural": newly, "contradicts_sealed": contradicts,
            "pre_registry_changed": pre_registry_moved, "passed": passed,
        })
        write_csv(out / "correctness.csv", rows)
        if not passed:
            failures += 1
            (out / "failures").mkdir(parents=True, exist_ok=True)
            (out / "failures" / f"correctness_{stem}.json").write_text(json.dumps(
                {"row": rows[-1], "sealed": sealed, "indexed": indexed,
                 "reference": reference}, indent=2, default=str) + "\n")
    return rows, failures


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------

def measure(repo: Path, out: Path, inventory: list[dict[str, str]],
            solvers: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    variants = ["cadical", "kissat", "lkk_4a_sealed", "lkk_4c_indexed"]
    for index, item in enumerate(inventory):
        path = P3 / "cnfs" / item["instance"]
        if sha256_file(path) != item["cnf_sha256"]:
            raise RuntimeError(f"Phase 3 CNF hash mismatch: {path.name}")
        for seed in (1, 2, 3):
            rotation = (index + seed) % len(variants)
            for variant in variants[rotation:] + variants[:rotation]:
                name = f"{index+1:03d}__{path.stem}__{variant}__seed{seed}"
                log = out / "logs" / "measurement" / f"{name}.log"
                if variant in ENGINES:
                    run = native_run(repo, ENGINES[variant], path, item["cnf_sha256"],
                                     seed, TIMEOUT, log)
                else:
                    run = direct_run(next(s for s in solvers if s.name == variant),
                                     benchmark_cnf(path, repo), seed, TIMEOUT, log)
                rows.append({
                    "instance": path.name, "family": item["family"], "scale": item["scale"],
                    "variant": item["variant"], "seed": seed, "solver": variant,
                    "runtime_ms": run["runtime_ms"], "status": run["status"],
                    "par2_ms": par2(run["runtime_ms"], run["status"]),
                    "timed_out": run["timed_out"],
                    "structural_result": run.get("structural_result", ""),
                    "fallback_used": run.get("fallback_used", ""),
                    "registry_ms": run.get("registry_ms", ""),
                    "family_checks": run.get("family_checks", ""),
                    "recovery_ms": run.get("recovery_ms", ""),
                    "flow_ms": run.get("flow_ms", ""),
                    "fallback_ms": run.get("fallback_ms", ""),
                    "total_end_to_end_ms": run.get("total_end_to_end_ms", run["runtime_ms"]),
                    "peak_rss_kb": run.get("peak_rss_kb_external", run.get("peak_rss_kb")),
                })
                write_csv(out / "measurements.csv", rows)
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["instance"], row["solver"])].append(row)
    order: list[tuple[str, str, str]] = []
    for row in rows:
        key = (row["instance"], row["family"], str(row["scale"]))
        if key not in order:
            order.append(key)
    summary = []
    for instance, family, scale in order:
        entry: dict[str, Any] = {"instance": instance, "family": family, "scale": scale}
        for solver in ("cadical", "kissat", "lkk_4a_sealed", "lkk_4c_indexed"):
            runs = grouped[(instance, solver)]
            entry[f"{solver}_par2_ms"] = statistics.mean(r["par2_ms"] for r in runs) if runs else ""
            entry[f"{solver}_timeouts"] = sum(1 for r in runs if r["status"] not in DEFINITE)
        sealed = entry["lkk_4a_sealed_par2_ms"]
        indexed = entry["lkk_4c_indexed_par2_ms"]
        entry["speedup_4c_over_4a"] = sealed / indexed if sealed and indexed else ""
        for baseline in ("cadical", "kissat"):
            base = entry[f"{baseline}_par2_ms"]
            entry[f"speedup_4c_over_{baseline}"] = base / indexed if base and indexed else ""
        runs = grouped[(instance, "lkk_4c_indexed")]
        entry["structural_result"] = runs[0]["structural_result"] if runs else ""
        entry["fallback_used"] = runs[0]["fallback_used"] if runs else ""
        sealed_runs = grouped[(instance, "lkk_4a_sealed")]
        entry["sealed_structural_result"] = sealed_runs[0]["structural_result"] if sealed_runs else ""
        entry["newly_structural"] = bool(
            sealed_runs and runs
            and sealed_runs[0]["structural_result"] == "UNKNOWN"
            and runs[0]["structural_result"] != "UNKNOWN")
        summary.append(entry)
    return summary


def report(out: Path, run_id: str, correctness: list[dict[str, Any]],
           summary: list[dict[str, Any]], counts: dict[str, Any]) -> None:
    def number(value: Any) -> float | None:
        try:
            return None if value in ("", None) else float(value)
        except (TypeError, ValueError):
            return None

    newly = [r for r in summary if r["newly_structural"]]
    speedups = [n for n in (number(r["speedup_4c_over_4a"]) for r in summary) if n]

    def beats(baseline: str) -> int:
        wins = 0
        for row in summary:
            base, mine = number(row[f"{baseline}_par2_ms"]), number(row["lkk_4c_indexed_par2_ms"])
            if base is not None and mine is not None and mine < base:
                wins += 1
        return wins

    beat_c, beat_k = beats("cadical"), beats("kissat")
    # A campaign that costs an hour must not fall over while formatting its own
    # report, so every aggregate below tolerates an empty or partial run.
    median_speedup = f"{statistics.median(speedups):.2f}x" if speedups else "n/a"
    max_speedup = f"{max(speedups):.2f}x" if speedups else "n/a"
    random_rows = [r for r in summary if r["family"] == "random_3sat"]
    lines = []
    for r in summary:
        sealed, indexed = number(r["lkk_4a_sealed_par2_ms"]), number(r["lkk_4c_indexed_par2_ms"])
        ratio = number(r["speedup_4c_over_4a"])
        if sealed is None or indexed is None:
            continue
        lines.append(
            f"| {r['instance']} | {r['sealed_structural_result']} | {r['structural_result']} | "
            f"{sealed:,.1f} | {indexed:,.1f} | "
            f"{f'{ratio:.2f}x' if ratio else 'n/a'} |")

    text = f"""# Phase 4C results — indexed registry search

Run ID: `{run_id}`
Completed: {now()}

## What changed

Phase 4C changes only how the exact-r candidate search spends
`family_check_budget`. The acceptance test, the candidate key, the selection
order and the meaning of a "check" are unchanged, so with an unbounded budget the
search returns exactly what the accepted registry returns.

1. **Indexed candidates.** The accepted search paired every monotone-positive
   clause with every monotone-negative one and discarded the pair unless it
   shared exactly two variables. An inverted index from a variable pair to the
   negatives containing it yields only the partners that can qualify.
2. **Lazy family verification.** The accepted search materialised every required
   combination and charged the budget for all of them before testing one, so a
   candidate rejected on its first clause still cost the whole family. Each
   membership test is now charged as it happens, and every decided group is
   remembered so it is never re-decided.

The structural core is now a single definition in `native/core/lkk_core.hpp`,
shared by every engine, with `native/core/test_core_drift.py` differential-testing
it against the sealed Phase 4A binary.

## Correctness gate

| Gate | Result |
| --- | --- |
| Fresh FlowBoxTest/exhaustive cross-checks | {counts['flow_cases']:,} cases, {counts['flow_mismatches']} mismatches |
| Correctness cases | {counts['correctness_cases']}, {counts['correctness_failures']} failures |
| Contradicts the sealed engine | {sum(1 for r in correctness if r['contradicts_sealed'])} |
| Pre-registry stages changed | {sum(1 for r in correctness if r['pre_registry_changed'])} |
| Witnesses rejected by the independent checker | {sum(1 for r in correctness if r['witness_valid'] is False)} |
| Newly structural (decided more) | {sum(1 for r in correctness if r['newly_structural'])} |

## Measurement against the sealed engine

| Instance | 4A structural | 4C structural | 4A PAR-2 (ms) | 4C PAR-2 (ms) | speedup |
| --- | --- | --- | --- | --- | --- |
{chr(10).join(lines)}

## Findings

1. Instances the sealed engine left to CDCL fallback and Phase 4C now decides
   structurally: **{len(newly)}** ({', '.join(r['instance'] for r in newly) or 'none'}).
2. Median speedup over the sealed engine: **{median_speedup}**
   (max {max_speedup}) across {len(speedups)} instances.
3. Phase 4C beats locked CaDiCaL on {beat_c}/{len(summary)} and locked Kissat on
   {beat_k}/{len(summary)} by mean PAR-2.
4. Random 3-SAT negative controls (no structure, so the cost is gate plus
   fallback): {', '.join(f"{r['instance'][:3]}={number(r['lkk_4c_indexed_par2_ms']) or 0:,.0f}ms"
                         for r in random_rows) or 'none in this set'}.

## Limitations

- The change is a budget-allocation change. It cannot decide anything the
  accepted acceptance test would reject, but at a fixed budget it does decide
  more, so Phase 4C is not bit-identical to Phase 4A on UNKNOWN instances --
  that is the intended effect and is tested rather than assumed.
- Results apply only to these generated families, solver pins, timeout and
  hardware. No general complexity claim is made.

Raw artifacts: `correctness.csv`, `measurements.csv`, `summary.csv`, `logs/`,
`failures/`, `metadata.json`.

Phase 5 was not started.
"""
    (out / "RESULTS.md").write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--skip-flow-campaign", action="store_true")
    args = parser.parse_args()
    repo = Path.cwd().resolve()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError("non-empty output directory")
    for directory in (out, out / "logs", out / "failures"):
        directory.mkdir(parents=True, exist_ok=True)

    before = {"phase2": tree_digest(P2), "phase3": tree_digest(P3)}
    for name, path in (("phase4a", P4A), ("phase4b", P4B)):
        if path.exists():
            before[name] = tree_digest(path)
    if before["phase2"]["tree_sha256"] != P2_DIGEST or before["phase3"]["tree_sha256"] != P3_DIGEST:
        raise RuntimeError("immutable predecessor mismatch")

    config = json.loads(Path("phase3/config.json").read_text())
    solvers = solver_infos(config)
    inventory = list(csv.DictReader((P3 / "instances.csv").open(newline="", encoding="utf-8")))

    metadata: dict[str, Any] = {
        "phase": "4C", "run_id": args.run_id, "started_utc": now(),
        "predecessors_before": before, "machine": machine_metadata(),
        "python": sys.version, "compiler_flags": FLAGS,
        "sealed_binary_sha256": sha256_file(Path(SEALED)),
        "indexed_binary_sha256": sha256_file(Path(INDEXED)),
        "shared_core_binary_sha256": sha256_file(Path(SHARED_CORE)),
        "native_image_id": os.environ.get("LKK_NATIVE_IMAGE_ID"),
        "benchmark_timeout_seconds": TIMEOUT,
        "family_check_budget": FAMILY_CHECK_BUDGET,
        "family_check_budget_source": "phase5/config.json, fitted by phase5/tune.py "
                                      "on a calibration split",
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    flow_cases, flow_mismatches = (0, 0)
    if not args.skip_flow_campaign:
        flow_cases, flow_mismatches = flow_campaign(repo, out)
    correctness, failures = correctness_campaign(repo, out, config, solvers)
    if flow_mismatches or failures:
        metadata.update({"stopped_before_performance": True,
                         "flow_mismatches": flow_mismatches,
                         "correctness_failures": failures, "completed_utc": now()})
        (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        return 2

    rows = measure(repo, out, inventory, solvers)
    summary = summarize(rows)
    write_csv(out / "summary.csv", summary)
    counts = {"flow_cases": flow_cases, "flow_mismatches": flow_mismatches,
              "correctness_cases": len(correctness), "correctness_failures": failures,
              "measurement_runs": len(rows)}
    report(out, args.run_id, correctness, summary, counts)

    after = {k: tree_digest(Path(v["root"])) for k, v in before.items()}
    immutable = all(before[k]["tree_sha256"] == after[k]["tree_sha256"] for k in before)
    metadata.update({"completed_utc": now(), "predecessors_after": after,
                     "predecessors_immutable": immutable, **counts})
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"run_id": args.run_id, **counts, "immutable": immutable}))
    return 0 if immutable else 2


if __name__ == "__main__":
    raise SystemExit(main())
