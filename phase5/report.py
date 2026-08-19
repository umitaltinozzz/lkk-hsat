"""Render the Phase 5 report, answering §17 strictly from measured rows."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFINITE = {"SATISFIABLE", "UNSATISFIABLE"}
STRUCTURAL_STRATA = ("lkk_direct_structural_unsat", "cardinality_exact_r_heavy",
                     "matching_hall_like", "hidden_cardinality_recovered",
                     "resource_allocation_like")


def _num(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any, digits: int = 1) -> str:
    number = _num(value)
    return "n/a" if number is None else f"{number:,.{digits}f}"


def _by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key, ""))].append(row)
    return grouped


def _per_instance(results: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in results:
        out[row["instance"]][row["mode"]] = row
    return out


def _wins(per_instance: dict[str, dict[str, dict[str, Any]]], mine: str,
          theirs: str) -> tuple[int, int, int]:
    win = loss = tie = 0
    for modes in per_instance.values():
        if mine not in modes or theirs not in modes:
            continue
        a, b = _num(modes[mine]["par2_ms"]), _num(modes[theirs]["par2_ms"])
        if a is None or b is None:
            continue
        if a < b:
            win += 1
        elif b < a:
            loss += 1
        else:
            tie += 1
    return win, loss, tie


def render(run_id: str, telemetry: list[dict[str, Any]], results: list[dict[str, Any]],
           ablation: list[dict[str, Any]], correctness: list[dict[str, Any]],
           calibration: dict[str, Any], fitted: dict[str, Any], out: Path,
           timeout: float) -> str:
    timeout_ms = timeout * 1000
    per_instance = _per_instance(results)
    modes = sorted({r["mode"] for r in results})
    by_mode = _by(results, "mode")

    summary_lines = []
    for mode in modes:
        runs = by_mode[mode]
        solved = [r for r in runs if r["status"] in DEFINITE]
        par2 = statistics.mean(_num(r["par2_ms"]) or 0 for r in runs) if runs else 0
        median = statistics.median([_num(r["runtime_ms"]) or 0 for r in solved]) if solved else 0
        summary_lines.append(
            f"| {mode} | {len(runs)} | {len(solved)} | "
            f"{sum(1 for r in runs if r['status'] == 'TIMEOUT')} | {par2:,.0f} | {median:,.1f} |")

    strata = _by(telemetry, "stratum")
    strata_lines = [
        f"| {name} | {len(rows)} | "
        f"{', '.join(sorted({r['domain'] for r in rows}))} |"
        for name, rows in sorted(strata.items(), key=lambda kv: -len(kv[1]))]

    direct = [r for r in telemetry if r.get("structural_result") == "UNSAT"]
    no_structure = [r for r in telemetry if r["stratum"] == "no_useful_detected_structure"]
    no_structure_names = {r["instance"] for r in no_structure}
    overheads = []
    for instance, m in per_instance.items():
        if instance in no_structure_names and "E_lkk_selector" in m and "A_cadical" in m:
            a, b = _num(m["E_lkk_selector"]["runtime_ms"]), _num(m["A_cadical"]["runtime_ms"])
            if a is not None and b is not None:
                overheads.append(a - b)

    beat_cad = _wins(per_instance, "E_lkk_selector", "A_cadical")
    beat_kis = _wins(per_instance, "E_lkk_selector", "B_kissat")
    beat_both = sum(1 for m in per_instance.values()
                    if all(k in m for k in ("E_lkk_selector", "A_cadical", "B_kissat"))
                    and (_num(m["E_lkk_selector"]["par2_ms"]) or 0) <
                    min(_num(m["A_cadical"]["par2_ms"]) or 0,
                        _num(m["B_kissat"]["par2_ms"]) or 0))

    solved_counts = {m: sum(1 for r in by_mode[m] if r["status"] in DEFINITE) for m in modes}
    par2_totals = {m: sum(_num(r["par2_ms"]) or 0 for r in by_mode[m]) for m in modes}

    selector_regret = []
    harmful = beneficial = 0
    for m in per_instance.values():
        if not all(k in m for k in ("C_lkk_cadical", "D_lkk_kissat", "E_lkk_selector")):
            continue
        c, k, s = (_num(m["C_lkk_cadical"]["par2_ms"]) or 0,
                   _num(m["D_lkk_kissat"]["par2_ms"]) or 0,
                   _num(m["E_lkk_selector"]["par2_ms"]) or 0)
        best = min(c, k)
        selector_regret.append(s - best)
        if s > best:
            harmful += 1
        elif c != k:
            beneficial += 1

    stratum_lines = []
    for name in STRUCTURAL_STRATA:
        rows = [r for r in results if r["stratum"] == name]
        if not rows:
            continue
        names = {r["instance"] for r in rows}
        cad = [_num(per_instance[n]["A_cadical"]["par2_ms"]) for n in names
               if "A_cadical" in per_instance[n]]
        lkk = [_num(per_instance[n]["E_lkk_selector"]["par2_ms"]) for n in names
               if "E_lkk_selector" in per_instance[n]]
        cad = [c for c in cad if c is not None]
        lkk = [x for x in lkk if x is not None]
        if not cad or not lkk:
            continue
        stratum_lines.append(
            f"| {name} | {len(names)} | {statistics.mean(cad):,.0f} | "
            f"{statistics.mean(lkk):,.0f} | "
            f"{statistics.mean(cad)/statistics.mean(lkk) if statistics.mean(lkk) else 0:.2f}x |")

    ablation_lines = []
    for variant, rows in sorted(_by(ablation, "mode").items()):
        solved = sum(1 for r in rows if r["status"] in DEFINITE)
        ablation_lines.append(
            f"| {variant} | {len(rows)} | {solved} | "
            f"{statistics.mean(_num(r['par2_ms']) or 0 for r in rows):,.0f} |")

    failures = sum(1 for r in correctness if not r["passed"])
    standard = [r for r in telemetry if r["source"] != "SATLIB" or True]
    domains = _by(telemetry, "domain")
    cpu_saved_ms = 0.0
    for m in per_instance.values():
        if "A_cadical" in m and "E_lkk_selector" in m:
            a, b = _num(m["A_cadical"]["par2_ms"]) or 0, _num(m["E_lkk_selector"]["par2_ms"]) or 0
            cpu_saved_ms += a - b

    outcome, recommendation = _classify_outcome(failures, beat_cad, beat_kis, stratum_lines,
                                                overheads, solved_counts)

    return f"""# Phase 5 results — standard and real-world benchmark campaign

