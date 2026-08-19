# Phase 3 results - locked-solver performance campaign

Run ID: `20260816Tphase3benchmark01Z`  
Completed: 2026-08-16T16:12:35.415390+00:00

## Scope and methodology

This phase benchmarks the unchanged Phase 2 LKK structural engine against locked CaDiCaL 3.0.1 and Kissat 4.0.4. It does not implement LemmaBridge. A preserved calibration sweep selected boundary scales; calibration timings are not mixed into the final results.

Every final CNF first passed an unmeasured 30-second agreement run with CaDiCaL, Kissat, and LKK-hybrid. The measured campaign then used seeds [1, 2, 3], 1 repetition per seed, deterministic solver-order rotation, and a 5.0-second timeout. PAR-2 assigns twice the timeout to an unsolved run.

The primary LKK runtime is process-level `runtime_ms` / `total_lkk_end_to_end_ms`. It includes Python startup, CNF parsing, FastCheapGate, BenefitGate, bounded recovery, registry construction, FlowBoxTest, witness generation/checking, and any CDCL fallback. Internal phase times are reported separately, not substituted for the full total.

## Exact commands

```powershell
python -m unittest benchmark.test_harness phase2.test_phase2 phase3.test_phase3 -v
powershell -ExecutionPolicy Bypass -File .\scripts\calibrate_phase3.ps1 -RunId 20260816Tphase3calibration02Z
powershell -ExecutionPolicy Bypass -File .\scripts\run_phase3.ps1 -RunId 20260816Tphase3benchmark01Z
```

## Correctness safeguards

- Final benchmark CNFs: 14
- Pre-measurement LKK/CaDiCaL/Kissat validation failures: 0
- New randomized FlowBoxTest/exhaustive checks: 1000
- Flow/exhaustive mismatches: 0
- Definite measured-answer mismatches/errors: 0
- Phase 2 immutable tree: 235 files, `dba6b4579fa6ee07bd55564c796b2e70f37633028bfa571c5002dc8a7e4c56e7`

## Performance results

Ratios above 1.0 mean the LKK PAR-2 value was lower. These results apply only to this generated campaign and hardware.

| Instance | CaDiCaL solved/PAR-2 ms | Kissat solved/PAR-2 ms | LKK solved/PAR-2 ms | CaDiCaL/LKK | Kissat/LKK |
|---|---:|---:|---:|---:|---:|
| 001_pigeonhole_exact1_boundary_low_scale8_noise0.cnf | 3/3 / 118.0 | 3/3 / 35.5 | 3/3 / 234.9 | 0.50x | 0.15x |
| 002_pigeonhole_exact1_boundary_high_scale10_noise0.cnf | 2/3 / 6273.8 | 3/3 / 503.4 | 3/3 / 319.3 | 19.65x | 1.58x |
| 003_exact_r_allocation_boundary_low_scale9_noise0.cnf | 3/3 / 236.4 | 3/3 / 185.7 | 3/3 / 637.2 | 0.37x | 0.29x |
| 004_exact_r_allocation_boundary_high_scale11_noise0.cnf | 0/3 / 10000.0 | 0/3 / 10000.0 | 0/3 / 10000.0 | 1.00x | 1.00x |
| 005_overlapping_hall_violation_boundary_low_scale8_noise0.cnf | 3/3 / 468.3 | 3/3 / 167.1 | 3/3 / 1137.6 | 0.41x | 0.15x |
| 006_overlapping_hall_violation_boundary_high_scale9_noise0.cnf | 3/3 / 4117.4 | 3/3 / 435.1 | 1/3 / 8044.8 | 0.51x | 0.05x |
| 007_capacity2_violation_boundary_low_scale5_noise0.cnf | 3/3 / 1172.3 | 3/3 / 285.3 | 3/3 / 536.6 | 2.18x | 0.53x |
| 008_capacity2_violation_noise100_scale5_noise100.cnf | 3/3 / 1624.7 | 3/3 / 269.2 | 3/3 / 721.1 | 2.25x | 0.37x |
| 009_hidden_pigeonhole_visible_noise0_scale9_noise0.cnf | 3/3 / 502.9 | 3/3 / 186.9 | 3/3 / 571.3 | 0.88x | 0.33x |
| 010_hidden_pigeonhole_hidden_noise100_scale9_noise100.cnf | 3/3 / 553.7 | 3/3 / 169.3 | 3/3 / 971.8 | 0.57x | 0.17x |
| 011_satisfiable_capacity_control_sat_fallback_scale24_noise0.cnf | 3/3 / 101.5 | 3/3 / 30.0 | 3/3 / 837.3 | 0.12x | 0.04x |
| 012_random_3sat_uniform_scale150_noise0.cnf | 3/3 / 118.1 | 3/3 / 119.6 | 3/3 / 306.1 | 0.39x | 0.39x |
| 013_random_3sat_planted_sat_scale400_noise0.cnf | 3/3 / 837.9 | 3/3 / 971.7 | 3/3 / 1055.4 | 0.79x | 0.92x |
| 014_random_3sat_planted_sat_scale800_noise0.cnf | 3/3 / 955.1 | 3/3 / 167.7 | 3/3 / 1256.0 | 0.76x | 0.13x |

- Instances where LKK had lower PAR-2 than both locked solvers: 1/14.
- Baseline timeouts: {'cadical': 4, 'kissat': 3}.
- FastCheapGate measured decisions: {'RUN_LKK': 37, 'TIMEOUT': 5}.
- Structural outcomes: {'UNSAT': 18, 'UNKNOWN': 24}.
- LKK fallback runs: 19/42.

