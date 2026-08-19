"""Parallel execution for the stages where wall-clock timing is not the measure.

Phase 5 runs solvers sequentially with rotated order because PAR-2 is a timing
measure and contention perturbs it. That discipline is necessary for the timing
tables and wasteful everywhere else: the telemetry pass, the structural
classification and the answer-agreement checks care only about *what* each run
decided, not how long it took.

This module runs those stages across a worker pool. Every record it produces is
marked `timing_valid=False` so a downstream aggregate can never quietly treat a
contended runtime as a measurement.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Sequence


def worker_count(requested: int | None = None) -> int:
    """Leave headroom so the pool does not fight the host for every core."""
    available = os.cpu_count() or 2
    if requested and requested > 0:
        return max(1, min(requested, available))
    return max(1, min(8, available - 2))


def run_pool(items: Sequence[Any], work: Callable[[Any], dict[str, Any]],
             workers: int | None = None,
             on_result: Callable[[dict[str, Any], int, int], None] | None = None
             ) -> list[dict[str, Any]]:
    """Apply `work` to every item across a pool, preserving input order.

    Results are returned in the order of `items` regardless of completion order,
    so a rerun with the same input produces byte-identical CSVs.
    """
    count = worker_count(workers)
    results: list[dict[str, Any] | None] = [None] * len(items)
    done = 0
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = {pool.submit(work, item): index
                   for index, item in enumerate(items)}
        for future in as_completed(futures):
            index = futures[future]
            record = future.result()
            record["timing_valid"] = False
            record["pool_workers"] = count
            results[index] = record
            done += 1
            if on_result:
                on_result(record, done, len(items))
    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# Deriving the selector arm instead of measuring it
# ---------------------------------------------------------------------------

def derive_selector_rows(rows: Iterable[dict[str, Any]],
                         telemetry: dict[str, dict[str, Any]],
                         parameters: dict[str, int],
                         mode_name: str = "E_lkk_selector",
                         cadical_mode: str = "C_lkk_cadical",
                         kissat_mode: str = "D_lkk_kissat") -> list[dict[str, Any]]:
    """Compute the selector arm from the two fallback arms.

    The static selector is a deterministic function of features computed before
    the fallback solver starts (Section: selector). Its result on an instance is
    therefore whichever of the two measured arms it would have chosen -- running
    it again measures nothing new and costs a fifth of the campaign.

    The derived row copies the chosen arm's measurement and records both the
    choice and the fact that it was derived, so the provenance is never lost.
    """
    from phase5.selector import decide

    by_instance: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_instance.setdefault(row["instance"], {})[row["mode"]] = row

    derived: list[dict[str, Any]] = []
    for instance, arms in by_instance.items():
        chosen_source = arms.get(cadical_mode)
        other = arms.get(kissat_mode)
        if chosen_source is None or other is None:
            continue
        features = telemetry.get(instance, {})
        engine, reason = decide(features, parameters)
        source = other if engine == "kissat" else chosen_source
        record = dict(source)
        record.update({
            "mode": mode_name,
            "selector_choice": engine,
            "selector_reason": reason,
            "derived_from": source["mode"],
            "measured": False,
        })
        derived.append(record)
    return derived
