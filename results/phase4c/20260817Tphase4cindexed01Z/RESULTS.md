# Phase 4C results — indexed registry search

Run ID: `20260817Tphase4cindexed01Z`
Completed: 2026-08-17T02:01:37.589075+00:00

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
| Newly structural (decided more) | 5 |

## Measurement against the sealed engine

| Instance | 4A structural | 4C structural | 4A PAR-2 (ms) | 4C PAR-2 (ms) | speedup |
| --- | --- | --- | --- | --- | --- |
| 001_pigeonhole_exact1_boundary_low_scale8_noise0.cnf | UNSAT | UNSAT | 23.9 | 18.7 | 1.28x |
| 002_pigeonhole_exact1_boundary_high_scale10_noise0.cnf | UNSAT | UNSAT | 67.3 | 34.6 | 1.95x |
| 003_exact_r_allocation_boundary_low_scale9_noise0.cnf | UNSAT | UNSAT | 45.5 | 18.5 | 2.46x |
| 004_exact_r_allocation_boundary_high_scale11_noise0.cnf |  | UNSAT | 10,000.0 | 18.5 | 539.50x |
| 005_overlapping_hall_violation_boundary_low_scale8_noise0.cnf | UNKNOWN | UNSAT | 686.3 | 34.9 | 19.68x |
| 006_overlapping_hall_violation_boundary_high_scale9_noise0.cnf | UNKNOWN | UNSAT | 4,066.2 | 67.5 | 60.26x |
| 007_capacity2_violation_boundary_low_scale5_noise0.cnf | UNSAT | UNSAT | 268.2 | 285.5 | 0.94x |
| 008_capacity2_violation_noise100_scale5_noise100.cnf | UNSAT | UNSAT | 335.2 | 285.3 | 1.17x |
| 009_hidden_pigeonhole_visible_noise0_scale9_noise0.cnf | UNSAT | UNSAT | 67.4 | 35.3 | 1.91x |
| 010_hidden_pigeonhole_hidden_noise100_scale9_noise100.cnf | UNKNOWN | UNSAT | 469.5 | 35.3 | 13.30x |
| 011_satisfiable_capacity_control_sat_fallback_scale24_noise0.cnf | UNKNOWN | FEASIBLE | 67.6 | 6,473.7 | 0.01x |
| 012_random_3sat_uniform_scale150_noise0.cnf | UNKNOWN | UNKNOWN | 67.2 | 67.1 | 1.00x |
| 013_random_3sat_planted_sat_scale400_noise0.cnf | UNKNOWN | UNKNOWN | 804.5 | 736.3 | 1.09x |
| 014_random_3sat_planted_sat_scale800_noise0.cnf | UNKNOWN | UNKNOWN | 820.8 | 922.1 | 0.89x |

## Findings

1. Instances the sealed engine left to CDCL fallback and Phase 4C now decides
   structurally: **4** (005_overlapping_hall_violation_boundary_low_scale8_noise0.cnf, 006_overlapping_hall_violation_boundary_high_scale9_noise0.cnf, 010_hidden_pigeonhole_hidden_noise100_scale9_noise100.cnf, 011_satisfiable_capacity_control_sat_fallback_scale24_noise0.cnf).
2. Median speedup over the sealed engine: **1.60x**
   (max 539.50x) across 14 instances.
3. Phase 4C beats locked CaDiCaL on 12/14 and locked Kissat on
   9/14 by mean PAR-2.
4. Random 3-SAT negative controls (no structure, so the cost is gate plus
   fallback): 012=67ms, 013=736ms, 014=922ms.

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
