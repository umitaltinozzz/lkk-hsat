# Phase 4B results — fallback engine comparison and LemmaBridge

Run ID: `20260816Tphase4bfallbacklemma01Z`
Completed run of the two separate Phase 4B questions. Part A changes only which
solver receives the unchanged formula. Part B adds only clauses that a locked
independent solver certified as implied by the original CNF. The accepted
LKK-HSAT 3.0 structural semantics are unchanged in both parts: the structural
answer is still produced by the unchanged registry and FlowBoxTest.

## Scope and gates

| Gate | Result |
| --- | --- |
| Fresh FlowBoxTest/exhaustive cross-checks | 5,000 cases, 0 mismatches |
| Correctness cases (all modes vs both locked solvers) | 47 cases, 0 failures |
| Definite-answer mismatches | 0 |
| Accepted Phase 3 instances re-run (hash verified, not regenerated) | 14 |
| Expanded Part A instances | 33 |
| Part A measured runs | 375 |
| Part B measured runs | 435 |

# Part A — fallback engine comparison

Modes: **A** `LKK + in-process CaDiCaL`, **B** `LKK + external Kissat`,
**C** `LKK structural only, then UNKNOWN`. Locked CaDiCaL and locked Kissat
baselines are measured alongside. Kissat's DIMACS serialization and
fork/exec cost are recorded separately from its own reported process time.

| Family | Instances | Kissat wins | CaDiCaL wins | mean PAR-2 LKK+CaDiCaL (ms) | mean PAR-2 LKK+Kissat (ms) | Preferred |
| --- | --- | --- | --- | --- | --- | --- |
| capacity2_violation | 2 | 0 | 0 | 10,000.0 | 10,000.0 | tie (both time out) |
| exact_r_allocation | 3 | 0 | 0 | 10,000.0 | 10,000.0 | tie (both time out) |
| hidden_pigeonhole | 4 | 4 | 0 | 2,480.7 | 255.5 | Kissat |
| overlapping_hall_violation | 6 | 5 | 0 | 5,004.2 | 2,528.7 | Kissat |
| random_3sat | 9 | 4 | 4 | 1,610.2 | 1,468.2 | mixed |
| satisfiable_capacity_control | 5 | 1 | 4 | 71.5 | 117.7 | CaDiCaL |

### Answers

1. **On how many instances is LKK+Kissat faster than LKK+CaDiCaL?**
   14 of 29 fallback-dominated instances by mean PAR-2.
   8 favour CaDiCaL and 7 are ties in which both engines
   exhaust the timeout and carry the identical PAR-2 penalty. Counting only the
   22 decided instances, Kissat wins 14 and CaDiCaL wins 8.
2. **Does Kissat fallback remove any timeout?**
   Yes — 008_overlapping_hall_violation_scale10_phase4b.cnf
   
   Totals: CaDiCaL-fallback timeouts 10, Kissat-fallback timeouts 9.
3. **Does the extra process-launch cost erase Kissat's solver advantage?**
   Mean measured serialization 3.54 ms and total
   process+serialization overhead 26.77 ms per
   fallback. Mean PAR-2 gap in Kissat's favour is
   855.2 ms, so the overhead
   does not erase
   the advantage on this set.
4. **Which families favour each fallback engine?** See the table above; per-instance
   values are in `part_a_summary.csv` and `fallback_comparison.csv`.
5. **Is a solver-selection gate justified by evidence?**
   Yes, but only as a static family-level rule keyed on structure the front-end already computes, never on benchmark labels.
   The split is family-shaped rather than instance-shaped: hidden_pigeonhole, overlapping_hall_violation
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
| Candidates generated | 3344 |
| Independently verified and accepted | 3344 |
| **Failed implication verification (refuted)** | **0** |
| Unverified within the 5.0 s budget (rejected, not injected) | 0 |
| Total independent verification time | 65.1 s |

Candidates by origin: bounded_resolution_resolvent=696, capacity_family=1386, exact_r_family=1262.

