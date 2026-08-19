# Phase 3 results - locked-solver performance campaign

Run ID: `20260816Tphase3candidate02Z`  
Completed: 2026-08-16T16:07:28.504707+00:00

## Scope and methodology

This phase benchmarks the unchanged Phase 2 LKK structural engine against locked CaDiCaL 3.0.1 and Kissat 4.0.4. It does not implement LemmaBridge. A preserved calibration sweep selected boundary scales; calibration timings are not mixed into the final results.

Every final CNF first passed an unmeasured 30-second agreement run with CaDiCaL, Kissat, and LKK-hybrid. The measured campaign then used seeds [1, 2, 3], 1 repetition per seed, deterministic solver-order rotation, and a 5.0-second timeout. PAR-2 assigns twice the timeout to an unsolved run.

The primary LKK runtime is process-level `runtime_ms` / `total_lkk_end_to_end_ms`. It includes Python startup, CNF parsing, FastCheapGate, BenefitGate, bounded recovery, registry construction, FlowBoxTest, witness generation/checking, and any CDCL fallback. Internal phase times are reported separately, not substituted for the full total.

## Exact commands

```powershell
python -m unittest benchmark.test_harness phase2.test_phase2 phase3.test_phase3 -v
powershell -ExecutionPolicy Bypass -File .\scripts\calibrate_phase3.ps1 -RunId 20260816Tphase3calibration02Z
powershell -ExecutionPolicy Bypass -File .\scripts\run_phase3.ps1 -RunId 20260816Tphase3candidate02Z
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
| 001_pigeonhole_exact1_boundary_low_scale8_noise0.cnf | 3/3 / 136.6 | 3/3 / 58.1 | 3/3 / 252.9 | 0.54x | 0.23x |
| 002_pigeonhole_exact1_boundary_high_scale10_noise0.cnf | 3/3 / 4704.4 | 3/3 / 468.7 | 3/3 / 319.1 | 14.74x | 1.47x |
| 003_exact_r_allocation_boundary_low_scale9_noise0.cnf | 3/3 / 184.5 | 3/3 / 185.0 | 3/3 / 535.7 | 0.34x | 0.35x |
| 004_exact_r_allocation_boundary_high_scale11_noise0.cnf | 0/3 / 10000.0 | 0/3 / 10000.0 | 0/3 / 10000.0 | 1.00x | 1.00x |
| 005_overlapping_hall_violation_boundary_low_scale8_noise0.cnf | 3/3 / 552.1 | 3/3 / 184.0 | 3/3 / 1188.7 | 0.46x | 0.15x |
| 006_overlapping_hall_violation_boundary_high_scale9_noise0.cnf | 3/3 / 4302.2 | 3/3 / 419.0 | 1/3 / 8145.1 | 0.53x | 0.05x |
| 007_capacity2_violation_boundary_low_scale5_noise0.cnf | 3/3 / 1273.3 | 3/3 / 286.0 | 3/3 / 636.8 | 2.00x | 0.45x |
| 008_capacity2_violation_noise100_scale5_noise100.cnf | 3/3 / 1691.7 | 3/3 / 251.7 | 3/3 / 687.4 | 2.46x | 0.37x |
| 009_hidden_pigeonhole_visible_noise0_scale9_noise0.cnf | 3/3 / 554.3 | 3/3 / 169.1 | 3/3 / 503.1 | 1.10x | 0.34x |
| 010_hidden_pigeonhole_hidden_noise100_scale9_noise100.cnf | 3/3 / 570.8 | 3/3 / 151.5 | 3/3 / 837.0 | 0.68x | 0.18x |
| 011_satisfiable_capacity_control_sat_fallback_scale24_noise0.cnf | 3/3 / 118.4 | 3/3 / 24.7 | 3/3 / 854.1 | 0.14x | 0.03x |
| 012_random_3sat_uniform_scale150_noise0.cnf | 3/3 / 118.9 | 3/3 / 118.5 | 3/3 / 319.6 | 0.37x | 0.37x |
| 013_random_3sat_planted_sat_scale400_noise0.cnf | 3/3 / 922.3 | 3/3 / 988.2 | 3/3 / 1122.4 | 0.82x | 0.88x |
| 014_random_3sat_planted_sat_scale800_noise0.cnf | 3/3 / 887.8 | 3/3 / 151.3 | 3/3 / 1239.2 | 0.72x | 0.12x |

- Instances where LKK had lower PAR-2 than both locked solvers: 1/14.
- Baseline timeouts: {'kissat': 3, 'cadical': 3}.
- FastCheapGate measured decisions: {'RUN_LKK': 37, 'TIMEOUT': 5}.
- Structural outcomes: {'UNSAT': 18, 'UNKNOWN': 24}.
- LKK fallback runs: 19/42.

## Random 3-SAT negative controls

012_random_3sat_uniform_scale150_noise0.cnf: CaDiCaL/LKK PAR-2=0.372053x, 013_random_3sat_planted_sat_scale400_noise0.cnf: CaDiCaL/LKK PAR-2=0.821663x, 014_random_3sat_planted_sat_scale800_noise0.cnf: CaDiCaL/LKK PAR-2=0.716424x

Random 3-SAT is a negative control, not a target family. Gate/recovery/registry behavior and any fallback overhead are retained in `measurements.csv`; no random-SAT advantage is assumed.

## Raw outputs

- `measurements.csv`: every interleaved raw timing, status, RSS, statistics, and LKK phase breakdown.
- `performance_summary.csv`: solved counts, timeout counts, medians, PAR-2, RSS, and relative ratios.
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
