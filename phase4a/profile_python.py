from __future__ import annotations

import argparse
import cProfile
import csv
import json
import pstats
import subprocess
import sys
import time
from pathlib import Path

from phase2.cnf import bounded_resolution, parse_cnf
from phase2.flow import flow_box_test
from phase2.structure import benefit_gate, build_structural_model, fast_cheap_gate, recover_exact_registry
from phase2.witness import check_witness, create_witness


def ns() -> int:
    return time.perf_counter_ns()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase3", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text())
    inventory = list(csv.DictReader((args.phase3 / "instances.csv").open(newline="", encoding="utf-8")))
    rows: list[dict[str, object]] = []
    profiler = cProfile.Profile()
    profiler.enable()
    for item in inventory:
        path = args.phase3 / "cnfs" / item["instance"]
        started = ns(); cnf = parse_cnf(path); parse_ms = (ns() - started) / 1e6
        gate = fast_cheap_gate(cnf)
        benefit_ms = recovery_ms = registry_ms = flow_ms = witness_ms = witness_check_ms = 0.0
        structural = "UNKNOWN"; boxes = resources = checks = 0
        if gate.decision == "RUN_LKK":
            benefit = benefit_gate(cnf, gate, config["benefit_gate"]); benefit_ms = benefit.elapsed_ms
            if benefit.decision == "RUN_LKK":
                recovery = bounded_resolution(cnf, config["recovery"]); recovery_ms = recovery.elapsed_ms
                registry = recover_exact_registry(recovery, config["registry"]); registry_ms = registry.elapsed_ms
                boxes, resources, checks = len(registry.boxes), len(registry.resources), registry.family_checks
                if registry.status == "COMPLETE":
                    model = build_structural_model(registry); flow = flow_box_test(model.capacity_instance); flow_ms = flow.elapsed_ms
                    structural = flow.result
                    if flow.result == "UNSAT":
                        started = ns(); witness = create_witness(cnf, recovery, model, flow); witness_ms = (ns() - started) / 1e6
                        wp = args.output / (path.stem + ".profile.witness.json")
                        wp.write_text(json.dumps(witness, sort_keys=True), encoding="utf-8")
                        started = ns(); check_witness(path, wp); witness_check_ms = (ns() - started) / 1e6
        rows.append({"instance": path.name, "parse_ms": parse_ms, "gate_ms": gate.elapsed_ms,
                     "benefit_gate_ms": benefit_ms, "recovery_ms": recovery_ms,
                     "registry_ms": registry_ms, "flow_ms": flow_ms, "witness_ms": witness_ms,
                     "witness_check_ms": witness_check_ms, "structural_result": structural,
                     "boxes_found": boxes, "resources_found": resources, "family_checks": checks})
    profiler.disable()
    profiler.dump_stats(args.output / "python_structural.prof")
    with (args.output / "python_structural.pstats.txt").open("w", encoding="utf-8") as out:
        pstats.Stats(profiler, stream=out).strip_dirs().sort_stats("cumulative").print_stats(80)
    fields = list(rows[0])
    with (args.output / "python_phase_profile.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fields); writer.writeheader(); writer.writerows(rows)

    # Startup/serialization are measured outside the in-process profile.
    startup = []
    for repetition in range(20):
        started = ns()
        subprocess.run([sys.executable, "-c", "import phase2.hybrid"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        startup.append((ns() - started) / 1e6)
    (args.output / "startup.json").write_text(json.dumps({"repetitions": 20,
        "samples_ms": startup, "mean_ms": sum(startup) / len(startup)}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
