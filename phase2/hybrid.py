from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from benchmark.harness import parse_status
from phase2.cnf import bounded_resolution, parse_cnf
from phase2.flow import flow_box_test
from phase2.structure import (
    benefit_gate,
    build_structural_model,
    fast_cheap_gate,
    recover_exact_registry,
)
from phase2.witness import check_witness, create_witness


def run_external_solver(
    solver: dict[str, Any], cnf_path: Path, timeout_seconds: float, log_path: Path
) -> tuple[str, float, bool, int | None]:
    command = [
        solver["executable"],
        *(arg.format(cnf=str(cnf_path)) for arg in solver["args"]),
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter_ns()
    timed_out = False
    with log_path.open("wb") as stream:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
    elapsed_ms = (time.perf_counter_ns() - start) / 1e6
    output = log_path.read_text(encoding="utf-8", errors="replace")
    return parse_status(output, process.returncode, timed_out), elapsed_ms, timed_out, process.returncode


def solve_hybrid(
    cnf_path: Path,
    config: dict[str, Any],
    fallback_log: Path,
    witness_path: Path,
) -> dict[str, Any]:
    total_start = time.perf_counter_ns()
    cnf = parse_cnf(cnf_path)
    gate = fast_cheap_gate(cnf)
    benefit_ms = 0.0
    benefit_decision = "NOT_RUN"
    benefit_reason = "fast_gate_skipped"
    recovery_ms = 0.0
    registry_ms = 0.0
    flow_ms = 0.0
    fallback_ms = 0.0
    boxes_found = 0
    resources_found = 0
    flow_nodes = 0
    flow_edges = 0
    recovery_status = "NOT_RUN"
    recovery_reason = "fast_gate_skipped"
    registry_status = "NOT_RUN"
    registry_reason = "fast_gate_skipped"
    structural_result = "UNKNOWN"
    fallback_used = False
    witness_valid = False
    witness_file = ""
    maximum_flow: int | None = None
    total_demand: int | None = None

    should_run = gate.decision == "RUN_LKK"
    if should_run:
        benefit = benefit_gate(cnf, gate, config["benefit_gate"])
        benefit_ms = benefit.elapsed_ms
        benefit_decision = benefit.decision
        benefit_reason = benefit.reason
        should_run = benefit.decision == "RUN_LKK"
        if not should_run:
            recovery_reason = "benefit_gate_skipped"
            registry_reason = "benefit_gate_skipped"

    if should_run:
        recovery = bounded_resolution(cnf, config["recovery"])
        recovery_ms = recovery.elapsed_ms
        recovery_status = recovery.status
        recovery_reason = recovery.reason
        registry = recover_exact_registry(recovery, config["registry"])
        registry_ms = registry.elapsed_ms
        registry_status = registry.status
        registry_reason = registry.reason
        boxes_found = len(registry.boxes)
        resources_found = len(registry.resources)
        if registry.status == "COMPLETE":
            try:
                model = build_structural_model(registry)
            except ValueError as exc:
                registry_status = "UNKNOWN"
                registry_reason = f"model_rejected:{exc}"
            else:
                flow = flow_box_test(model.capacity_instance)
                flow_ms = flow.elapsed_ms
                flow_nodes = flow.flow_nodes
                flow_edges = flow.flow_edges
                maximum_flow = flow.maximum_flow
                total_demand = flow.total_demand
                if flow.result == "UNSAT":
                    witness_path.parent.mkdir(parents=True, exist_ok=True)
                    witness_path.write_text(
                        json.dumps(
                            create_witness(cnf, recovery, model, flow),
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    verdict = check_witness(cnf_path, witness_path)
                    witness_valid = verdict["valid"] is True
                    if witness_valid:
                        structural_result = "UNSAT"
                        witness_file = str(witness_path)
                else:
                    structural_result = "FEASIBLE"

    if structural_result == "UNSAT" and witness_valid:
        final_result = "UNSATISFIABLE"
        fallback_status = "NOT_RUN"
        fallback_timed_out = False
        fallback_exit_code = None
    else:
        fallback_used = True
        fallback_status, fallback_ms, fallback_timed_out, fallback_exit_code = run_external_solver(
            config["cadical"],
            cnf_path,
            float(config["fallback_timeout_seconds"]),
            fallback_log,
        )
        final_result = fallback_status

    return {
        "instance": cnf_path.name,
        "cnf_sha256": cnf.sha256,
        "gate_ms": gate.elapsed_ms,
        "gate_decision": gate.decision,
        "gate_reason": gate.reason,
        "gate_signals": json.dumps(gate.signals, sort_keys=True, separators=(",", ":")),
        "benefit_gate_ms": benefit_ms,
        "benefit_gate_decision": benefit_decision,
        "benefit_gate_reason": benefit_reason,
        "recovery_ms": recovery_ms,
        "recovery_status": recovery_status,
        "recovery_reason": recovery_reason,
        "registry_ms": registry_ms,
        "registry_status": registry_status,
        "registry_reason": registry_reason,
        "flow_ms": flow_ms,
        "fallback_cdcl_ms": fallback_ms,
        "total_lkk_hybrid_ms": (time.perf_counter_ns() - total_start) / 1e6,
        "boxes_found": boxes_found,
        "resources_found": resources_found,
        "flow_nodes": flow_nodes,
        "flow_edges": flow_edges,
        "maximum_flow": maximum_flow,
        "total_demand": total_demand,
        "structural_result": structural_result,
        "final_result": final_result,
        "fallback_used": fallback_used,
        "fallback_status": fallback_status,
        "fallback_timed_out": fallback_timed_out,
        "fallback_exit_code": fallback_exit_code,
        "witness_valid": witness_valid,
        "witness_file": witness_file,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one LKK-HSAT Phase 2 hybrid solve")
    parser.add_argument("--cnf", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--fallback-log", required=True, type=Path)
    parser.add_argument("--witness", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    metrics = solve_hybrid(args.cnf, config, args.fallback_log, args.witness)
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(metrics, sort_keys=True))
    return 0 if metrics["final_result"] in {"SATISFIABLE", "UNSATISFIABLE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
