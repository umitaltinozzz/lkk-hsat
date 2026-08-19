from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark.harness import machine_metadata, sha256_file
from phase2.flow import CapacityInstance, exhaustive_box_test, flow_box_test
from phase3.common import (
    benchmark_cnf,
    direct_run,
    hybrid_run,
    solver_infos,
    tree_digest,
    write_csv,
)
from phase3.generator import BenchmarkRequest, generate_request


DEFINITE = {"SATISFIABLE", "UNSATISFIABLE"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def selected_requests(config: dict[str, Any]) -> list[BenchmarkRequest]:
    return [
        BenchmarkRequest(
            item["family"],
            int(item["scale"]),
            int(item.get("noise_percent", 0)),
            item.get("variant", "default"),
        )
        for item in config["selected_instances"]
    ]


def random_capacity_instance(seed: int) -> CapacityInstance:
    rng = random.Random(seed)
    resources = rng.randint(1, 7)
    boxes = rng.randint(1, 7)
    reachable = []
    demands = []
    for _ in range(boxes):
        degree = rng.randint(1, resources)
        neighbors = tuple(sorted(rng.sample(range(resources), degree)))
        reachable.append(neighbors)
        demands.append(rng.randint(1, min(3, degree)))
    capacities = tuple(rng.randint(1, 3) for _ in range(resources))
    return CapacityInstance(tuple(demands), capacities, tuple(reachable))


def correctness_safeguard(output: Path, count: int = 1000) -> tuple[list[dict[str, Any]], int]:
    rows = []
    mismatches = 0
    for offset in range(count):
        seed = 500000 + offset
        instance = random_capacity_instance(seed)
        exhaustive, subset, exhaustive_ms = exhaustive_box_test(instance)
        flow = flow_box_test(instance)
        mismatch = exhaustive != flow.result
        row = {
            "seed": seed,
            "boxes": len(instance.demands),
            "resources": len(instance.capacities),
            "demands": json.dumps(instance.demands, separators=(",", ":")),
            "capacities": json.dumps(instance.capacities, separators=(",", ":")),
            "reachable": json.dumps(instance.reachable, separators=(",", ":")),
            "exhaustive_result": exhaustive,
            "flow_result": flow.result,
            "exhaustive_subset": json.dumps(subset, separators=(",", ":")),
            "exhaustive_ms": f"{exhaustive_ms:.9f}",
            "flow_ms": f"{flow.elapsed_ms:.9f}",
            "mismatch": mismatch,
        }
        rows.append(row)
        if mismatch:
            mismatches += 1
            (output / "failures" / f"flow_oracle_seed_{seed}.json").write_text(
                json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        if (offset + 1) % 100 == 0:
            write_csv(output / "correctness_safeguard.csv", rows)
    return rows, mismatches


def validation_pass(
    repo: Path,
    output: Path,
    config: dict[str, Any],
    config_path: Path,
    inventory: list[dict[str, Any]],
    solvers: list[Any],
) -> tuple[list[dict[str, Any]], dict[str, str], int]:
    rows = []
    validated: dict[str, str] = {}
    failures = 0
    timeout = float(config["validation_timeout_seconds"])
    for item in inventory:
        cnf_path = output / "cnfs" / item["instance"]
        cnf = benchmark_cnf(cnf_path, repo)
        answers: dict[str, str] = {}
        details: dict[str, Any] = {}
        for solver in solvers:
            log = output / "logs" / "validation" / f"{cnf_path.stem}__{solver.name}.log"
            result = direct_run(solver, cnf, 1, timeout, log)
            answers[solver.name] = result["status"]
            details[f"{solver.name}_status"] = result["status"]
            details[f"{solver.name}_ms"] = f"{result['runtime_ms']:.6f}"
            details[f"{solver.name}_timed_out"] = result["timed_out"]
            details[f"{solver.name}_log"] = log.relative_to(repo).as_posix()
        hybrid = hybrid_run(
            repo,
            config,
            config_path,
            cnf_path,
            1,
            timeout,
            f"validation__{cnf_path.stem}",
            output / "logs" / "validation_hybrid",
        )
        answers["lkk_hybrid"] = hybrid["status"]
        details["lkk_hybrid_status"] = hybrid["status"]
        details["lkk_hybrid_ms"] = f"{hybrid['runtime_ms']:.6f}"
        expected = str(item["expected_result"])
        direct_values = {answers["cadical"], answers["kissat"]}
        if expected == "TO_VALIDATE" and len(direct_values) == 1 and direct_values <= DEFINITE:
            expected = next(iter(direct_values))
            item["expected_result"] = expected
        passed = all(answer == expected for answer in answers.values()) and expected in DEFINITE
        row = {
            "instance": item["instance"],
            "family": item["family"],
            "scale": item["scale"],
            "variant": item["variant"],
            "expected_result": expected,
            **details,
            "passed": passed,
        }
        rows.append(row)
        write_csv(output / "correctness.csv", rows)
        if passed:
            validated[item["instance"]] = expected
        else:
            failures += 1
            (output / "failures" / f"validation_{cnf_path.stem}.json").write_text(
                json.dumps({"row": row, "answers": answers}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    return rows, validated, failures


def measurement_campaign(
    repo: Path,
    output: Path,
    config: dict[str, Any],
    config_path: Path,
    inventory: list[dict[str, Any]],
    solvers: list[Any],
    validated: dict[str, str],
) -> tuple[list[dict[str, Any]], int]:
    rows = []
    mismatches = 0
    timeout = float(config["benchmark_timeout_seconds"])
    seeds = [int(seed) for seed in config["seeds"]]
    repetitions = int(config["repetitions"])
    variants = [solver.name for solver in solvers] + ["lkk_hybrid"]
    order = 0
    for instance_index, item in enumerate(inventory):
        cnf_path = output / "cnfs" / item["instance"]
        cnf = benchmark_cnf(cnf_path, repo)
        for repetition in range(1, repetitions + 1):
            for seed_index, seed in enumerate(seeds):
                rotation = (instance_index + repetition + seed_index) % len(variants)
                ordered = variants[rotation:] + variants[:rotation]
                for variant in ordered:
                    order += 1
                    run_name = (
                        f"{order:05d}__{cnf_path.stem}__{variant}__seed{seed}__rep{repetition}"
                    )
                    if variant == "lkk_hybrid":
                        result = hybrid_run(
                            repo,
                            config,
                            config_path,
                            cnf_path,
                            seed,
                            timeout,
                            run_name,
                            output / "logs" / "measurements",
                        )
                    else:
                        solver = next(solver for solver in solvers if solver.name == variant)
                        log = output / "logs" / "measurements" / "direct" / f"{run_name}.log"
                        result = direct_run(solver, cnf, seed, timeout, log)
                    raw_log = Path(str(result["raw_log"]))
                    try:
                        raw_log_value = raw_log.relative_to(repo).as_posix()
                    except ValueError:
                        raw_log_value = str(raw_log)
                    result["raw_log"] = raw_log_value
                    row = {
                        "run_order": order,
                        "instance": item["instance"],
                        "family": item["family"],
                        "scale": item["scale"],
                        "variant": item["variant"],
                        "noise_percent": item["noise_percent"],
                        "variables": item["variables"],
                        "clauses": item["clauses"],
                        "cnf_sha256": item["cnf_sha256"],
                        "expected_result": validated[item["instance"]],
                        "repetition": repetition,
                        "timeout_seconds": timeout,
                        **result,
                    }
                    rows.append(row)
                    write_csv(output / "measurements.csv", rows)
                    if result["status"] in DEFINITE and result["status"] != validated[item["instance"]]:
                        mismatches += 1
                        (output / "failures" / f"measurement_{run_name}.json").write_text(
                            json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                        )
                    elif result["status"] not in DEFINITE | {"TIMEOUT"}:
                        mismatches += 1
                        (output / "failures" / f"measurement_{run_name}.json").write_text(
                            json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                        )
    return rows, mismatches


def summarize_measurements(
    rows: list[dict[str, Any]], timeout_seconds: float
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    instance_info: dict[str, dict[str, Any]] = {}
    for row in rows:
        groups[(row["instance"], row["solver"])].append(row)
        instance_info[row["instance"]] = row
    summaries = []
    timeout_ms = timeout_seconds * 1000
    for (instance, solver), group in sorted(groups.items()):
        solved = [float(row["runtime_ms"]) for row in group if row["status"] in DEFINITE]
        penalties = [
            float(row["runtime_ms"]) if row["status"] in DEFINITE else 2 * timeout_ms
            for row in group
        ]
        info = instance_info[instance]
        summaries.append(
            {
                "instance": instance,
                "family": info["family"],
                "scale": info["scale"],
                "variant": info["variant"],
                "solver": solver,
                "runs": len(group),
                "solved": len(solved),
                "timeouts": sum(row["status"] == "TIMEOUT" for row in group),
                "median_solved_ms": f"{statistics.median(solved):.6f}" if solved else "",
                "median_wall_ms": f"{statistics.median(float(row['runtime_ms']) for row in group):.6f}",
                "par2_ms": f"{statistics.mean(penalties):.6f}",
                "max_peak_rss_kb": max(
                    (int(row["peak_rss_kb"]) for row in group if row.get("peak_rss_kb") not in (None, "")),
                    default=None,
                ),
            }
        )
    par2 = {(row["instance"], row["solver"]): float(row["par2_ms"]) for row in summaries}
    for row in summaries:
        if row["solver"] == "lkk_hybrid":
            row["cadical_par2_over_lkk"] = f"{par2[(row['instance'], 'cadical')] / float(row['par2_ms']):.6f}"
            row["kissat_par2_over_lkk"] = f"{par2[(row['instance'], 'kissat')] / float(row['par2_ms']):.6f}"
        else:
            row["cadical_par2_over_lkk"] = ""
            row["kissat_par2_over_lkk"] = ""
    return summaries


def summarize_lkk_phases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["solver"] == "lkk_hybrid":
            groups[row["instance"]].append(row)
    phase_fields = (
        "gate_ms",
        "benefit_gate_ms",
        "recovery_ms",
        "registry_ms",
        "flow_ms",
        "fallback_cdcl_ms",
        "total_lkk_hybrid_ms",
        "total_lkk_end_to_end_ms",
    )
    summaries = []
    for instance, group in sorted(groups.items()):
        first = group[0]
        summary: dict[str, Any] = {
            "instance": instance,
            "family": first["family"],
            "scale": first["scale"],
            "variant": first["variant"],
            "runs": len(group),
            "completed_runs": sum(row["status"] in DEFINITE for row in group),
            "timeout_runs": sum(row["status"] == "TIMEOUT" for row in group),
            "fallback_runs": sum(row.get("fallback_used") is True for row in group),
        }
        for field in phase_fields:
            values = [
                float(row[field])
                for row in group
                if row.get(field) not in (None, "")
            ]
            summary[f"median_{field}"] = (
                f"{statistics.median(values):.6f}" if values else ""
            )
        summaries.append(summary)
    return summaries


def report_markdown(
    output: Path,
    run_id: str,
    config: dict[str, Any],
    summaries: list[dict[str, Any]],
    phase_summaries: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    validation_failures: int,
    measurement_mismatches: int,
    safeguard_mismatches: int,
    phase2_digest: dict[str, Any],
) -> None:
    by_key = {(row["instance"], row["solver"]): row for row in summaries}
    instances = sorted({row["instance"] for row in summaries})
    table = [
        "| Instance | CaDiCaL solved/PAR-2 ms | Kissat solved/PAR-2 ms | LKK solved/PAR-2 ms | CaDiCaL/LKK | Kissat/LKK |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    lkk_wins_both = 0
    for instance in instances:
        cad = by_key[(instance, "cadical")]
        kis = by_key[(instance, "kissat")]
        lkk = by_key[(instance, "lkk_hybrid")]
        cad_ratio = float(lkk["cadical_par2_over_lkk"])
        kis_ratio = float(lkk["kissat_par2_over_lkk"])
        if cad_ratio > 1 and kis_ratio > 1:
            lkk_wins_both += 1
        table.append(
            f"| {instance} | {cad['solved']}/{cad['runs']} / {float(cad['par2_ms']):.1f} | "
            f"{kis['solved']}/{kis['runs']} / {float(kis['par2_ms']):.1f} | "
            f"{lkk['solved']}/{lkk['runs']} / {float(lkk['par2_ms']):.1f} | "
            f"{cad_ratio:.2f}x | {kis_ratio:.2f}x |"
        )
    lkk_rows = [row for row in measurements if row["solver"] == "lkk_hybrid"]
    gate_counts = Counter(row.get("gate_decision", "TIMEOUT") for row in lkk_rows)
    structural_counts = Counter(row.get("structural_result", "UNKNOWN") for row in lkk_rows)
    fallback_count = sum(row.get("fallback_used") is True for row in lkk_rows)
    baseline_timeouts = Counter(
        row["solver"] for row in measurements if row["solver"] != "lkk_hybrid" and row["status"] == "TIMEOUT"
    )
    random_rows = [row for row in summaries if row["family"] == "random_3sat" and row["solver"] == "lkk_hybrid"]
    random_text = ", ".join(
        f"{row['instance']}: CaDiCaL/LKK PAR-2={row['cadical_par2_over_lkk']}x"
        for row in random_rows
    )
    phase_table = [
        "| Instance | done/timeout | gate | benefit | recovery | registry | flow | fallback | internal total | process total |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in phase_summaries:
        def show(field: str) -> str:
            value = row.get(field, "")
            return f"{float(value):.1f}" if value not in (None, "") else "n/a"

        phase_table.append(
            f"| {row['instance']} | {row['completed_runs']}/{row['timeout_runs']} | "
            f"{show('median_gate_ms')} | {show('median_benefit_gate_ms')} | "
            f"{show('median_recovery_ms')} | "
            f"{show('median_registry_ms')} | {show('median_flow_ms')} | "
            f"{show('median_fallback_cdcl_ms')} | {show('median_total_lkk_hybrid_ms')} | "
            f"{show('median_total_lkk_end_to_end_ms')} |"
        )
    report = f"""# Phase 3 results - locked-solver performance campaign

Run ID: `{run_id}`  
Completed: {utc_now()}

## Scope and methodology

This phase benchmarks the unchanged Phase 2 LKK structural engine against locked CaDiCaL 3.0.1 and Kissat 4.0.4. It does not implement LemmaBridge. A preserved calibration sweep selected boundary scales; calibration timings are not mixed into the final results.

Every final CNF first passed an unmeasured 30-second agreement run with CaDiCaL, Kissat, and LKK-hybrid. The measured campaign then used seeds {config['seeds']}, {config['repetitions']} repetition per seed, deterministic solver-order rotation, and a {config['benchmark_timeout_seconds']}-second timeout. PAR-2 assigns twice the timeout to an unsolved run.

The primary LKK runtime is process-level `runtime_ms` / `total_lkk_end_to_end_ms`. It includes Python startup, CNF parsing, FastCheapGate, BenefitGate, bounded recovery, registry construction, FlowBoxTest, witness generation/checking, and any CDCL fallback. Internal phase times are reported separately, not substituted for the full total.

## Exact commands

```powershell
python -m unittest benchmark.test_harness phase2.test_phase2 phase3.test_phase3 -v
powershell -ExecutionPolicy Bypass -File .\\scripts\\calibrate_phase3.ps1 -RunId 20260816Tphase3calibration02Z
powershell -ExecutionPolicy Bypass -File .\\scripts\\run_phase3.ps1 -RunId {run_id}
```

## Correctness safeguards

- Final benchmark CNFs: {len(instances)}
- Pre-measurement LKK/CaDiCaL/Kissat validation failures: {validation_failures}
- New randomized FlowBoxTest/exhaustive checks: 1000
- Flow/exhaustive mismatches: {safeguard_mismatches}
- Definite measured-answer mismatches/errors: {measurement_mismatches}
- Phase 2 immutable tree: {phase2_digest['file_count']} files, `{phase2_digest['tree_sha256']}`

## Performance results

Ratios above 1.0 mean the LKK PAR-2 value was lower. These results apply only to this generated campaign and hardware.

{chr(10).join(table)}

- Instances where LKK had lower PAR-2 than both locked solvers: {lkk_wins_both}/{len(instances)}.
- Baseline timeouts: {dict(baseline_timeouts)}.
- FastCheapGate measured decisions: {dict(gate_counts)}.
- Structural outcomes: {dict(structural_counts)}.
- LKK fallback runs: {fallback_count}/{len(lkk_rows)}.

## LKK end-to-end phase costs

All values below are medians in milliseconds. `process total` is the primary full cost. `n/a` means the process hit the outer timeout before writing final phase telemetry; its 5-second timeout is still included in `measurements.csv` and PAR-2.

{chr(10).join(phase_table)}

## Random 3-SAT negative controls

{random_text}

Random 3-SAT is a negative control, not a target family. Gate/recovery/registry behavior and any fallback overhead are retained in `measurements.csv`; no random-SAT advantage is assumed.

## Raw outputs

- `measurements.csv`: every interleaved raw timing, status, RSS, statistics, and LKK phase breakdown.
- `performance_summary.csv`: solved counts, timeout counts, medians, PAR-2, RSS, and relative ratios.
- `lkk_phase_summary.csv`: median full process cost and each internal LKK phase.
- `correctness.csv`: unmeasured pre-timing agreement checks.
- `correctness_safeguard.csv`: 1,000 new small flow/exhaustive comparisons.
- `instances.csv`: generated parameters and CNF hashes.
- `logs/`: raw solver, hybrid, witness, timeout-memory, and effective-config records.
- `failures/`: preserved correctness or measurement counterexamples, if present.
- `metadata.json`: exact binaries, machine, configuration, calibration reference, and immutable-tree hashes.

## Limitations and negative results

- This is a controlled generated-family study, not a claim of general SAT superiority.
- Process launch overhead is material for very short runs and is included for all variants.
- Timeouts make PAR-2 dependent on the declared 5-second cap; solved-only medians are also retained.
- The Phase 2 registry remains conservative and budgeted. A missed structure or budget rejection correctly falls back to CaDiCaL and may add overhead.
- No clauses or lemmas are injected into CDCL. This remains LKK-or-fallback, not LemmaBridge.
- No novelty, P=NP, or general polynomial-SAT claim is made.

## Next experiment

The next phase may investigate CaDiCaL API integration and only sound, independently verified constraint transfer. It should not begin until these Phase 3 results are reviewed. No LemmaBridge work was performed here.
"""
    (output / "RESULTS.md").write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run final Phase 3 performance campaign")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    repo = Path.cwd().resolve()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"output exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "logs").mkdir()
    (output / "failures").mkdir()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    (output / "config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    phase2 = repo / config["immutable_phase2_relative_path"]
    phase2_before = tree_digest(phase2)
    if phase2_before["tree_sha256"] != config["immutable_phase2_tree_sha256"]:
        raise RuntimeError("immutable Phase 2 digest mismatch before Phase 3")
    solvers = solver_infos(config)
    metadata = {
        "phase": 3,
        "run_id": args.run_id,
        "started_utc": utc_now(),
        "config_sha256": sha256_file(args.config),
        "phase2_before": phase2_before,
        "machine": machine_metadata(),
        "docker_image_id": os.environ.get("LKK_DOCKER_IMAGE_ID"),
        "docker_server_version": os.environ.get("LKK_DOCKER_SERVER_VERSION"),
        "solvers": [
            {"name": solver.name, "version": solver.version, "binary_sha256": solver.binary_sha256}
            for solver in solvers
        ],
        "calibration_run": "results/phase3/20260816Tphase3calibration02Z",
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    inventory = []
    for index, request in enumerate(selected_requests(config), 1):
        stem = f"{index:03d}_{request.family}_{request.variant}_scale{request.scale}_noise{request.noise_percent}"
        row = generate_request(
            request,
            int(config["generator_seed"]) + 1000 + index,
            output / "cnfs" / f"{stem}.cnf",
        )
        inventory.append(row)
    write_csv(output / "instances.csv", inventory)

    safeguard, safeguard_mismatches = correctness_safeguard(output)
    write_csv(output / "correctness_safeguard.csv", safeguard)
    correctness, validated, validation_failures = validation_pass(
        repo, output, config, args.config, inventory, solvers
    )
    if validation_failures:
        metadata.update({
            "completed_utc": utc_now(),
            "validation_failures": validation_failures,
            "measurement_started": False,
        })
        (output / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps({"validation_failures": validation_failures, "measurement_started": False}))
        return 2

    measurements, measurement_mismatches = measurement_campaign(
        repo, output, config, args.config, inventory, solvers, validated
    )
    summaries = summarize_measurements(measurements, float(config["benchmark_timeout_seconds"]))
    write_csv(output / "performance_summary.csv", summaries)
    phase_summaries = summarize_lkk_phases(measurements)
    write_csv(output / "lkk_phase_summary.csv", phase_summaries)
    phase2_after = tree_digest(phase2)
    if phase2_after != phase2_before:
        raise RuntimeError("Phase 2 changed during Phase 3")
    metadata.update(
        {
            "completed_utc": utc_now(),
            "phase2_after": phase2_after,
            "phase2_immutable": True,
            "instances": len(inventory),
            "validation_failures": validation_failures,
            "correctness_safeguard_instances": len(safeguard),
            "correctness_safeguard_mismatches": safeguard_mismatches,
            "measurement_runs": len(measurements),
            "measurement_mismatches": measurement_mismatches,
            "measurement_timeouts": sum(row["status"] == "TIMEOUT" for row in measurements),
        }
    )
    (output / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_markdown(
        output,
        args.run_id,
        config,
        summaries,
        phase_summaries,
        measurements,
        validation_failures,
        measurement_mismatches,
        safeguard_mismatches,
        phase2_after,
    )
    result = {
        "run_id": args.run_id,
        "instances": len(inventory),
        "measurement_runs": len(measurements),
        "validation_failures": validation_failures,
        "safeguard_mismatches": safeguard_mismatches,
        "measurement_mismatches": measurement_mismatches,
        "timeouts": metadata["measurement_timeouts"],
        "phase2_immutable": True,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if not (validation_failures or safeguard_mismatches or measurement_mismatches) else 2


if __name__ == "__main__":
    raise SystemExit(main())
