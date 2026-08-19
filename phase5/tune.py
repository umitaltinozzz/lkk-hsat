"""Automated budget configuration on a calibration split.

Phase 4C changed what a check costs: work that used to be unbilled is now
charged, so `family_check_budget = 200000` no longer means what it meant when it
was chosen. Picking a replacement by looking at how the fourteen benchmark
instances respond would be tuning on the test set -- exactly what the Phase 5
methodology forbids -- so the value is fitted here instead, on instances
generated for this purpose with seeds disjoint from every benchmark set.

The procedure is a small F-race: every configuration is evaluated on a batch of
instances, configurations that are already clearly worse than the best are
dropped, and the survivors continue on the next batch. Racing matters because
the expensive configurations are exactly the ones worth eliminating early.

Nothing here reads `results/phase3`, and the tuner refuses to run against any
instance whose generator seed belongs to a benchmark range.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from phase3.generator import BenchmarkRequest, generate_request

NATIVE = "/opt/solvers/bin/lkk-native4c"
DEFINITE = {"SATISFIABLE", "UNSATISFIABLE"}

# Seeds used by earlier phases, kept out of bounds so a calibration instance can
# never coincide with a benchmark instance.
RESERVED_SEED_RANGES = ((31000, 32100), (41000, 41100), (51000, 51100),
                        (91000, 91100), (95000, 95100))
CALIBRATION_SEED_BASE = 71000

# Calibration must be drawn from the same distribution as the evaluation, or the
# race cannot see the regime where the parameter binds. A first version skipped
# capacity-2 at the scales where the budget actually matters and duly reported
# that every candidate budget was equivalent. Sharing families and scales with
# the benchmark is not leakage -- the seeds differ, and no benchmark result is
# consulted -- whereas choosing the budget by watching the benchmark would be.
CALIBRATION_FAMILIES = (
    *[BenchmarkRequest("pigeonhole_exact1", s, 0, "tune") for s in (6, 7, 8, 9, 10, 11)],
    *[BenchmarkRequest("exact_r_allocation", s, 0, "tune") for s in (5, 7, 9, 10, 11, 12)],
    *[BenchmarkRequest("overlapping_hall_violation", s, 0, "tune") for s in (5, 7, 8, 9, 10)],
    *[BenchmarkRequest("capacity2_violation", s, n, "tune")
      for s in (3, 4, 5, 6, 7) for n in (0, 100)],
    *[BenchmarkRequest("hidden_pigeonhole", s, n, "tune")
      for s in (6, 8, 9) for n in (0, 100)],
    *[BenchmarkRequest("satisfiable_capacity_control", s, 0, "tune")
      for s in (10, 18, 24, 26)],
    *[BenchmarkRequest("random_3sat", s, 0, "uniform") for s in (120, 300, 600)],
)


@dataclass(frozen=True)
class Configuration:
    family_check_budget: int

    def args(self) -> list[str]:
        return ["--family-check-budget", str(self.family_check_budget)]

    def key(self) -> str:
        return f"budget={self.family_check_budget}"


def guard_seed(seed: int) -> None:
    for low, high in RESERVED_SEED_RANGES:
        if low <= seed < high:
            raise ValueError(f"calibration seed {seed} collides with a benchmark range")


def build_calibration_set(directory: Path) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    items = []
    for index, request in enumerate(CALIBRATION_FAMILIES):
        seed = CALIBRATION_SEED_BASE + index
        guard_seed(seed)
        path = directory / f"{index:03d}_{request.family}_s{request.scale}_n{request.noise_percent}.cnf"
        info = generate_request(request, seed, path)
        items.append({**info, "path": str(path)})
    return items


def run(configuration: Configuration, cnf: Path, timeout: float,
        log: Path) -> dict[str, Any]:
    log.parent.mkdir(parents=True, exist_ok=True)
    command = ["/usr/bin/timeout", "--signal=KILL", str(timeout), NATIVE,
               "--cnf", str(cnf), "--sha256", "x", "--fallback", "cadical",
               *configuration.args()]
    start = time.perf_counter_ns()
    with log.open("wb") as stream:
        completed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, check=False,
                                   timeout=timeout + 15)
    elapsed = (time.perf_counter_ns() - start) / 1e6
    timed_out = completed.returncode in {124, 137}
    payload: dict[str, Any] = {}
    if not timed_out:
        lines = log.read_text(errors="replace").splitlines()
        found = next((x for x in reversed(lines) if x.startswith("{")), None)
        if found:
            payload = json.loads(found)
    status = payload.get("final_result", "TIMEOUT" if timed_out else "ERROR")
    return {"runtime_ms": elapsed, "status": status,
            "structural_result": payload.get("structural_result", "UNKNOWN"),
            "registry_status": payload.get("registry_status", ""),
            "family_checks": payload.get("family_checks", "")}


def score(result: dict[str, Any], timeout_ms: float) -> float:
    """PAR-2 on end-to-end runtime: the measure Phase 5 reports."""
    return result["runtime_ms"] if result["status"] in DEFINITE else 2 * timeout_ms


def race(configurations: Iterable[Configuration], instances: list[dict[str, Any]],
         timeout: float, logs: Path, batch: int = 6,
         drop_factor: float = 1.35) -> dict[str, Any]:
    """F-race: evaluate in batches, drop configurations that fall behind.

    A configuration survives a batch if its cumulative mean PAR-2 is within
    `drop_factor` of the best so far. The rule is deliberately crude -- with a
    handful of configurations a full statistical test buys nothing -- but it does
    stop the race spending its time on settings that are already losing.
    """
    alive = list(configurations)
    totals: dict[str, list[float]] = {c.key(): [] for c in alive}
    history: list[dict[str, Any]] = []
    timeout_ms = timeout * 1000
    for start in range(0, len(instances), batch):
        chunk = instances[start:start + batch]
        for configuration in alive:
            for item in chunk:
                log = logs / configuration.key().replace("=", "_") / f"{Path(item['path']).stem}.log"
                result = run(configuration, Path(item["path"]), timeout, log)
                totals[configuration.key()].append(score(result, timeout_ms))
                history.append({"configuration": configuration.key(),
                                "instance": item["instance"],
                                "family": item["family"], **result})
        means = {c.key(): statistics.mean(totals[c.key()]) for c in alive}
        best = min(means.values())
        survivors = [c for c in alive if means[c.key()] <= best * drop_factor]
        dropped = [c.key() for c in alive if c not in survivors]
        if dropped:
            history.append({"configuration": "RACE", "instance": f"after {start + len(chunk)}",
                            "family": "dropped", "status": ", ".join(dropped),
                            "runtime_ms": "", "structural_result": "",
                            "registry_status": "", "family_checks": ""})
        alive = survivors or alive
        if len(alive) == 1:
            break
    means = {c.key(): statistics.mean(totals[c.key()]) for c in alive}
    # Ties break toward the smaller budget: less work for the same result.
    winner = min(alive, key=lambda c: (means[c.key()], c.family_check_budget))
    return {"winner": winner, "means": means, "history": history,
            "evaluated": {k: len(v) for k, v in totals.items()}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("results/tuning"))
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--budgets", type=int, nargs="+",
                        default=[100_000, 200_000, 300_000, 500_000, 1_000_000])
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    instances = build_calibration_set(out / "cnfs")
    configurations = [Configuration(b) for b in sorted(set(args.budgets))]
    outcome = race(configurations, instances, args.timeout, out / "logs")

    from phase3.common import write_csv
    write_csv(out / "tuning_runs.csv", outcome["history"])
    payload = {
        "fitted_on": "generated calibration instances only; benchmark seeds excluded",
        "calibration_instances": len(instances),
        "calibration_seed_base": CALIBRATION_SEED_BASE,
        "candidates": [c.key() for c in configurations],
        "evaluations": outcome["evaluated"],
        "mean_par2_ms": outcome["means"],
        "family_check_budget": outcome["winner"].family_check_budget,
    }
    (out / "tuned_config.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
