from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from benchmark.harness import (
    CnfInfo,
    SolverInfo,
    parse_statistics,
    parse_status,
    parse_dimacs,
    sha256_file,
)


def tree_digest(root: Path) -> dict[str, Any]:
    entries = []
    files = [item for item in root.rglob("*") if item.is_file()]
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix().casefold()):
        entries.append(f"{path.relative_to(root).as_posix()} {sha256_file(path)}")
    payload = ("\n".join(entries) + "\n").encode("utf-8")
    return {
        "root": root.as_posix(),
        "file_count": len(entries),
        "tree_sha256": hashlib.sha256(payload).hexdigest(),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"empty CSV: {path}")
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def solver_infos(config: dict[str, Any]) -> list[SolverInfo]:
    infos = []
    for name in ("cadical", "kissat"):
        item = config[name]
        executable = Path(item["executable"])
        version = subprocess.run(
            [str(executable), *item.get("version_args", ["--version"])],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=10,
        )
        if version.returncode != 0:
            raise RuntimeError(f"failed to identify {name}")
        infos.append(
            SolverInfo(
                name,
                executable,
                version.stdout.strip().splitlines()[0],
                sha256_file(executable),
                tuple(item["args"]),
            )
        )
    return infos


def benchmark_cnf(path: Path, repo: Path) -> CnfInfo:
    return parse_dimacs(path.resolve(), repo)


def direct_run(
    solver: SolverInfo,
    cnf: CnfInfo,
    seed: int,
    timeout_seconds: float,
    log_path: Path,
) -> dict[str, Any]:
    rendered = [arg.format(seed=seed, cnf=str(cnf.path)) for arg in solver.args]
    time_path = log_path.with_suffix(".time")
    command = [
        "/usr/bin/time", "-f", "%M", "-o", str(time_path),
        "/usr/bin/timeout", "--signal=KILL", str(timeout_seconds),
        str(solver.executable), *rendered,
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter_ns()
    with log_path.open("wb") as stream:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds + 3,
            check=False,
        )
    runtime_ms = (time.perf_counter_ns() - start) / 1e6
    timed_out = completed.returncode in {124, 137}
    output = log_path.read_text(encoding="utf-8", errors="replace")
    status = parse_status(output, completed.returncode, timed_out)
    statistics = parse_statistics(output)
    return {
        "solver": solver.name,
        "solver_version": solver.version,
        "binary_sha256": solver.binary_sha256,
        "seed": seed,
        "runtime_ms": runtime_ms,
        "status": status,
        "timed_out": timed_out,
        "exit_code": completed.returncode,
        "peak_rss_kb": parse_time_rss(time_path),
        "conflicts": _find_stat(statistics, "conflicts"),
        "decisions": _find_stat(statistics, "decisions"),
        "propagations": _find_stat(statistics, "propagations"),
        "raw_log": str(log_path),
    }


def _find_stat(stats: dict[str, int | float], name: str) -> int | float | None:
    if name in stats:
        return stats[name]
    for key, value in stats.items():
        if key.endswith("_" + name) or key.startswith(name + "_"):
            return value
    return None


def parse_time_rss(path: Path) -> int | None:
    if not path.exists():
        return None
    integers = [line.strip() for line in path.read_text(encoding="ascii").splitlines() if line.strip().isdigit()]
    return int(integers[-1]) if integers else None


def hybrid_run(
    repo: Path,
    config: dict[str, Any],
    config_path: Path,
    cnf_path: Path,
    seed: int,
    timeout_seconds: float,
    run_name: str,
    logs_root: Path,
) -> dict[str, Any]:
    seed_config = json.loads(json.dumps(config))
    seed_config["cadical"]["args"] = [
        arg.replace("{seed}", str(seed)) for arg in seed_config["cadical"]["args"]
    ]
    seed_config["fallback_timeout_seconds"] = timeout_seconds
    effective_config = logs_root / "configs" / f"{run_name}.json"
    effective_config.parent.mkdir(parents=True, exist_ok=True)
    effective_config.write_text(
        json.dumps(seed_config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    metrics_path = logs_root / "hybrid" / f"{run_name}.metrics.json"
    stdout_path = logs_root / "hybrid" / f"{run_name}.log"
    time_path = logs_root / "hybrid" / f"{run_name}.time"
    fallback_path = logs_root / "fallback" / f"{run_name}__cadical.log"
    witness_path = logs_root / "witnesses" / f"{run_name}.witness.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "/usr/bin/time", "-f", "%M", "-o", str(time_path),
        "/usr/bin/timeout", "--signal=KILL", str(timeout_seconds),
        sys.executable, "-m", "phase2.hybrid",
        "--cnf", str(cnf_path),
        "--config", str(effective_config),
        "--metrics", str(metrics_path),
        "--fallback-log", str(fallback_path),
        "--witness", str(witness_path),
    ]
    start = time.perf_counter_ns()
    with stdout_path.open("wb") as stream:
        completed = subprocess.run(
            command,
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds + 3,
            check=False,
        )
    wall_ms = (time.perf_counter_ns() - start) / 1e6
    timed_out = completed.returncode in {124, 137}
    if timed_out or not metrics_path.exists():
        return {
            "solver": "lkk_hybrid",
            "seed": seed,
            "runtime_ms": wall_ms,
            "status": "TIMEOUT" if timed_out else "ERROR",
            "timed_out": timed_out,
            "exit_code": completed.returncode,
            "peak_rss_kb": parse_time_rss(time_path),
            "gate_ms": None,
            "benefit_gate_ms": None,
            "recovery_ms": None,
            "registry_ms": None,
            "flow_ms": None,
            "fallback_cdcl_ms": None,
            "total_lkk_hybrid_ms": None,
            "structural_result": "UNKNOWN",
            "fallback_used": None,
            "raw_log": str(stdout_path),
        }
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    fallback_stats: dict[str, int | float] = {}
    if fallback_path.exists():
        fallback_stats = parse_statistics(fallback_path.read_text(encoding="utf-8", errors="replace"))
    return {
        "solver": "lkk_hybrid",
        "seed": seed,
        "runtime_ms": wall_ms,
        "status": metrics["final_result"],
        "timed_out": False,
        "exit_code": completed.returncode,
        "peak_rss_kb": parse_time_rss(time_path),
        "conflicts": _find_stat(fallback_stats, "conflicts"),
        "decisions": _find_stat(fallback_stats, "decisions"),
        "propagations": _find_stat(fallback_stats, "propagations"),
        "raw_log": str(stdout_path),
        **metrics,
        "total_lkk_end_to_end_ms": wall_ms,
    }