## CDCL search effect

Only instances that actually received lemmas can show a lemma effect. The other
24 eligible instances returned `NO_LEMMA`; they still pay generation
cost and are accounted for separately below.

| Instance | Lemmas | Conflicts (no lemmas) | Conflicts (with lemmas) | Conflicts | Decisions | Propagations | PAR-2 no lemmas (ms) | PAR-2 with lemmas (ms) | Runtime |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 010_hidden_pigeonhole_hidden_noise100_scale9_noise100.cnf | 697 | 47,704 | 45,266 | -5.1% | -11.9% | -6.5% | 520 | 553 | 6.4% |
| 012_random_3sat_uniform_scale150_noise0.cnf | 1 | 3,062 | 2,736 | -10.6% | -13.0% | -8.2% | 68 | 68 | 0.5% |
| 025_hidden_pigeonhole_scale9_phase4b_noise100.cnf | 683 | 42,840 | 47,648 | 11.2% | 1.9% | 13.0% | 486 | 569 | 17.1% |
| 026_hidden_pigeonhole_scale10_phase4b_noise0.cnf | 975 | 352,083 | 330,720 | -6.1% | -7.7% | -4.7% | 4,066 | 4,134 | 1.7% |
| 027_hidden_pigeonhole_scale10_phase4b_noise100.cnf | 988 | 344,137 | 408,376 | 18.7% | 13.9% | 14.7% | 4,317 | 8,229 | 90.6% |

On the 24 `NO_LEMMA` instances the lemma arm is pure overhead: generation
runs, finds nothing transferable, and the run is slower by that amount.
11 of them regressed by more than 5%.

## Sound structural refutation found before lemma transfer

The harvest step re-runs the unchanged family search under a Phase 4B budget and
keeps the candidates already proven when the accepted registry reports UNKNOWN.
Each kept family still required all of its clauses to be present in the recovered
database, and a box is used only when every one of its variables is covered by a
distinct retained capacity group, so the sub-model is implied by the CNF. On the
instances below the sub-model alone has no feasible flow.

| Instance | Sub-model boxes | Sub-model resources | Max flow | Demand | Locked-solver answer |
| --- | --- | --- | --- | --- | --- |
| 003_exact_r_allocation_scale11_phase4b.cnf | 6 | 11 | 11 | 12 | UNSATISFIABLE |
| 004_exact_r_allocation_boundary_high_scale11_noise0.cnf | 6 | 11 | 11 | 12 | UNSATISFIABLE |
| 005_overlapping_hall_violation_boundary_low_scale8_noise0.cnf | 17 | 17 | 16 | 17 | UNSATISFIABLE |
| 006_overlapping_hall_violation_boundary_high_scale9_noise0.cnf | 19 | 19 | 18 | 19 | UNSATISFIABLE |
| 006_overlapping_hall_violation_scale8_phase4b.cnf | 17 | 17 | 16 | 17 | UNSATISFIABLE |
| 007_overlapping_hall_violation_scale9_phase4b.cnf | 19 | 19 | 18 | 19 | UNSATISFIABLE |
| 008_overlapping_hall_violation_scale10_phase4b.cnf | 21 | 21 | 20 | 21 | UNSATISFIABLE |
| 009_overlapping_hall_violation_scale11_phase4b.cnf | 23 | 23 | 22 | 23 | UNSATISFIABLE |
| 010_hidden_pigeonhole_hidden_noise100_scale9_noise100.cnf | 9 | 8 | 8 | 9 | UNSATISFIABLE |
| 014_capacity2_violation_scale7_phase4b.cnf | 15 | 7 | 14 | 15 | UNSATISFIABLE |
| 015_capacity2_violation_scale8_phase4b.cnf | 17 | 8 | 16 | 17 | UNSATISFIABLE |
| 025_hidden_pigeonhole_scale9_phase4b_noise100.cnf | 9 | 8 | 8 | 9 | UNSATISFIABLE |
| 026_hidden_pigeonhole_scale10_phase4b_noise0.cnf | 10 | 9 | 9 | 10 | UNSATISFIABLE |
| 027_hidden_pigeonhole_scale10_phase4b_noise100.cnf | 10 | 9 | 9 | 10 | UNSATISFIABLE |

