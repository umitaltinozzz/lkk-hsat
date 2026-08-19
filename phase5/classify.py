"""Stratify the corpus from LKK telemetry alone.

The class of an instance is decided by what the structural front-end actually
reported on it, never by its file name, its source directory, or its answer.
Provenance domain is carried alongside as separate metadata so the two can be
cross-tabulated without one contaminating the other.
"""

from __future__ import annotations

from typing import Any

# The §3 strata, in priority order: the first predicate that matches wins, so
# the classes are mutually exclusive and every instance lands in exactly one.
STRATA = (
    "lkk_direct_structural_unsat",
    "structural_not_closed_by_flow",
    "hidden_cardinality_recovered",
    "cardinality_exact_r_heavy",
    "resource_allocation_like",
    "matching_hall_like",
    "no_useful_detected_structure",
    "unknown_fallback_dominated",
)


def _int(row: dict[str, Any], key: str, default: int = 0) -> int:
    value = row.get(key, default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def classify(telemetry: dict[str, Any]) -> tuple[str, str]:
    """Return (stratum, why) for one instance's telemetry record."""
    gate = str(telemetry.get("gate_decision", ""))
    recovery = str(telemetry.get("recovery_status", ""))
    registry = str(telemetry.get("registry_status", ""))
    registry_reason = str(telemetry.get("registry_reason", ""))
    structural = str(telemetry.get("structural_result", ""))
    boxes = _int(telemetry, "boxes_found")
    groups = _int(telemetry, "capacity_groups")
    derived = _int(telemetry, "recovery_derived")
    mirror = _int(telemetry, "gate_mirror_pairs")

    if structural == "UNSAT":
        return "lkk_direct_structural_unsat", "FlowBoxTest refuted the instance"
    if gate != "RUN_LKK":
        return "no_useful_detected_structure", f"cheap gate declined: {telemetry.get('gate_reason')}"
    if str(telemetry.get("benefit_gate_decision", "")) == "SKIP_TO_CDCL":
        return "no_useful_detected_structure", f"benefit gate declined: {telemetry.get('benefit_gate_reason')}"
    if registry == "COMPLETE" and structural == "FEASIBLE":
        return ("structural_not_closed_by_flow",
                "registry closed but max-flow found the model feasible")
    if boxes > 0 and groups > 0:
        return ("resource_allocation_like",
                f"{boxes} exact-r boxes over {groups} capacity groups")
    if boxes > 0 and mirror > 0 and derived > 0:
        return ("hidden_cardinality_recovered",
                f"{boxes} boxes recovered behind {mirror} mirror pairs")
    if boxes > 0 and "family_check_budget" in registry_reason:
        return ("matching_hall_like",
                f"{boxes} boxes found, registry abandoned at the family-check budget")
    if boxes > 0:
        return "cardinality_exact_r_heavy", f"{boxes} exact-r boxes, registry {registry}"
    if mirror > 0 and derived > 0:
        return ("hidden_cardinality_recovered",
                f"{mirror} mirror pairs and {derived} resolvents, no closed box")
    if recovery == "COMPLETE" and registry == "UNKNOWN":
        return ("unknown_fallback_dominated",
                f"recovery completed but registry returned UNKNOWN: {registry_reason}")
    return ("unknown_fallback_dominated",
            f"recovery {recovery}, registry {registry}")


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Counts per stratum, and how each stratum maps onto provenance domains."""
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        stratum = row.get("stratum", "unclassified")
        domain = row.get("domain", "unknown")
        bucket = counts.setdefault(stratum, {})
        bucket[domain] = bucket.get(domain, 0) + 1
    out = []
    for stratum in [*STRATA, *sorted(set(counts) - set(STRATA))]:
        if stratum not in counts:
            continue
        bucket = counts[stratum]
        out.append({
            "stratum": stratum,
            "instances": sum(bucket.values()),
            "domains": "; ".join(f"{k}={v}" for k, v in sorted(bucket.items())),
        })
    return out
