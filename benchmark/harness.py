#!/usr/bin/env python3
"""Reproducible direct-CLI SAT solver benchmark harness for Phase 1."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


RESULT_RE = re.compile(r"^s\s+(SATISFIABLE|UNSATISFIABLE|UNKNOWN)\s*$", re.MULTILINE)
STAT_RE = re.compile(
    r"^\s*c\s+([A-Za-z][A-Za-z0-9 _-]*?):\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
    re.MULTILINE,
)
CSV_FIELDS = [
    "experiment",
    "run_order",
    "instance",
    "cnf_sha256",
    "variables",
    "clauses",
    "solver",
    "solver_version",
    "binary_sha256",
    "seed",
    "repetition",
    "timeout_seconds",
    "runtime_seconds",
    "status",
    "timed_out",
    "exit_code",
    "peak_rss_kb",
    "conflicts",
    "decisions",
    "propagations",
    "raw_log",
]


class HarnessError(RuntimeError):
    pass


@dataclass(frozen=True)
class CnfInfo:
    path: Path
    relative_path: str
    sha256: str
    variables: int
    clauses: int


@dataclass(frozen=True)
class SolverInfo:
    name: str
    executable: Path
    version: str
    binary_sha256: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class ProcessResult:
    runtime_seconds: float
    status: str
    timed_out: bool
    exit_code: int | None
    peak_rss_kb: int | None
    statistics: dict[str, int | float]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dimacs(path: Path, repo: Path) -> CnfInfo:
    header: tuple[int, int] | None = None
    clause_count = 0
    open_clause = False

    with path.open("r", encoding="ascii") as stream:
        for line_number, raw in enumerate(stream, 1):
            line = raw.strip()
            if not line or line.startswith("c"):
                continue
            if line.startswith("p"):
                if header is not None:
                    raise HarnessError(f"{path}:{line_number}: duplicate DIMACS header")
                parts = line.split()
                if len(parts) != 4 or parts[:2] != ["p", "cnf"]:
                    raise HarnessError(f"{path}:{line_number}: invalid DIMACS header")
                try:
                    header = (int(parts[2]), int(parts[3]))
                except ValueError as exc:
                    raise HarnessError(f"{path}:{line_number}: non-integer header") from exc
                if min(header) < 0:
                    raise HarnessError(f"{path}:{line_number}: negative header count")
                continue
            if header is None:
                raise HarnessError(f"{path}:{line_number}: clause precedes DIMACS header")
            for token in line.split():
                try:
                    literal = int(token)
                except ValueError as exc:
                    raise HarnessError(f"{path}:{line_number}: invalid literal {token!r}") from exc
                if literal == 0:
                    clause_count += 1
                    open_clause = False
                else:
                    if abs(literal) > header[0]:
                        raise HarnessError(
                            f"{path}:{line_number}: literal {literal} exceeds declared variables"
                        )
                    open_clause = True

    if header is None:
        raise HarnessError(f"{path}: missing DIMACS header")
    if open_clause:
        raise HarnessError(f"{path}: final clause is not terminated by 0")
    if clause_count != header[1]:
        raise HarnessError(
            f"{path}: declares {header[1]} clauses but contains {clause_count}"
        )
    return CnfInfo(
        path=path,
        relative_path=path.relative_to(repo).as_posix(),
        sha256=sha256_file(path),
        variables=header[0],
        clauses=header[1],
    )


def parse_status(output: str, exit_code: int | None, timed_out: bool) -> str:
    if timed_out:
        return "TIMEOUT"
    matches = RESULT_RE.findall(output)
    distinct = set(matches)
    if len(distinct) > 1:
        return "ERROR"
    reported = matches[-1] if matches else None
    exit_status = {10: "SATISFIABLE", 20: "UNSATISFIABLE"}.get(exit_code)
    if reported and exit_status and reported != exit_status:
        return "ERROR"
    return reported or exit_status or "ERROR"


def parse_statistics(output: str) -> dict[str, int | float]:
    parsed: dict[str, int | float] = {}
    for name, raw_value in STAT_RE.findall(output):
        key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        value_text = raw_value.replace(",", "")
        value: int | float = float(value_text) if "." in value_text else int(value_text)
        parsed[key] = value
    return parsed


def find_stat(statistics: dict[str, int | float], wanted: str) -> int | float | None:
    if wanted in statistics:
        return statistics[wanted]
    for key, value in statistics.items():
        if key.endswith("_" + wanted) or key.startswith(wanted + "_"):
            return value
    return None


def read_linux_rss_kb(pid: int) -> int | None:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
    except (FileNotFoundError, PermissionError, OSError):
        return None
    values = []
    for key in ("VmHWM", "VmRSS"):
        match = re.search(rf"^{key}:\s+(\d+)\s+kB$", text, re.MULTILINE)
        if match:
            values.append(int(match.group(1)))
    return max(values) if values else None


def parse_gnu_time_rss(text: str) -> int | None:
    """Read GNU time %M despite its diagnostic for SAT-style exit codes 10/20."""
    integer_lines = [line.strip() for line in text.splitlines() if line.strip().isdigit()]
    return int(integer_lines[-1]) if integer_lines else None


def terminate_process(process: subprocess.Popen[Any]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()
    process.wait()


def run_solver(
    solver: SolverInfo,
    cnf: CnfInfo,
    seed: int,
    timeout_seconds: float,
    log_path: Path,
) -> ProcessResult:
    rendered = [arg.format(seed=seed, cnf=str(cnf.path)) for arg in solver.args]
    command = [str(solver.executable), *rendered]
    time_path = log_path.with_suffix(".time")
    use_gnu_time = os.name == "posix" and Path("/usr/bin/time").is_file()
    if use_gnu_time:
        command = ["/usr/bin/time", "-f", "%M", "-o", str(time_path), *command]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter_ns()
    peak_rss_kb: int | None = None
    timed_out = False
    with log_path.open("wb") as log_stream:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
        )
        while process.poll() is None:
            if not use_gnu_time and sys.platform.startswith("linux"):
                sample = read_linux_rss_kb(process.pid)
                if sample is not None:
                    peak_rss_kb = max(peak_rss_kb or 0, sample)
            if (time.perf_counter_ns() - start) / 1e9 >= timeout_seconds:
                timed_out = True
                terminate_process(process)
                break
            time.sleep(0.005)
        exit_code = process.returncode
    runtime_seconds = (time.perf_counter_ns() - start) / 1e9

    if use_gnu_time and time_path.exists():
        peak_rss_kb = parse_gnu_time_rss(time_path.read_text(encoding="ascii"))
    output = log_path.read_text(encoding="utf-8", errors="replace")
    return ProcessResult(
        runtime_seconds=runtime_seconds,
        status=parse_status(output, exit_code, timed_out),
        timed_out=timed_out,
        exit_code=exit_code,
        peak_rss_kb=peak_rss_kb,
        statistics=parse_statistics(output),
    )


def read_cpu_model() -> str | None:
    cpuinfo = Path("/proc/cpuinfo")
    if not cpuinfo.exists():
        return platform.processor() or None
    text = cpuinfo.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^model name\s*:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def read_memory_bytes() -> int | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    match = re.search(
        r"^MemTotal:\s+(\d+)\s+kB$",
        meminfo.read_text(encoding="ascii"),
        re.MULTILINE,
    )
    return int(match.group(1)) * 1024 if match else None


def machine_metadata() -> dict[str, Any]:
    return {
        "captured_utc": utc_now(),
        "platform": platform.platform(),
        "uname": list(platform.uname()),
        "python": sys.version,
        "logical_cpus_visible": os.cpu_count(),
        "cpu_model": read_cpu_model(),
        "memory_bytes_visible": read_memory_bytes(),
        "cgroup": Path("/proc/self/cgroup").read_text(encoding="utf-8", errors="replace")
        if Path("/proc/self/cgroup").exists()
        else None,
    }


def load_solvers(config: dict[str, Any]) -> list[SolverInfo]:
    solvers = []
    for item in config.get("solvers", []):
        executable = Path(item["executable"]).resolve()
        if not executable.is_file():
            raise HarnessError(f"solver executable does not exist: {executable}")
        version_run = subprocess.run(
            [str(executable), *item.get("version_args", ["--version"])],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
        version = version_run.stdout.strip().splitlines()
        if version_run.returncode != 0 or not version:
            raise HarnessError(f"could not read version from {item['name']}")
        solvers.append(
            SolverInfo(
                name=item["name"],
                executable=executable,
                version=version[0].strip(),
                binary_sha256=sha256_file(executable),
                args=tuple(item["args"]),
            )
        )
    if len(solvers) < 2:
        raise HarnessError("agreement testing requires at least two solvers")
    if len({solver.name for solver in solvers}) != len(solvers):
        raise HarnessError("solver names must be unique")
    return solvers


def discover_cnfs(repo: Path, patterns: Iterable[str]) -> list[CnfInfo]:
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(path.resolve() for path in repo.glob(pattern) if path.is_file())
    if not paths:
        raise HarnessError("no DIMACS files matched cnf_globs")
    return [parse_dimacs(path, repo) for path in sorted(paths)]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def relative_log(log_path: Path, repo: Path) -> str:
    try:
        return log_path.relative_to(repo).as_posix()
    except ValueError:
        return str(log_path)


def validate_agreement(
    repo: Path,
    output: Path,
    cnfs: list[CnfInfo],
    solvers: list[SolverInfo],
    seed: int,
    timeout: float,
) -> dict[str, str]:
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    expected: dict[str, str] = {}
    for cnf in cnfs:
        statuses: dict[str, str] = {}
        for solver in solvers:
            log_path = output / "logs" / "validation" / f"{cnf.path.stem}__{solver.name}.log"
            result = run_solver(solver, cnf, seed, timeout, log_path)
            statuses[solver.name] = result.status
            rows.append(
                {
                    "instance": cnf.relative_path,
                    "solver": solver.name,
                    "seed": seed,
                    "status": result.status,
                    "timed_out": result.timed_out,
                    "exit_code": result.exit_code,
                    "runtime_seconds": f"{result.runtime_seconds:.9f}",
                    "raw_log": relative_log(log_path, repo),
                }
            )
        values = set(statuses.values())
        if len(values) != 1 or not values.issubset({"SATISFIABLE", "UNSATISFIABLE"}):
            failures.append(f"{cnf.relative_path}: {statuses}")
        else:
            expected[cnf.relative_path] = next(iter(values))

    write_csv(
        output / "agreement.csv",
        rows,
        [
            "instance",
            "solver",
            "seed",
            "status",
            "timed_out",
            "exit_code",
            "runtime_seconds",
            "raw_log",
        ],
    )
    if failures:
        raise HarnessError(
            "agreement validation failed; no measurement runs were started:\n"
            + "\n".join(failures)
        )
    return expected


def benchmark(
    repo: Path,
    output: Path,
    config: dict[str, Any],
    cnfs: list[CnfInfo],
    solvers: list[SolverInfo],
    expected_results: dict[str, str],
) -> list[dict[str, Any]]:
    timeout = float(config["timeout_seconds"])
    seeds = [int(seed) for seed in config["seeds"]]
    repetitions = int(config["repetitions"])
    if timeout <= 0 or repetitions <= 0 or not seeds:
        raise HarnessError("timeout, repetitions, and seeds must all be positive/non-empty")

    rows: list[dict[str, Any]] = []
    order = 0
    for cnf_index, cnf in enumerate(cnfs):
        for repetition in range(1, repetitions + 1):
            for seed_index, seed in enumerate(seeds):
                rotation = (cnf_index + repetition + seed_index) % len(solvers)
                ordered_solvers = solvers[rotation:] + solvers[:rotation]
                for solver in ordered_solvers:
                    order += 1
                    log_path = (
                        output
                        / "logs"
                        / "measurements"
                        / f"{order:05d}__{cnf.path.stem}__{solver.name}__seed{seed}__rep{repetition}.log"
                    )
                    result = run_solver(solver, cnf, seed, timeout, log_path)
                    row = {
                        "experiment": config["experiment"],
                        "run_order": order,
                        "instance": cnf.relative_path,
                        "cnf_sha256": cnf.sha256,
                        "variables": cnf.variables,
                        "clauses": cnf.clauses,
                        "solver": solver.name,
                        "solver_version": solver.version,
                        "binary_sha256": solver.binary_sha256,
                        "seed": seed,
                        "repetition": repetition,
                        "timeout_seconds": timeout,
                        "runtime_seconds": f"{result.runtime_seconds:.9f}",
                        "status": result.status,
                        "timed_out": result.timed_out,
                        "exit_code": result.exit_code,
                        "peak_rss_kb": result.peak_rss_kb,
                        "conflicts": find_stat(result.statistics, "conflicts"),
                        "decisions": find_stat(result.statistics, "decisions"),
                        "propagations": find_stat(result.statistics, "propagations"),
                        "raw_log": relative_log(log_path, repo),
                    }
                    rows.append(row)
                    write_csv(output / "measurements.csv", rows, CSV_FIELDS)
                    if result.status not in {"SATISFIABLE", "UNSATISFIABLE", "TIMEOUT"}:
                        raise HarnessError(
                            f"solver error during measurement; partial results preserved: {log_path}"
                        )
                    if (
                        result.status in {"SATISFIABLE", "UNSATISFIABLE"}
                        and result.status != expected_results[cnf.relative_path]
                    ):
                        raise HarnessError(
                            "measured result disagrees with the validated result; "
                            f"partial results preserved: {log_path}"
                        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = Path.cwd().resolve()
    config_path = (repo / args.config).resolve() if not args.config.is_absolute() else args.config
    output = (repo / args.output).resolve() if not args.output.is_absolute() else args.output
    output.mkdir(parents=True, exist_ok=True)

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        solvers = load_solvers(config)
        cnfs = discover_cnfs(repo, config.get("cnf_globs", []))
        shutil.copyfile(config_path, output / "config.json")
        lock_path = repo / "solvers.lock.json"
        if lock_path.is_file():
            shutil.copyfile(lock_path, output / "solvers.lock.json")
        build_metadata_path = Path("/opt/solvers/BUILD-METADATA.txt")
        if build_metadata_path.is_file():
            shutil.copyfile(build_metadata_path, output / "solver-build-metadata.txt")
        metadata = machine_metadata()
        metadata.update(
            {
                "experiment": config.get("experiment"),
                "config_sha256": sha256_file(config_path),
                "solvers": [
                    {
                        "name": solver.name,
                        "executable": str(solver.executable),
                        "version": solver.version,
                        "binary_sha256": solver.binary_sha256,
                    }
                    for solver in solvers
                ],
                "instances": [
                    {
                        "path": cnf.relative_path,
                        "sha256": cnf.sha256,
                        "variables": cnf.variables,
                        "clauses": cnf.clauses,
                    }
                    for cnf in cnfs
                ],
            }
        )
        (output / "container-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        timeout = float(config["timeout_seconds"])
        expected_results = validate_agreement(
            repo,
            output,
            cnfs,
            solvers,
            int(config["validation_seed"]),
            timeout,
        )
        rows = benchmark(repo, output, config, cnfs, solvers, expected_results)
        summary = {
            "completed_utc": utc_now(),
            "agreement": "passed",
            "instances": len(cnfs),
            "solvers": len(solvers),
            "measurement_runs": len(rows),
            "timeouts": sum(row["timed_out"] is True for row in rows),
            "validated_results": expected_results,
            "status_counts": {
                status: sum(row["status"] == status for row in rows)
                for status in sorted({str(row["status"]) for row in rows})
            },
        }
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (HarnessError, KeyError, ValueError, json.JSONDecodeError) as exc:
        (output / "FAILED.txt").write_text(str(exc) + "\n", encoding="utf-8")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