## LKK end-to-end phase costs

All values below are medians in milliseconds. `process total` is the primary full cost. `n/a` means the process hit the outer timeout before writing final phase telemetry; its 5-second timeout is still included in `measurements.csv` and PAR-2.

| Instance | done/timeout | gate | benefit | recovery | registry | flow | fallback | internal total | process total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 001_pigeonhole_exact1_boundary_low_scale8_noise0.cnf | 3/0 | 1.5 | 0.0 | 1.2 | 40.6 | 0.2 | 0.0 | 78.2 | 218.2 |
| 002_pigeonhole_exact1_boundary_high_scale10_noise0.cnf | 3/0 | 3.0 | 0.0 | 2.4 | 122.6 | 0.3 | 0.0 | 169.6 | 319.2 |
| 003_exact_r_allocation_boundary_low_scale9_noise0.cnf | 3/0 | 4.1 | 0.0 | 2.5 | 382.0 | 0.2 | 0.0 | 431.0 | 620.6 |
| 004_exact_r_allocation_boundary_high_scale11_noise0.cnf | 0/3 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| 005_overlapping_hall_violation_boundary_low_scale8_noise0.cnf | 3/0 | 4.0 | 0.0 | 3.4 | 425.9 | 0.0 | 519.4 | 1002.4 | 1170.2 |
| 006_overlapping_hall_violation_boundary_high_scale9_noise0.cnf | 1/2 | 6.3 | 0.0 | 5.6 | 458.5 | 0.0 | 3481.9 | 3976.5 | 4134.3 |
| 007_capacity2_violation_boundary_low_scale5_noise0.cnf | 3/0 | 4.3 | 0.0 | 2.6 | 349.1 | 0.2 | 0.0 | 399.5 | 520.4 |
| 008_capacity2_violation_noise100_scale5_noise100.cnf | 3/0 | 11.0 | 0.0 | 8.2 | 458.7 | 0.2 | 0.0 | 560.5 | 720.2 |
| 009_hidden_pigeonhole_visible_noise0_scale9_noise0.cnf | 3/0 | 8.9 | 0.0 | 14.3 | 275.9 | 0.2 | 0.0 | 384.6 | 570.7 |
| 010_hidden_pigeonhole_hidden_noise100_scale9_noise100.cnf | 3/0 | 13.6 | 0.0 | 16.0 | 136.4 | 0.0 | 569.5 | 787.2 | 972.2 |
| 011_satisfiable_capacity_control_sat_fallback_scale24_noise0.cnf | 3/0 | 49.4 | 0.0 | 39.9 | 433.5 | 0.0 | 117.8 | 708.6 | 870.6 |
| 012_random_3sat_uniform_scale150_noise0.cnf | 3/0 | 3.2 | 0.0 | 1.8 | 4.2 | 0.0 | 117.0 | 143.2 | 321.6 |
| 013_random_3sat_planted_sat_scale400_noise0.cnf | 3/0 | 8.5 | 0.0 | 5.9 | 31.0 | 0.0 | 870.2 | 931.7 | 1071.7 |
| 014_random_3sat_planted_sat_scale800_noise0.cnf | 3/0 | 19.1 | 0.0 | 13.5 | 123.9 | 0.0 | 819.9 | 1003.1 | 1122.5 |

## Random 3-SAT negative controls

012_random_3sat_uniform_scale150_noise0.cnf: CaDiCaL/LKK PAR-2=0.385933x, 013_random_3sat_planted_sat_scale400_noise0.cnf: CaDiCaL/LKK PAR-2=0.793936x, 014_random_3sat_planted_sat_scale800_noise0.cnf: CaDiCaL/LKK PAR-2=0.760421x

Random 3-SAT is a negative control, not a target family. Gate/recovery/registry behavior and any fallback overhead are retained in `measurements.csv`; no random-SAT advantage is assumed.

## Raw outputs

- `measurements.csv`: every interleaved raw timing, status, RSS, statistics, and LKK phase breakdown.
- `performance_summary.csv`: solved counts, timeout counts, medians, PAR-2, RSS, and relative ratios.
- `lkk_phase_summary.csv`: median full process cost and each internal LKK phase.
- `correctness.csv`: unmeasured pre-timing agreement checks.
- `correctness_safeguard.csv`: 1,000 new small flow/exhaustive comparisons.
- `instances.csv`: generated parameters and CNF hashes.
- `logs/`: raw solver, hybrid, witness, timeout-memory, and effective-config records.
- `failures/`: preserved correctness or measurement counterexamples, if present.
- `metadata.json`: exact binaries, machine, configuration, calibration reference, and immutable-tree hashes.

## Limitations and negative results

- This is a controlled generated-family study, not a claim of general SAT superiority.
- Process launch overhead is material for very short runs and is included for all variants.
- Timeouts make PAR-2 dependent on the declared 5-second cap; solved-only medians are also retained.
- The Phase 2 registry remains conservative and budgeted. A missed structure or budget rejection correctly falls back to CaDiCaL and may add overhead.
- No clauses or lemmas are injected into CDCL. This remains LKK-or-fallback, not LemmaBridge.
- No novelty, P=NP, or general polynomial-SAT claim is made.

## Next experiment

The next phase may investigate CaDiCaL API integration and only sound, independently verified constraint transfer. It should not begin until these Phase 3 results are reviewed. No LemmaBridge work was performed here.
