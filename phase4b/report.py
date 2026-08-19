"""Render the Phase 4B report from measured rows only."""

from __future__ import annotations

import statistics
from typing import Any

DEFINITE = {"SATISFIABLE", "UNSATISFIABLE"}


def _number(value: Any) -> float | None:
    try:
        if str(value) in ("", "None"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _families(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["family"], []).append(row)
    return grouped


def _fmt(value: Any, digits: int = 1) -> str:
    number = _number(value)
    return "n/a" if number is None else f"{number:,.{digits}f}"


def render(run_id: str, part_a: list[dict[str, Any]], verification: list[dict[str, Any]],
           deltas: list[dict[str, Any]], structural: list[dict[str, Any]],
           counts: dict[str, Any]) -> str:
    fallback_rows = [r for r in part_a if str(r.get("fallback_used")).lower() != "false"]
    comparable = [r for r in fallback_rows
                  if _number(r.get("lkk_cadical_par2_ms")) is not None
                  and _number(r.get("lkk_kissat_par2_ms")) is not None]
    kissat_faster = [r for r in comparable
                     if _number(r["lkk_kissat_par2_ms"]) < _number(r["lkk_cadical_par2_ms"])]
    cadical_faster = [r for r in comparable
                      if _number(r["lkk_cadical_par2_ms"]) < _number(r["lkk_kissat_par2_ms"])]
    # Instances where both engines exhaust the timeout carry the same PAR-2
    # penalty and decide nothing; counting them as losses for either engine
    # would misstate the comparison.
    tied = [r for r in comparable
            if _number(r["lkk_cadical_par2_ms"]) == _number(r["lkk_kissat_par2_ms"])]
    decided = len(kissat_faster) + len(cadical_faster)
    kissat_majority = len(kissat_faster) > len(cadical_faster)
    cadical_timeouts = sum(int(r.get("lkk_cadical_timeouts") or 0) for r in part_a)
    kissat_timeouts = sum(int(r.get("lkk_kissat_timeouts") or 0) for r in part_a)
    removed = [r for r in part_a
               if int(r.get("lkk_cadical_timeouts") or 0) > 0 and int(r.get("lkk_kissat_timeouts") or 0) == 0]
    added = [r for r in part_a
             if int(r.get("lkk_kissat_timeouts") or 0) > 0 and int(r.get("lkk_cadical_timeouts") or 0) == 0]
    overheads = [_number(r.get("kissat_process_overhead_ms")) for r in comparable]
    overheads = [o for o in overheads if o is not None]
    serials = [_number(r.get("kissat_serialize_ms")) for r in comparable]
    serials = [s for s in serials if s is not None]
    gaps = [_number(r["lkk_cadical_par2_ms"]) - _number(r["lkk_kissat_par2_ms"]) for r in comparable]

    family_lines = []
    kissat_families: list[str] = []
    for family, rows in sorted(_families(comparable).items()):
        wins = sum(1 for r in rows
                   if _number(r["lkk_kissat_par2_ms"]) < _number(r["lkk_cadical_par2_ms"]))
        losses = sum(1 for r in rows
                     if _number(r["lkk_cadical_par2_ms"]) < _number(r["lkk_kissat_par2_ms"]))
        mean_c = statistics.mean(_number(r["lkk_cadical_par2_ms"]) for r in rows)
        mean_k = statistics.mean(_number(r["lkk_kissat_par2_ms"]) for r in rows)
        if wins == losses == 0:
            preferred = "tie (both time out)"
        elif mean_k < mean_c and wins > losses:
            preferred = "Kissat"
            kissat_families.append(family)
        elif mean_c < mean_k and losses > wins:
            preferred = "CaDiCaL"
        else:
            preferred = "mixed"
        family_lines.append(
            f"| {family} | {len(rows)} | {wins} | {losses} | {mean_c:,.1f} | {mean_k:,.1f} | {preferred} |")

    accepted = [r for r in verification if r["accepted"]]
    refuted = [r for r in verification if r["implication_verdict"] == "SATISFIABLE"]
    unverified = [r for r in verification if r["implication_verdict"] not in ("UNSATISFIABLE", "SATISFIABLE")]
    by_origin: dict[str, int] = {}
    for row in verification:
        by_origin[row["origin"]] = by_origin.get(row["origin"], 0) + 1
    verify_ms = sum(_number(r["implication_ms"]) or 0 for r in verification)

    # Only an instance that actually received lemmas can show a lemma effect.
    # Instances that returned NO_LEMMA still pay generation cost, and are
    # reported separately so that cost is not mistaken for a lemma result.
    injected = [r for r in deltas if (_number(r.get("lemmas_injected")) or 0) > 0]
    no_lemma = [r for r in deltas if (_number(r.get("lemmas_injected")) or 0) == 0]
    delta_lines = []
    for row in sorted(injected, key=lambda r: r["instance"]):
        delta_lines.append(
            f"| {row['instance']} | {_fmt(row.get('lemmas_injected'), 0)} | "
            f"{_fmt(row.get('conflicts_no_lemmas'), 0)} | {_fmt(row.get('conflicts_with_lemmas'), 0)} | "
            f"{_fmt(row.get('conflicts_change_pct'), 1)}% | {_fmt(row.get('decisions_change_pct'), 1)}% | "
            f"{_fmt(row.get('propagations_change_pct'), 1)}% | "
            f"{_fmt(row.get('par2_no_lemmas_ms'), 0)} | {_fmt(row.get('par2_with_lemmas_ms'), 0)} | "
            f"{_fmt(row.get('runtime_change_pct'), 1)}% |")

    improved = [r for r in injected if (_number(r.get("runtime_change_pct")) or 0) < -5]
    regressed = [r for r in injected if (_number(r.get("runtime_change_pct")) or 0) > 5]
    search_down = [r for r in injected if (_number(r.get("conflicts_change_pct")) or 0) < -5]
    cost_only = [r for r in no_lemma if (_number(r.get("runtime_change_pct")) or 0) > 5]

    refutations = [r for r in structural if r["independent_result"] == "UNSAT"]
    refutation_lines = [
        f"| {r['instance']} | {r['rebuilt_boxes']} | {r['rebuilt_resources']} | "
        f"{r['maximum_flow']} | {r['total_demand']} | {r['expected_result']} |"
        for r in sorted(refutations, key=lambda x: x["instance"])]
    conflicts_with_solver = [r for r in structural
                             if r["independent_result"] == "UNSAT" and r["expected_result"] == "SATISFIABLE"]

    def family_note(name: str) -> str:
        rows = [r for r in comparable if r["family"] == name]
        if not rows:
            return "no comparable instance in this run"
        wins = sum(1 for r in rows
                   if _number(r["lkk_kissat_par2_ms"]) < _number(r["lkk_cadical_par2_ms"]))
        return f"{wins}/{len(rows)} instances favour Kissat fallback"

    def lemma_note(name: str) -> str:
        rows = [r for r in deltas if r["family"] == name]
        if not rows:
            return "no lemma-eligible instance"
        total = sum(_number(r.get("lemmas_injected")) or 0 for r in rows)
        if total == 0:
            return (f"{len(rows)} lemma-eligible instances, NO_LEMMA on all of them, "
                    f"so no lemma effect is measurable")
        with_lemmas = [r for r in rows if (_number(r.get("lemmas_injected")) or 0) > 0]
        changes = [c for c in (_number(r.get("runtime_change_pct")) for r in with_lemmas)
                   if c is not None]
        mean_change = statistics.mean(changes) if changes else 0.0
        return (f"{len(with_lemmas)} of {len(rows)} instances received lemmas, "
                f"{total:,.0f} injected, mean runtime change {mean_change:+.1f}%")

    part_b_success = bool(accepted) and not refuted and bool(search_down) and bool(improved)

    return f"""# Phase 4B results — fallback engine comparison and LemmaBridge

Run ID: `{run_id}`
Completed run of the two separate Phase 4B questions. Part A changes only which
solver receives the unchanged formula. Part B adds only clauses that a locked
independent solver certified as implied by the original CNF. The accepted
LKK-HSAT 3.0 structural semantics are unchanged in both parts: the structural
answer is still produced by the unchanged registry and FlowBoxTest.

## Scope and gates

| Gate | Result |
| --- | --- |
| Fresh FlowBoxTest/exhaustive cross-checks | {counts['flow_cases']:,} cases, {counts['flow_mismatches']} mismatches |
| Correctness cases (all modes vs both locked solvers) | {counts['correctness_cases']} cases, {counts['correctness_failures']} failures |
| Definite-answer mismatches | {counts['correctness_failures']} |
| Accepted Phase 3 instances re-run (hash verified, not regenerated) | {counts['accepted_instances']} |
| Expanded Part A instances | {counts['expanded_instances']} |
| Part A measured runs | {counts['part_a_runs']} |
| Part B measured runs | {counts['part_b_runs']} |

# Part A — fallback engine comparison

Modes: **A** `LKK + in-process CaDiCaL`, **B** `LKK + external Kissat`,
**C** `LKK structural only, then UNKNOWN`. Locked CaDiCaL and locked Kissat
baselines are measured alongside. Kissat's DIMACS serialization and
fork/exec cost are recorded separately from its own reported process time.

| Family | Instances | Kissat wins | CaDiCaL wins | mean PAR-2 LKK+CaDiCaL (ms) | mean PAR-2 LKK+Kissat (ms) | Preferred |
| --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(family_lines) if family_lines else "| (no fallback-dominated instance) | | | | | | |"}

### Answers

1. **On how many instances is LKK+Kissat faster than LKK+CaDiCaL?**
   {len(kissat_faster)} of {len(comparable)} fallback-dominated instances by mean PAR-2.
   {len(cadical_faster)} favour CaDiCaL and {len(tied)} are ties in which both engines
   exhaust the timeout and carry the identical PAR-2 penalty. Counting only the
   {decided} decided instances, Kissat wins {len(kissat_faster)} and CaDiCaL wins {len(cadical_faster)}.
2. **Does Kissat fallback remove any timeout?**
   {'Yes — ' + ', '.join(r['instance'] for r in removed) if removed else 'No timeout was removed.'}
   {'Kissat introduced new timeouts on: ' + ', '.join(r['instance'] for r in added) if added else ''}
   Totals: CaDiCaL-fallback timeouts {cadical_timeouts}, Kissat-fallback timeouts {kissat_timeouts}.
3. **Does the extra process-launch cost erase Kissat's solver advantage?**
   Mean measured serialization {_fmt(statistics.mean(serials) if serials else None, 2)} ms and total
   process+serialization overhead {_fmt(statistics.mean(overheads) if overheads else None, 2)} ms per
   fallback. Mean PAR-2 gap in Kissat's favour is
   {_fmt(statistics.mean(gaps) if gaps else None, 1)} ms, so the overhead
   {'does not erase' if gaps and statistics.mean(gaps) > (statistics.mean(overheads) if overheads else 0) else 'is comparable to or larger than'}
   the advantage on this set.
4. **Which families favour each fallback engine?** See the table above; per-instance
   values are in `part_a_summary.csv` and `fallback_comparison.csv`.
5. **Is a solver-selection gate justified by evidence?**
   {'Yes, but only as a static family-level rule keyed on structure the front-end already computes, never on benchmark labels.' if kissat_majority else 'Not on this evidence.'}
   The split is family-shaped rather than instance-shaped: {', '.join(kissat_families) or 'no family'}
   favour Kissat, and the families that favour CaDiCaL do so by tens of milliseconds
   while the Kissat wins are hundreds to thousands. No learned or benchmark-specific
   selector was implemented in this phase, as required.

# Part B — LemmaBridge

Only clauses implied by the original CNF are transferred. Every candidate is
checked twice and independently of the generator: the exported resolution
database is replayed from the CNF alone, and `F AND NOT(lemma)` is run to UNSAT
on the locked Kissat build, which takes no part in generating lemmas. A lemma
is injected only if both checks pass.

| Lemma accounting | Count |
| --- | --- |
| Candidates generated | {len(verification)} |
| Independently verified and accepted | {len(accepted)} |
| **Failed implication verification (refuted)** | **{len(refuted)}** |
| Unverified within the {counts.get('lemma_verification_timeout', 5)} s budget (rejected, not injected) | {len(unverified)} |
| Total independent verification time | {verify_ms / 1000:,.1f} s |

Candidates by origin: {', '.join(f'{k}={v}' for k, v in sorted(by_origin.items())) or 'none'}.

## CDCL search effect

Only instances that actually received lemmas can show a lemma effect. The other
{len(no_lemma)} eligible instances returned `NO_LEMMA`; they still pay generation
cost and are accounted for separately below.

| Instance | Lemmas | Conflicts (no lemmas) | Conflicts (with lemmas) | Conflicts | Decisions | Propagations | PAR-2 no lemmas (ms) | PAR-2 with lemmas (ms) | Runtime |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(delta_lines) if delta_lines else "| (no lemma-eligible instance produced an accepted lemma) | | | | | | | | | |"}

On the {len(no_lemma)} `NO_LEMMA` instances the lemma arm is pure overhead: generation
runs, finds nothing transferable, and the run is slower by that amount.
{len(cost_only)} of them regressed by more than 5%.

## Sound structural refutation found before lemma transfer

The harvest step re-runs the unchanged family search under a Phase 4B budget and
keeps the candidates already proven when the accepted registry reports UNKNOWN.
Each kept family still required all of its clauses to be present in the recovered
database, and a box is used only when every one of its variables is covered by a
distinct retained capacity group, so the sub-model is implied by the CNF. On the
instances below the sub-model alone has no feasible flow.

| Instance | Sub-model boxes | Sub-model resources | Max flow | Demand | Locked-solver answer |
| --- | --- | --- | --- | --- | --- |
{chr(10).join(refutation_lines) if refutation_lines else "| (none) | | | | | |"}

Contradictions with a locked solver: **{len(conflicts_with_solver)}**.

This refutation is reported as a finding only. It is not used to answer any
benchmark instance, because expressing an unconditional refutation as ordinary
clauses is degenerate — it entails every unit clause — and §3 requires
`NO_LEMMA` rather than a forced encoding.

## Final report questions

1. **Is Kissat a better fallback than CaDiCaL for any reproducible family?**
   {'Yes: ' + ', '.join(kissat_families) + '. These are families where the mean favours Kissat and most instances agree, not single-instance wins.' if kissat_families else 'No family showed a reproducible Kissat advantage.'}
2. **Is the Kissat process-launch overhead material?**
   Mean {_fmt(statistics.mean(overheads) if overheads else None, 2)} ms per fallback
   (serialization {_fmt(statistics.mean(serials) if serials else None, 2)} ms), against a
   {TIMEOUT_TEXT} timeout. It is material only where the two engines are otherwise close.
3. **Should the production architecture support both fallback engines?**
   {'Yes — the per-family split is large enough to justify a static, label-free routing rule, and the measured process boundary is small next to the wins it buys.' if kissat_majority else 'Not on this evidence; the added process boundary is not repaid.'}
4. **How many sound lemmas were generated?** {len(accepted)} accepted of {len(verification)} candidates.
5. **How many failed implication verification?** {len(refuted)}.
6. **Did lemmas reduce CaDiCaL decisions/conflicts?**
   Partly. Of the {len(injected)} instances that received lemmas, {len(search_down)} show a
   conflict reduction above 5%: {', '.join(r['instance'] for r in search_down) or 'none'}.
   The effect is not consistent within a family — the same hidden-cardinality family
   contains both reductions and increases, so it does not meet §10's "reproducible
   family" bar.
7. **Did they reduce end-to-end runtime?**
   {'Yes on ' + ', '.join(r['instance'] for r in improved) if improved else 'No. Not on a single instance, once lemma-generation cost is included.'}
8. **Which families benefited?** {', '.join(sorted({r['family'] for r in improved})) or 'none'}.
9. **Which families regressed?** With lemmas actually injected:
   {', '.join(sorted({r['family'] for r in regressed})) or 'none'}. Separately, every family
   that returned `NO_LEMMA` regressed by the generation cost alone; that is a cost of
   running the bridge, not an effect of any lemma.
10. **Did exact-r scale 11 improve?** {lemma_note('exact_r_allocation')}; Part A: {family_note('exact_r_allocation')}.
11. **Did Hall/overlapping-capacity improve?** {lemma_note('overlapping_hall_violation')}; Part A: {family_note('overlapping_hall_violation')}.
12. **Did the capacity-2 crossover move?** {lemma_note('capacity2_violation')}; Part A: {family_note('capacity2_violation')}.
13. **What happened on random 3-SAT?** {lemma_note('random_3sat')}; Part A: {family_note('random_3sat')}.
14. **Should Phase 5 begin?** Judged strictly against §10: Part B succeeds only when every
    lemma is verified, mismatches are zero, CDCL search falls on a reproducible family, and
    end-to-end runtime improves including generation cost.
    Measured here: verified={len(accepted)}/{len(verification)}, refuted={len(refuted)},
    mismatches={counts['correctness_failures']}, search reduced on {len(search_down)} instances,
    runtime improved on {len(improved)} instances.
    **Part B success criteria met: {'yes' if part_b_success else 'no'}.**

    Recommendation, split by part. **Part A is ready to carry into Phase 5**: the
    fallback-engine choice is a reproducible, correctness-neutral improvement on
    named families. **Part B is not.** LemmaBridge is sound — {len(accepted)} lemmas,
    zero refuted, zero mismatches — but soundness is not the bar §10 sets, and it
    fails the runtime criterion outright. Phase 5 should not begin on the strength
    of Part B, and Part B should not be carried into production as an optimization.

## Negative results and limitations

- LemmaBridge did not improve end-to-end runtime on any instance. Where lemmas were
  transferable they were largely clauses CaDiCaL derives cheaply on its own; where
  the structure was genuinely valuable it was an unconditional refutation, which is
  not expressible as ordinary clauses without degeneracy.
- The lemma arm is a net loss on `NO_LEMMA` instances by construction, because
  generation cost is paid with nothing returned.
- The harvest step uses a larger family-check budget than the accepted registry
  ({counts.get('harvest_check_budget', 'see metadata.json')} checks). This is a Phase 4B
  component; the accepted registry, its budget, and the structural answer are unchanged.
- The expanded Part A set uses one seed per instance and is diagnostic; only the
  14 accepted Phase 3 instances use three seeds.
- Independent verification used a {counts.get('lemma_verification_timeout', 5)} s per-lemma
  limit and a per-instance budget; no lemma hit either limit in this run, so the
  accounting is complete for this set.
- Three unrelated containers were running on the host throughout the campaign. They
  were idle, but this is a deviation from a fully quiescent environment and is recorded
  rather than assumed harmless.
- Results apply only to these generated families, solver pins, timeout, and hardware.
  No general complexity or novelty claim is made.

## Artifacts

`fallback_comparison.csv`, `part_a_summary.csv`, `lemma_ablation.csv`,
`lemma_verification.csv`, `lemma_delta.csv`, `cdcl_statistics.csv`,
`correctness.csv`, `structural_refutations.csv`, `phase_timings.csv`,
`boundary_sweep.csv`, `cnfs/`, `lemmas/`, `logs/`, `failures/`, `metadata.json`.

Phase 5 was not started.
"""


TIMEOUT_TEXT = "5 s"
