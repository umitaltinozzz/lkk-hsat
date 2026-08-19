"""Re-render RESULTS.md from a completed run's CSVs.

The measurements are immutable; only the presentation is regenerated. Nothing
here re-runs a solver or recomputes a number that was measured.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from phase4b.report import render
from phase4b.run_phase4b import lemma_deltas, summarize_part_a


def rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def numeric(values: list[dict[str, Any]], *fields: str) -> list[dict[str, Any]]:
    for row in values:
        for field in fields:
            if row.get(field) not in ("", None):
                row[field] = float(row[field])
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    metadata = json.loads((run / "metadata.json").read_text(encoding="utf-8"))

    comparison = numeric(rows(run / "fallback_comparison.csv"), "runtime_ms", "par2_ms")
    ablation = numeric(rows(run / "lemma_ablation.csv"), "runtime_ms", "par2_ms")
    statistics_rows = rows(run / "cdcl_statistics.csv")
    verification = rows(run / "lemma_verification.csv")
    for row in verification:
        row["accepted"] = str(row.get("accepted")).lower() == "true"
    structural = rows(run / "structural_refutations.csv")

    summary = summarize_part_a(comparison)
    deltas = lemma_deltas(ablation, statistics_rows)
    limits = metadata.get("lemma_limits", {})
    counts = {
        "flow_cases": metadata.get("flow_cases", 0),
        "flow_mismatches": metadata.get("flow_mismatches", 0),
        "correctness_cases": metadata.get("correctness_cases", 0),
        "correctness_failures": metadata.get("correctness_failures", 0),
        "accepted_instances": metadata.get("accepted_instances", 0),
        "expanded_instances": metadata.get("expanded_instances", 0),
        "part_a_runs": metadata.get("part_a_runs", len(comparison)),
        "part_b_runs": metadata.get("part_b_runs", len(ablation)),
        "harvest_check_budget": limits.get("harvest_check_budget"),
        "lemma_verification_timeout": metadata.get("lemma_verification_timeout_seconds", 5),
    }
    (run / "RESULTS.md").write_text(
        render(metadata["run_id"], summary, verification, deltas, structural, counts),
        encoding="utf-8",
    )
    print(json.dumps({"run": run.name, "part_a_summary_rows": len(summary),
                      "delta_rows": len(deltas), "verification_rows": len(verification)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