Contradictions with a locked solver: **0**.

This refutation is reported as a finding only. It is not used to answer any
benchmark instance, because expressing an unconditional refutation as ordinary
clauses is degenerate — it entails every unit clause — and §3 requires
`NO_LEMMA` rather than a forced encoding.

## Final report questions

1. **Is Kissat a better fallback than CaDiCaL for any reproducible family?**
   Yes: hidden_pigeonhole, overlapping_hall_violation. These are families where the mean favours Kissat and most instances agree, not single-instance wins.
2. **Is the Kissat process-launch overhead material?**
   Mean 26.77 ms per fallback
   (serialization 3.54 ms), against a
   5 s timeout. It is material only where the two engines are otherwise close.
3. **Should the production architecture support both fallback engines?**
   Yes — the per-family split is large enough to justify a static, label-free routing rule, and the measured process boundary is small next to the wins it buys.
4. **How many sound lemmas were generated?** 3344 accepted of 3344 candidates.
5. **How many failed implication verification?** 0.
6. **Did lemmas reduce CaDiCaL decisions/conflicts?**
   Partly. Of the 5 instances that received lemmas, 3 show a
   conflict reduction above 5%: 010_hidden_pigeonhole_hidden_noise100_scale9_noise100.cnf, 012_random_3sat_uniform_scale150_noise0.cnf, 026_hidden_pigeonhole_scale10_phase4b_noise0.cnf.
   The effect is not consistent within a family — the same hidden-cardinality family
   contains both reductions and increases, so it does not meet §10's "reproducible
   family" bar.
7. **Did they reduce end-to-end runtime?**
   No. Not on a single instance, once lemma-generation cost is included.
8. **Which families benefited?** none.
9. **Which families regressed?** With lemmas actually injected:
   hidden_pigeonhole. Separately, every family
   that returned `NO_LEMMA` regressed by the generation cost alone; that is a cost of
   running the bridge, not an effect of any lemma.
10. **Did exact-r scale 11 improve?** 3 lemma-eligible instances, NO_LEMMA on all of them, so no lemma effect is measurable; Part A: 0/3 instances favour Kissat fallback.
11. **Did Hall/overlapping-capacity improve?** 6 lemma-eligible instances, NO_LEMMA on all of them, so no lemma effect is measurable; Part A: 5/6 instances favour Kissat fallback.
12. **Did the capacity-2 crossover move?** 2 lemma-eligible instances, NO_LEMMA on all of them, so no lemma effect is measurable; Part A: 0/2 instances favour Kissat fallback.
13. **What happened on random 3-SAT?** 1 of 9 instances received lemmas, 1 injected, mean runtime change +0.5%; Part A: 4/9 instances favour Kissat fallback.
14. **Should Phase 5 begin?** Judged strictly against §10: Part B succeeds only when every
    lemma is verified, mismatches are zero, CDCL search falls on a reproducible family, and
    end-to-end runtime improves including generation cost.
    Measured here: verified=3344/3344, refuted=0,
    mismatches=0, search reduced on 3 instances,
    runtime improved on 0 instances.
    **Part B success criteria met: no.**

    Recommendation, split by part. **Part A is ready to carry into Phase 5**: the
    fallback-engine choice is a reproducible, correctness-neutral improvement on
    named families. **Part B is not.** LemmaBridge is sound — 3344 lemmas,
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
  (2000000 checks). This is a Phase 4B
  component; the accepted registry, its budget, and the structural answer are unchanged.
- The expanded Part A set uses one seed per instance and is diagnostic; only the
  14 accepted Phase 3 instances use three seeds.
- Independent verification used a 5.0 s per-lemma
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
