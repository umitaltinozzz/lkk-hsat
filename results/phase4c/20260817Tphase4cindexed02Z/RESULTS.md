# Phase 4C results — indexed registry search

Run ID: `20260817Tphase4cindexed02Z`
Completed: 2026-08-17T02:30:38.190287+00:00

## What changed

Phase 4C changes only how the exact-r candidate search spends
`family_check_budget`. The acceptance test, the candidate key, the selection
order and the meaning of a "check" are unchanged, so with an unbounded budget the
search returns exactly what the accepted registry returns.

1. **Indexed candidates.** The accepted search paired every monotone-positive
   clause with every monotone-negative one and discarded the pair unless it
   shared exactly two variables. An inverted index from a variable pair to the
   negatives containing it yields only the partners that can qualify.
2. **Lazy family verification.** The accepted search materialised every required
   combination and charged the budget for all of them before testing one, so a
   candidate rejected on its first clause still cost the whole family. Each
   membership test is now charged as it happens, and every decided group is
   remembered so it is never re-decided.

The structural core is now a single definition in `native/core/lkk_core.hpp`,
shared by every engine, with `native/core/test_core_drift.py` differential-testing
it against the sealed Phase 4A binary.

## Correctness gate

| Gate | Result |
| --- | --- |
| Fresh FlowBoxTest/exhaustive cross-checks | 5,000 cases, 0 mismatches |
| Correctness cases | 46, 0 failures |
| Contradicts the sealed engine | 0 |
| Pre-registry stages changed | 0 |
| Witnesses rejected by the independent checker | 0 |
| Newly structural (decided more) | 4 |

## Measurement against the sealed engine

| Instance | 4A structural | 4C structural | 4A PAR-2 (ms) | 4C PAR-2 (ms) | speedup |
| --- | --- | --- | --- | --- | --- |
| 001_pigeonhole_exact1_boundary_low_scale8_noise0.cnf | UNSAT | UNSAT | 36.4 | 20.2 | 1.80x |
| 002_pigeonhole_exact1_boundary_high_scale10_noise0.cnf | UNSAT | UNSAT | 101.6 | 51.1 | 1.99x |
| 003_exact_r_allocation_boundary_low_scale9_noise0.cnf | UNSAT | UNSAT | 34.8 | 15.6 | 2.23x |
| 004_exact_r_allocation_boundary_high_scale11_noise0.cnf |  | UNSAT | 10,000.0 | 18.8 | 531.25x |
| 005_overlapping_hall_violation_boundary_low_scale8_noise0.cnf | UNKNOWN | UNSAT | 821.5 | 36.2 | 22.71x |
| 006_overlapping_hall_violation_boundary_high_scale9_noise0.cnf | UNKNOWN | UNSAT | 4,335.8 | 34.8 | 124.55x |
| 007_capacity2_violation_boundary_low_scale5_noise0.cnf | UNSAT | UNSAT | 268.5 | 117.7 | 2.28x |
| 008_capacity2_violation_noise100_scale5_noise100.cnf | UNSAT | UNSAT | 387.4 | 118.6 | 3.27x |
| 009_hidden_pigeonhole_visible_noise0_scale9_noise0.cnf | UNSAT | UNSAT | 67.5 | 34.6 | 1.95x |
| 010_hidden_pigeonhole_hidden_noise100_scale9_noise100.cnf | UNKNOWN | UNSAT | 468.9 | 35.6 | 13.17x |
| 011_satisfiable_capacity_control_sat_fallback_scale24_noise0.cnf | UNKNOWN | UNKNOWN | 67.4 | 268.0 | 0.25x |
| 012_random_3sat_uniform_scale150_noise0.cnf | UNKNOWN | UNKNOWN | 67.4 | 67.3 | 1.00x |
| 013_random_3sat_planted_sat_scale400_noise0.cnf | UNKNOWN | UNKNOWN | 804.2 | 770.9 | 1.04x |
| 014_random_3sat_planted_sat_scale800_noise0.cnf | UNKNOWN | UNKNOWN | 955.6 | 972.5 | 0.98x |

## Findings

1. Instances the sealed engine left to CDCL fallback and Phase 4C now decides
   structurally: **3** (005_overlapping_hall_violation_boundary_low_scale8_noise0.cnf, 006_overlapping_hall_violation_boundary_high_scale9_noise0.cnf, 010_hidden_pigeonhole_hidden_noise100_scale9_noise100.cnf).
2. Median speedup over the sealed engine: **2.11x**
   (max 531.25x) across 14 instances.
3. Phase 4C beats locked CaDiCaL on 12/14 and locked Kissat on
   12/14 by mean PAR-2.
4. Random 3-SAT negative controls (no structure, so the cost is gate plus
   fallback): 012=67ms, 013=771ms, 014=972ms.

## Limitations

- The change is a budget-allocation change. It cannot decide anything the
  accepted acceptance test would reject, but at a fixed budget it does decide
  more, so Phase 4C is not bit-identical to Phase 4A on UNKNOWN instances --
  that is the intended effect and is tested rather than assumed.
- Results apply only to these generated families, solver pins, timeout and
  hardware. No general complexity claim is made.

Raw artifacts: `correctness.csv`, `measurements.csv`, `summary.csv`, `logs/`,
`failures/`, `metadata.json`.

Phase 5 was not started.
