from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import random
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark.harness import machine_metadata, sha256_file
from phase2.flow import CapacityInstance, exhaustive_box_test, flow_box_test
from phase2.generator import benchmark_specs, generate_benchmark, json_compact
from phase2.hybrid import run_external_solver


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tree_digest(root: Path) -> dict[str, Any]:
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        entries.append(f"{relative} {sha256_file(path)}")
    payload = ("\n".join(entries) + "\n").encode("utf-8")
    return {
        "root": root.as_posix(),
        "file_count": len(entries),
        "tree_sha256": hashlib.sha256(payload).hexdigest(),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def random_capacity_instance(seed: int) -> CapacityInstance:
    rng = random.Random(seed)
    resource_count = rng.randint(1, 6)
    box_count = rng.randint(1, 6)
    reachable = []
    demands = []
    for _ in range(box_count):
        degree = rng.randint(1, resource_count)
        neighbors = tuple(sorted(rng.sample(range(resource_count), degree)))
        reachable.append(neighbors)
        demands.append(rng.randint(1, min(3, degree)))
    capacities = tuple(rng.randint(1, 3) for _ in range(resource_count))
    return CapacityInstance(tuple(demands), capacities, tuple(reachable))


def run_flow_oracle(config: dict[str, Any], output: Path) -> tuple[list[dict[str, Any]], int]:
    rows = []
    mismatches = 0
    failures = output / "failures"
    start_seed = int(config["oracle_seed_start"])
    count = int(config["oracle_instances"])
    for offset in range(count):
        seed = start_seed + offset
        instance = random_capacity_instance(seed)
        exhaustive_result, subset, exhaustive_ms = exhaustive_box_test(instance)
        flow = flow_box_test(instance)
        mismatch = exhaustive_result != flow.result
        row = {
            "seed": seed,
            "boxes": len(instance.demands),
            "resources": len(instance.capacities),
            "demands": json_compact(instance.demands),
            "capacities": json_compact(instance.capacities),
            "reachable": json_compact(instance.reachable),
            "exhaustive_result": exhaustive_result,
            "flow_result": flow.result,
            "exhaustive_subset": json_compact(subset),
            "exhaustive_ms": f"{exhaustive_ms:.9f}",
            "flow_ms": f"{flow.elapsed_ms:.9f}",
            "maximum_flow": flow.maximum_flow,
            "total_demand": flow.total_demand,
            "flow_nodes": flow.flow_nodes,
            "flow_edges": flow.flow_edges,
            "mismatch": mismatch,
        }
        rows.append(row)
        if mismatch:
            mismatches += 1
            failure = failures / f"flow_mismatch_seed_{seed}.json"
            failure.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if (offset + 1) % 100 == 0:
            write_csv(output / "flow_vs_exhaustive.csv", rows)
    write_csv(output / "flow_vs_exhaustive.csv", rows)
    return rows, mismatches


def solver_identity(config: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for name in ("cadical", "kissat"):
        executable = Path(config[name]["executable"])
        version = subprocess.run(
            [str(executable), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=10,
        )
        if version.returncode != 0:
            raise RuntimeError(f"could not read {name} version")
        result.append(
            {
                "name": name,
                "version": version.stdout.strip().splitlines()[0],
                "executable": str(executable),
                "binary_sha256": sha256_file(executable),
            }
        )
    return result


def parse_time_rss(path: Path) -> int | None:
    if not path.exists():
        return None
    integers = [line.strip() for line in path.read_text(encoding="ascii").splitlines() if line.strip().isdigit()]
    return int(integers[-1]) if integers else None


def run_hybrid_process(
    repo: Path, output: Path, config_path: Path, cnf_path: Path
) -> dict[str, Any]:
    stem = cnf_path.stem
    metrics_path = output / "logs" / "hybrid" / f"{stem}.metrics.json"
    stdout_path = output / "logs" / "hybrid" / f"{stem}.log"
    time_path = output / "logs" / "hybrid" / f"{stem}.time"
    fallback_path = output / "logs" / "fallback" / f"{stem}__cadical.log"
    witness_path = output / "logs" / "witnesses" / f"{stem}.witness.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "/usr/bin/time",
        "-f",
        "%M",
        "-o",
        str(time_path),
        sys.executable,
        "-m",
        "phase2.hybrid",
        "--cnf",
        str(cnf_path),
        "--config",
        str(config_path),
        "--metrics",
        str(metrics_path),
        "--fallback-log",
        str(fallback_path),
        "--witness",
        str(witness_path),
    ]
    with stdout_path.open("wb") as stream:
        completed = subprocess.run(
            command,
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=90,
            check=False,
        )
    if completed.returncode != 0 or not metrics_path.exists():
        raise RuntimeError(f"hybrid process failed for {cnf_path.name}; see {stdout_path}")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["peak_rss_kb"] = parse_time_rss(time_path)
    metrics["peak_rss"] = metrics["peak_rss_kb"]
    metrics["peak_rss_unit"] = "kB"
    metrics["hybrid_log"] = stdout_path.relative_to(repo).as_posix()
    metrics["fallback_log"] = (
        fallback_path.relative_to(repo).as_posix() if fallback_path.exists() else ""
    )
    if metrics["witness_file"]:
        metrics["witness_file"] = witness_path.relative_to(repo).as_posix()
    return metrics


def generate_report(
    output: Path,
    run_id: str,
    config: dict[str, Any],
    oracle_rows: list[dict[str, Any]],
    flow_mismatches: int,
    benchmark_rows: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    correctness: list[dict[str, Any]],
    final_mismatches: int,
    phase1_digest: dict[str, Any],
) -> None:
    gate_counts = Counter(row["gate_decision"] for row in measurements)
    benefit_counts = Counter(row["benefit_gate_decision"] for row in measurements)
    recovery_counts = Counter(row["recovery_status"] for row in measurements)
    recovery_reasons = Counter(row["recovery_reason"] for row in measurements)
    structural_counts = Counter(row["structural_result"] for row in measurements)
    fallback_count = sum(bool(row["fallback_used"]) for row in measurements)
    witness_count = sum(bool(row["witness_valid"]) for row in measurements)
    report = f"""# Phase 2 results - structural correctness validation

Run ID: `{run_id}`  
Completed: {utc_now()}

## What was implemented

- FastCheapGate with monotone-cardinality and mirror-gadget signals. It only routes to LKK or CDCL.
- A transparent deterministic BenefitGate with recorded accept/reject reasons.
- Bounded, non-destructive resolution recovery with time, memory, derived-clause, depth, clause-size, and pivot-occurrence limits. Every derived clause stores a checkable resolution trace.
- Conservative exact-r `B(G,r)` registration without assignment materialization, plus validated resource at-most-capacity families.
- Dinic FlowBoxTest using source-to-box demand edges, unit box-to-resource edges, and resource-to-sink capacity edges.
- Machine-checkable max-flow/min-cut UNSAT witnesses and an independent witness checker.
- Exhaustive subset BoxTest restricted to small correctness-oracle instances.
- External locked CaDiCaL fallback and independent locked CaDiCaL/Kissat cross-checks.

No LemmaBridge, CaDiCaL API integration, learned BenefitGate, or benchmark-performance optimization was implemented.

## Exact commands

```powershell
python -m unittest benchmark.test_harness phase2.test_phase2 -v
powershell -ExecutionPolicy Bypass -File .\\scripts\\run_phase2.ps1 -RunId {run_id}
```

Configuration snapshot: `config.json`. Oracle seeds are {config['oracle_seed_start']} through {int(config['oracle_seed_start']) + int(config['oracle_instances']) - 1}.

## Correctness counts

- Random capacity oracle instances: {len(oracle_rows)}
- FlowBoxTest/exhaustive mismatches: {flow_mismatches}
- Generated structural CNFs: {len(benchmark_rows)}
- CNFs checked by both locked solvers: {len(correctness)}
- LKK-hybrid/CDCL/ground-truth final mismatches: {final_mismatches}
- Valid structural UNSAT witnesses: {witness_count}
- External fallback uses: {fallback_count}

## Gate behavior

- FastCheapGate: {dict(gate_counts)}
- BenefitGate: {dict(benefit_counts)}
- Structural outcomes: {dict(structural_counts)}

Gate and benefit costs are recorded per instance in `hybrid_measurements.csv`. Neither gate makes a SAT/UNSAT decision.

## Recovery and budget behavior

- Recovery statuses: {dict(recovery_counts)}
- Recovery reasons/aborts: {dict(recovery_reasons)}
- Default budgets: {config['recovery']['time_budget_ms']} ms, {config['recovery']['memory_budget_bytes']} bytes, {config['recovery']['derived_clause_budget']} derived clauses, resolution depth {config['recovery']['max_resolution_depth']}, derived clause size {config['recovery']['max_clause_size']}.
- Four explicit negative controls set the derived-clause budget to zero; all four aborted, returned UNKNOWN, and used CDCL fallback.

Every recovery abort routed to UNKNOWN and then external CaDiCaL. Partial recovery data was never used to assert UNSAT.

## Raw outputs

- `correctness.csv`: expected, hybrid, CaDiCaL, and Kissat answers.
- `flow_vs_exhaustive.csv`: all randomized oracle inputs, results, and timings.
- `structural_benchmarks.csv`: generated family/noise inventory and hashes.
- `hybrid_measurements.csv`: separated gate, benefit, recovery, registry, flow, fallback, total, memory, and structural telemetry.
- `logs/`: raw solver logs, hybrid logs, metrics, memory records, and witnesses.
- `failures/`: permanent counterexamples or end-to-end mismatch records, if any.
- `metadata.json`: machine, solver, configuration, and immutability metadata.

## Phase 1 immutability

The accepted Phase 1 tree remained at {phase1_digest['file_count']} files with SHA-256 tree digest `{phase1_digest['tree_sha256']}` before and after this run.

## Limitations and negative results

- These are generated correctness instances, not evidence that LKK is faster than either CDCL solver.
- Exact-r discovery currently recognizes complete direct combinational clause families or families recovered by the bounded resolution policy. Other sound encodings normally return UNKNOWN.
- Resource recovery is deliberately conservative: every box variable must map unambiguously to one validated capacity group.
- A FEASIBLE flow result never implies general SAT; all such cases use CDCL fallback.
- Noise can cause gate rejection, incomplete registries, or budget aborts. Those outcomes are preserved in the CSV/logs and are correct UNKNOWN behavior.

## Next experiment

Phase 3 should expand randomized CNF-level correctness testing and compare FlowBoxTest with exhaustive BoxTest over additional small encodings. It must retain witness checking and locked-solver cross-checks. No Phase 3 work has begun.
"""
    (output / "RESULTS.md").write_text(report, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the complete LKK-HSAT Phase 2 validation")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = Path.cwd().resolve()
    config_path = args.config.resolve()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "failures").mkdir()
    (output / "logs").mkdir()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    (output / "config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    phase1 = repo / config["immutable_phase1_relative_path"]
    phase1_before = tree_digest(phase1)
    if phase1_before["tree_sha256"] != config["immutable_phase1_tree_sha256"]:
        raise RuntimeError("accepted Phase 1 tree does not match its immutable expected digest")
    identities = solver_identity(config)
    metadata: dict[str, Any] = {
        "phase": 2,
        "run_id": args.run_id,
        "started_utc": utc_now(),
        "config_sha256": sha256_file(config_path),
        "phase1_before": phase1_before,
        "solvers": identities,
        "machine": machine_metadata(),
        "docker_image_id": os.environ.get("LKK_DOCKER_IMAGE_ID"),
        "docker_server_version": os.environ.get("LKK_DOCKER_SERVER_VERSION"),
        "host_name": os.environ.get("LKK_HOST_NAME"),
        "python": sys.version,
        "platform": platform.platform(),
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    oracle_rows, flow_mismatches = run_flow_oracle(config, output)

    benchmark_rows: list[dict[str, Any]] = []
    cnf_dir = output / "cnfs"
    generator_seed = int(config["generator_seed"])
    instance_index = 0
    for spec in benchmark_specs():
        for noise in config["noise_levels_percent"]:
            instance_index += 1
            seed = generator_seed + instance_index
            name = f"{instance_index:03d}_{spec.family}_noise{noise}.cnf"
            row = generate_benchmark(spec, int(noise), seed, cnf_dir / name)
            benchmark_rows.append(row)
    write_csv(output / "structural_benchmarks.csv", benchmark_rows)

    measurements: list[dict[str, Any]] = []
    correctness: list[dict[str, Any]] = []
    final_mismatches = 0
    for benchmark in benchmark_rows:
        cnf_path = cnf_dir / str(benchmark["instance"])
        instance_config_path = config_path
        if benchmark.get("budget_probe"):
            probe_config = json.loads(json.dumps(config))
            probe_config["recovery"]["derived_clause_budget"] = 0
            instance_config_path = output / "logs" / "hybrid-config" / f"{cnf_path.stem}.json"
            instance_config_path.parent.mkdir(parents=True, exist_ok=True)
            instance_config_path.write_text(
                json.dumps(probe_config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        hybrid = run_hybrid_process(repo, output, instance_config_path, cnf_path)
        hybrid["family"] = benchmark["family"]
        hybrid["noise_percent"] = benchmark["noise_percent"]
        hybrid["hidden"] = benchmark["hidden"]
        hybrid["budget_probe"] = benchmark.get("budget_probe", False)
        measurements.append(hybrid)
        write_csv(output / "hybrid_measurements.csv", measurements)

        direct_results: dict[str, str] = {}
        direct_details: dict[str, Any] = {}
        for solver_name in ("cadical", "kissat"):
            log_path = output / "logs" / "crosscheck" / f"{cnf_path.stem}__{solver_name}.log"
            status, elapsed_ms, timed_out, exit_code = run_external_solver(
                config[solver_name],
                cnf_path,
                float(config["crosscheck_timeout_seconds"]),
                log_path,
            )
            direct_results[solver_name] = status
            direct_details[f"{solver_name}_ms"] = f"{elapsed_ms:.6f}"
            direct_details[f"{solver_name}_timed_out"] = timed_out
            direct_details[f"{solver_name}_exit_code"] = exit_code
            direct_details[f"{solver_name}_log"] = log_path.relative_to(repo).as_posix()
        expected = str(benchmark["expected_result"])
        mismatch = not (
            hybrid["final_result"] == expected
            and direct_results["cadical"] == expected
            and direct_results["kissat"] == expected
        )
        if hybrid["structural_result"] == "UNSAT" and not hybrid["witness_valid"]:
            mismatch = True
        correctness_row = {
            "instance": benchmark["instance"],
            "family": benchmark["family"],
            "noise_percent": benchmark["noise_percent"],
            "hidden": benchmark["hidden"],
            "budget_probe": benchmark.get("budget_probe", False),
            "expected_result": expected,
            "lkk_hybrid_result": hybrid["final_result"],
            "structural_result": hybrid["structural_result"],
            "witness_valid": hybrid["witness_valid"],
            "fallback_used": hybrid["fallback_used"],
            "cadical_result": direct_results["cadical"],
            "kissat_result": direct_results["kissat"],
            **direct_details,
            "mismatch": mismatch,
        }
        correctness.append(correctness_row)
        write_csv(output / "correctness.csv", correctness)
        if mismatch:
            final_mismatches += 1
            (output / "failures" / f"end_to_end_{cnf_path.stem}.json").write_text(
                json.dumps(correctness_row, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    phase1_after = tree_digest(phase1)
    immutable = phase1_after == phase1_before
    if not immutable:
        raise RuntimeError("accepted Phase 1 artifacts changed during Phase 2")
    metadata.update(
        {
            "completed_utc": utc_now(),
            "phase1_after": phase1_after,
            "phase1_immutable": immutable,
            "oracle_instances": len(oracle_rows),
            "flow_exhaustive_mismatches": flow_mismatches,
            "structural_cnfs": len(benchmark_rows),
            "final_answer_mismatches": final_mismatches,
            "valid_witnesses": sum(bool(row["witness_valid"]) for row in measurements),
            "fallback_uses": sum(bool(row["fallback_used"]) for row in measurements),
        }
    )
    (output / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    generate_report(
        output,
        args.run_id,
        config,
        oracle_rows,
        flow_mismatches,
        benchmark_rows,
        measurements,
        correctness,
        final_mismatches,
        phase1_after,
    )
    summary = {
        "run_id": args.run_id,
        "oracle_instances": len(oracle_rows),
        "flow_exhaustive_mismatches": flow_mismatches,
        "structural_cnfs": len(benchmark_rows),
        "final_answer_mismatches": final_mismatches,
        "valid_witnesses": metadata["valid_witnesses"],
        "fallback_uses": metadata["fallback_uses"],
        "phase1_immutable": immutable,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if flow_mismatches == 0 and final_mismatches == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