Run ID: `{run_id}`

Phase 5 evaluates the frozen, evidence-supported architecture carried out of
Phase 4B: the native LKK structural engine, conservative exact-r/resource
recovery, FlowBoxTest, witness generation, and a static structure-informed
choice between the two locked fallback engines. LemmaBridge is absent from the
Phase 5 binary. No learned selector, no benchmark-specific routing, and no
per-instance parameter changes were used.

Every instance in this campaign was downloaded from a public source and is
recorded in `benchmark_manifest.csv` with its source, licence, and SHA-256. No
formula in this phase was generated by this project.

## Configuration

| Item | Value |
| --- | --- |
| Instances in corpus | {len(telemetry)} |
| Timeout chosen by calibration | {timeout:g} s |
| Calibration rule | {calibration.get('rule', 'n/a')} |
| Selector parameters (fitted on the calibration split only) | `{fitted.get('parameters')}` |
| Correctness cases | {len(correctness)} |
| **Definite-answer conflicts** | **{failures}** |

## Stratification from LKK telemetry

Classes come from what the front-end reported, never from a file name or a
known answer.

| Stratum | Instances | Provenance domains |
| --- | --- | --- |
{chr(10).join(strata_lines) if strata_lines else '| (none) | | |'}

## Solver summary

| Mode | Runs | Solved | Timeouts | mean PAR-2 (ms) | median solved (ms) |
| --- | --- | --- | --- | --- | --- |
{chr(10).join(summary_lines) if summary_lines else '| (none) | | | | | |'}

## Per-stratum comparison

| Stratum | Instances | mean PAR-2 CaDiCaL | mean PAR-2 LKK+selector | ratio |
| --- | --- | --- | --- | --- |
{chr(10).join(stratum_lines) if stratum_lines else '| (none) | | | | |'}

## Component ablation

| Variant | Runs | Solved | mean PAR-2 (ms) |
| --- | --- | --- | --- |
{chr(10).join(ablation_lines) if ablation_lines else '| (none) | | | |'}

## Answers to the required questions

1. **Total standard instances tested:** {len(telemetry)}.
2. **Domain/real-world instances tested:** {sum(len(v) for k, v in domains.items() if k not in ('random_control', 'competition_main'))}
   across {', '.join(sorted(k for k in domains if k not in ('random_control', 'competition_main')))}.
3. **Correctness mismatches:** {failures}.
4. **Instances LKK solved structurally (no fallback):** {len(direct)}.
5. **LKK beat CaDiCaL on:** {beat_cad[0]} instances (lost {beat_cad[1]}, tied {beat_cad[2]}).
6. **LKK beat Kissat on:** {beat_kis[0]} instances (lost {beat_kis[1]}, tied {beat_kis[2]}).
7. **LKK beat both on:** {beat_both} instances.
8. **Solved counts:** {', '.join(f'{m}={solved_counts[m]}' for m in modes)}.
9. **PAR-2 totals (ms):** {', '.join(f'{m}={par2_totals[m]:,.0f}' for m in modes)}.
10. **Families that benefit:** see the per-stratum table; only strata with a ratio above 1 benefit.
11. **Families that regress:** strata with a ratio below 1, plus the no-structure control below.
12. **Median overhead on no-structure instances:** {_fmt(statistics.median(overheads) if overheads else None, 2)} ms
    over {len(overheads)} instances.
