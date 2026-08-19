"""The static, structure-informed fallback selector.

The rule's *form* is frozen in `phase5/config.json` and implemented in
`native/phase5/lkk_native5.cpp` before any Phase 5 measurement is taken. Only
its numeric thresholds are fitted, and only ever against the calibration split.
`fit` refuses to look at anything else, and `evaluate` reports the regret and
the harmful/beneficial split that §8 requires.

Permitted features are structural facts the front-end computes before the
fallback solver starts. The instance name, source, family label, expected answer
and any previously measured runtime are not inputs and must never become inputs.
"""

from __future__ import annotations

import itertools
from typing import Any, Iterable

PERMITTED_FEATURES = ("boxes_found", "registry_status", "clauses",
                      "gate_monotone_positive", "gate_monotone_negative",
                      "gate_mirror_pairs")
FORBIDDEN_FEATURES = ("instance", "path", "domain", "set", "source", "stratum",
                      "declared_result", "expected_result", "runtime_ms", "par2_ms",
                      "status")


def decide(telemetry: dict[str, Any], parameters: dict[str, int]) -> tuple[str, str]:
    """Mirror of `choose_engine` in the native engine; kept in step by tests."""
    boxes = int(float(telemetry.get("boxes_found") or 0))
    clauses = int(float(telemetry.get("clauses") or 0))
    signal = (int(float(telemetry.get("gate_monotone_positive") or 0))
              + int(float(telemetry.get("gate_monotone_negative") or 0))
              + 2 * int(float(telemetry.get("gate_mirror_pairs") or 0)))
    registry_open = str(telemetry.get("registry_status", "")) != "COMPLETE"
    if boxes >= parameters["min_boxes"] and registry_open and signal >= parameters["min_signal"]:
        return "kissat", f"open_cardinality_structure(boxes={boxes})"
    if clauses >= parameters["min_clauses"]:
        return "kissat", f"large_formula(clauses={clauses})"
    return "cadical", f"default_in_process_cadical(boxes={boxes})"


def _par2(row: dict[str, Any], timeout_ms: float, multiplier: int = 2) -> float:
    status = str(row.get("status", ""))
    if status not in ("SATISFIABLE", "UNSATISFIABLE"):
        return timeout_ms * multiplier
    return float(row.get("runtime_ms", timeout_ms))


def evaluate(cases: Iterable[dict[str, Any]], parameters: dict[str, int],
             timeout_ms: float) -> dict[str, Any]:
    """Score a parameter set against per-instance CaDiCaL/Kissat fallback costs.

    `cases` carries the telemetry plus both measured fallback outcomes. Regret is
    the cost of the chosen engine minus the cost of the better one, so a perfect
    selector has zero regret.
    """
    total_regret = 0.0
    harmful = beneficial = correct = 0
    chosen_total = best_total = cadical_total = kissat_total = 0.0
    considered = 0
    for case in cases:
        cadical = _par2(case["cadical"], timeout_ms)
        kissat = _par2(case["kissat"], timeout_ms)
        engine, _ = decide(case["telemetry"], parameters)
        chosen = cadical if engine == "cadical" else kissat
        best, worst = min(cadical, kissat), max(cadical, kissat)
        considered += 1
        chosen_total += chosen
        best_total += best
        cadical_total += cadical
        kissat_total += kissat
        total_regret += chosen - best
        if cadical == kissat:
            correct += 1
        elif chosen == best:
            correct += 1
            beneficial += 1
        else:
            harmful += 1
    return {
        "instances": considered,
        "accuracy": correct / considered if considered else 0.0,
        "total_regret_ms": total_regret,
        "mean_regret_ms": total_regret / considered if considered else 0.0,
        "harmful_selections": harmful,
        "beneficial_selections": beneficial,
        "selector_total_par2_ms": chosen_total,
        "oracle_total_par2_ms": best_total,
        "always_cadical_total_par2_ms": cadical_total,
        "always_kissat_total_par2_ms": kissat_total,
        "parameters": dict(parameters),
    }


def fit(cases: list[dict[str, Any]], grid: dict[str, list[int]],
        timeout_ms: float) -> dict[str, Any]:
    """Pick thresholds minimising regret on the calibration split.

    Ties break toward the simplest rule (largest thresholds, i.e. least eager to
    leave the default in-process engine), so a parameter set only wins on
    evidence rather than on grid position.
    """
    for case in cases:
        leaked = sorted(set(case["telemetry"]) & set(FORBIDDEN_FEATURES))
        if leaked:
            raise ValueError(f"selector telemetry contains forbidden features: {leaked}")
    keys = sorted(grid)
    best: dict[str, Any] | None = None
    trials = []
    for combination in itertools.product(*(grid[k] for k in keys)):
        parameters = dict(zip(keys, combination))
        score = evaluate(cases, parameters, timeout_ms)
        trials.append(score)
        if best is None or (score["total_regret_ms"], -sum(parameters.values())) < (
                best["total_regret_ms"], -sum(best["parameters"].values())):
            best = score
    if best is None:
        raise ValueError("empty selector grid")
    return {"best": best, "trials": trials}
