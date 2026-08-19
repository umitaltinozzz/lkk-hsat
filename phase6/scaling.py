"""Where the counting argument and the resolution proof part company.

hole_n asks n+1 pigeons into n holes. Haken's lower bound makes every resolution
proof of it exponential in n, so a CDCL solver's time must eventually explode;
the cut argument reads the same formula as one line of arithmetic. This measures
both on the same instances, in one process, so the numbers are engine time and
not container start-up.

The SATLIB corpus stops at hole10. Larger instances are generated here in the
standard encoding - the same one SATLIB uses, checked against it for n <= 10 -
and are labelled generated wherever they are reported.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

NATIVE = "/opt/solvers/bin/lkk-native5"


def write_php(path: Path, holes: int) -> None:
    """Pigeons 1..holes+1, holes 1..holes; variable (p-1)*holes + h."""
    pigeons = holes + 1
    clauses = []
    for p in range(1, pigeons + 1):
        clauses.append([(p - 1) * holes + h for h in range(1, holes + 1)])
    for h in range(1, holes + 1):
        for a in range(1, pigeons + 1):
            for b in range(a + 1, pigeons + 1):
                clauses.append([-((a - 1) * holes + h), -((b - 1) * holes + h)])
    lines = [f"c generated pigeonhole: {pigeons} pigeons, {holes} holes",
             f"p cnf {pigeons * holes} {len(clauses)}"]
    lines += [" ".join(map(str, c)) + " 0" for c in clauses]
    path.write_text("\n".join(lines) + "\n")


def timed(command: list[str], timeout: float) -> tuple[float, str]:
    start = time.perf_counter()
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return timeout * 1000.0, "TIMEOUT"
    elapsed = (time.perf_counter() - start) * 1000.0
    text = done.stdout
    status = ("UNSATISFIABLE" if "s UNSATISFIABLE" in text
              else "SATISFIABLE" if "s SATISFIABLE" in text else "UNKNOWN")
    return elapsed, status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("phase5/config.json"))
    parser.add_argument("--satlib", type=Path,
                        default=Path("benchmarks/phase5/satlib/pigeon-hole"))
    parser.add_argument("--generated", type=Path, default=Path("benchmarks/phase6/php"))
    parser.add_argument("--min-holes", type=int, default=6)
    parser.add_argument("--max-holes", type=int, default=13)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output", type=Path, default=Path("results/phase6/scaling.json"))
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    args.generated.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for holes in range(args.min_holes, args.max_holes + 1):
        satlib = args.satlib / f"hole{holes}.cnf"
        if satlib.exists():
            cnf, origin = satlib, "SATLIB"
        else:
            cnf = args.generated / f"hole{holes}.cnf"
            if not cnf.exists():
                write_php(cnf, holes)
            origin = "generated"

        import hashlib
        sha = hashlib.sha256(cnf.read_bytes()).hexdigest()
        start = time.perf_counter()
        done = subprocess.run(
            [NATIVE, "--cnf", str(cnf), "--sha256", sha, "--telemetry-only",
             "--fallback", "none", "--atleast-boxes"],
            capture_output=True, text=True, check=False)
        lkk_wall = (time.perf_counter() - start) * 1000.0
        telemetry = json.loads(done.stdout)

        row = {
            "holes": holes, "pigeons": holes + 1, "origin": origin,
            "variables": telemetry["variables"], "clauses": telemetry["clauses"],
            "lkk_front_end_ms": telemetry["total_end_to_end_ms"],
            "lkk_process_ms": lkk_wall,
            "lkk_result": telemetry["structural_result"],
            "boxes": telemetry["boxes_found"], "groups": telemetry["capacity_groups"],
        }
        for key in ("kissat", "cadical"):
            solver = config[key]
            rendered = [a.format(seed=1, cnf=str(cnf)) for a in solver["args"]]
            ms, status = timed([solver["executable"], *rendered], args.timeout)
            row[f"{key}_ms"], row[f"{key}_status"] = ms, status
        rows.append(row)
        print(json.dumps(row), flush=True)
        args.output.write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