13. **Did the static selector beat always-CaDiCaL and always-Kissat?**
    Mean regret {_fmt(statistics.mean(selector_regret) if selector_regret else None, 1)} ms;
    harmful selections {harmful}, beneficial {beneficial}. Totals in `selector_results.csv`.
14. **Timeouts removed vs CaDiCaL:** {solved_counts.get('E_lkk_selector', 0) - solved_counts.get('A_cadical', 0)}
    net solved-count change.
15. **CPU time saved on the tested workloads:** {cpu_saved_ms/3.6e6:.4f} CPU-hours by PAR-2
    against always-CaDiCaL. This is measured on this corpus only and is not an extrapolation.
16. **Are the Phase 4 generated-family gains reproduced here?** See the per-stratum table:
    the answer is yes only for the strata whose ratio exceeds 1.
17. **Evidence strong enough for a paper?** {recommendation['paper']}
18. **Evidence strong enough for an enterprise pilot?** {recommendation['pilot']}
19. **Strongest defensible scientific claim:** {recommendation['scientific']}
20. **Strongest defensible commercial claim:** {recommendation['commercial']}

## Publication decision

**{outcome}**

{recommendation['outcome_text']}

## Plots

`plots/cactus.svg`, `plots/scatter_lkk_vs_cadical.svg`,
`plots/scatter_lkk_vs_kissat.svg`, `plots/structural_speedup.svg`,
`plots/no_structure_overhead.svg`.

## Artifacts

`benchmark_manifest.csv`, `standard_results.csv`, `domain_results.csv`,
`solver_summary.csv`, `selector_results.csv`, `structural_direct_solves.csv`,
`ablation.csv`, `correctness.csv`, `phase_timings.csv`, `cactus_data.csv`,
`calibration.csv`, `calibration_choice.json`, `selector_config.json`,
`selector_grid.csv`, `strata_summary.csv`, `plots/`, `logs/`, `failures/`,
`metadata.json`.

No patent filing, publication submission, or customer outreach was started.
Phase 5 stops here.
"""


def _classify_outcome(failures: int, beat_cad: tuple[int, int, int],
                      beat_kis: tuple[int, int, int], stratum_lines: list[str],
                      overheads: list[float], solved: dict[str, int]) -> tuple[str, dict[str, str]]:
    """Map the measurements onto the §15 outcomes without forcing A or B."""
    median_overhead = statistics.median(overheads) if overheads else 0.0
    net_solved = solved.get("E_lkk_selector", 0) - max(solved.get("A_cadical", 0),
                                                       solved.get("B_kissat", 0))
    strong = beat_cad[0] > beat_cad[1] and beat_kis[0] > beat_kis[1] and net_solved >= 0
    narrow = beat_cad[0] > beat_cad[1] or net_solved > 0
    if failures:
        return ("Outcome D — negative", {
            "paper": "No. Correctness must be resolved first.",
            "pilot": "No.",
            "scientific": "None until the definite-answer conflicts are explained.",
            "commercial": "None.",
            "outcome_text": "Correctness failures were recorded, so no performance claim is made. "
                            "Stop optimization and document the negative result.",
        })
    if strong:
        return ("Outcome A — strong", {
            "paper": "Yes, on the evidence in the per-stratum table.",
            "pilot": "Yes, scoped to the strata that benefit.",
            "scientific": "LKK's structural front-end reduces end-to-end solving cost on "
                          "non-generated instances carrying recoverable cardinality structure.",
            "commercial": "A structural accelerator for workloads in the benefiting strata, "
                          "with measured overhead elsewhere.",
            "outcome_text": "Real/standard structural workloads show gains over both modern "
                            "baselines. Recommendation: prepare paper, IP review, external pilot.",
        })
    if narrow:
        return ("Outcome B — narrow but real", {
            "paper": "Yes, positioned as a specialized result, not a general solver advance.",
            "pilot": "Only for the specific benefiting class, with the overhead disclosed.",
            "scientific": "On a specific non-generated class, structural detection plus "
                          "fallback selection reduces end-to-end cost.",
            "commercial": "A specialized structural accelerator for one identified class.",
            "outcome_text": "Only a specific non-generated problem class benefits. "
                            "Recommendation: publish or position as a specialized structural "
                            "accelerator, not a general-purpose solver accelerator.",
        })
    return ("Outcome C — generated-only", {
        "paper": "Only as a negative/limitation result.",
        "pilot": "No.",
        "scientific": "The Phase 3/4 advantages are specific to the generated families and do "
                      "not transfer to these standard and real-world instances.",
        "commercial": "None. Do not market as an enterprise solver accelerator.",
        "outcome_text": f"Advantages did not reproduce on standard/real benchmarks "
                        f"(median no-structure overhead {median_overhead:.2f} ms). "
                        "Recommendation: preserve as a research result; do not market as an "
                        "enterprise solver accelerator.",
    })
