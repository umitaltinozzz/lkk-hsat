from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark.harness import machine_metadata
from phase3.common import (
    benchmark_cnf,
    direct_run,
    solver_infos,
    tree_digest,
    write_csv,
)
from phase3.generator import BenchmarkRequest, generate_request


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def requests_from_grid(config: dict[str, Any]) -> list[BenchmarkRequest]:
    requests = []
    for family, scales in config["calibration_grid"].items():
        for scale in scales:
            if family == "random_3sat":
                requests.append(BenchmarkRequest(family, int(scale), 0, "uniform"))
                requests.append(BenchmarkRequest(family, int(scale), 0, "planted_sat"))
            else:
                requests.append(BenchmarkRequest(family, int(scale)))
    return requests


def main() -> int:
    parser = argparse.ArgumentParser(description="Preserved Phase 3 size calibration")
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
    phase2 = repo / config["immutable_phase2_relative_path"]
    before = tree_digest(phase2)
    if before["tree_sha256"] != config["immutable_phase2_tree_sha256"]:
        raise RuntimeError("immutable Phase 2 digest mismatch before calibration")
    solvers = solver_infos(config)
    timeout = float(config["calibration_timeout_seconds"])
    requests = requests_from_grid(config)
    inventory = []
    rows = []
    seed_base = int(config["generator_seed"])
    for index, request in enumerate(requests, 1):
        stem = f"{index:03d}_{request.family}_{request.variant}_scale{request.scale}"
        path = output / "cnfs" / f"{stem}.cnf"
        item = generate_request(request, seed_base + index, path)
        inventory.append(item)
        write_csv(output / "instances.csv", inventory)
        cnf = benchmark_cnf(path, repo)
        for solver in solvers:
            log = output / "logs" / f"{stem}__{solver.name}.log"
            result = direct_run(solver, cnf, 1, timeout, log)
            rows.append(
                {
                    "instance": item["instance"],
                    "family": request.family,
                    "scale": request.scale,
                    "variant": request.variant,
                    "variables": item["variables"],
                    "clauses": item["clauses"],
                    **result,
                }
            )
            write_csv(output / "calibration.csv", rows)
    after = tree_digest(phase2)
    if after != before:
        raise RuntimeError("Phase 2 changed during calibration")
    metadata = {
        "phase": 3,
        "kind": "calibration",
        "run_id": args.run_id,
        "completed_utc": utc_now(),
        "timeout_seconds": timeout,
        "candidate_instances": len(inventory),
        "solver_runs": len(rows),
        "phase2_before": before,
        "phase2_after": after,
        "phase2_immutable": True,
        "machine": machine_metadata(),
        "solvers": [
            {
                "name": solver.name,
                "version": solver.version,
                "binary_sha256": solver.binary_sha256,
            }
            for solver in solvers
        ],
        "docker_image_id": os.environ.get("LKK_DOCKER_IMAGE_ID"),
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "candidate_instances": len(inventory),
        "solver_runs": len(rows),
        "timeouts": sum(bool(row["timed_out"]) for row in rows),
        "phase2_immutable": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

