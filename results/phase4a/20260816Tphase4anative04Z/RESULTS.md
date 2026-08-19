# Phase 4A results — native performance integration

Run ID: `20260816Tphase4anative04Z`  
Completed: 2026-08-16T16:41:41.356078+00:00

## What was implemented and tested

The accepted LKK structural semantics were ported to C++: DIMACS parsing, both deterministic gates, bounded recovery, exact-r/resource registries, Dinic FlowBoxTest, the Phase 2 witness format, and same-process CaDiCaL 3.0.1 fallback. CaDiCaL received only the original parsed clauses. Kissat remained external. No LemmaBridge, learned gate, or benchmark-specific routing was added.

Correctness was the performance gate: 46 accepted Phase 2/3 CNFs were re-run through Python LKK, native LKK, locked CaDiCaL and locked Kissat; 5,000 new flow/exhaustive cases had zero mismatches; 53 emitted native structural witnesses passed the independent Python checker. The exact 14 Phase 3 CNFs were hash-verified and not regenerated.

## Exact commands

```powershell
docker build -f Dockerfile.phase4a -t lkk-hsat-phase4a:native .
docker run --rm --volume "${PWD}:/work" --entrypoint python3 lkk-hsat-phase1:locked -m phase4a.profile_python --phase3 /work/results/phase3/20260816Tphase3benchmark01Z --config /work/phase3/config.json --output /work/results/phase4a/profiling
docker run --rm --volume "${PWD}:/work" --entrypoint python3 lkk-hsat-phase1:locked -m phase4a.profile_memory --phase3 /work/results/phase3/20260816Tphase3benchmark01Z --config /work/phase3/config.json --output /work/results/phase4a/profiling
python -m phase4a.summarize_profile --profiling results/phase4a/profiling --phase3 results/phase3/20260816Tphase3benchmark01Z
powershell -ExecutionPolicy Bypass -File .\scripts\run_phase4a.ps1 -RunId 20260816Tphase4anative04Z
python -m phase4a.finalize_phase4a --run results/phase4a/20260816Tphase4anative04Z
python -m unittest benchmark.test_harness phase2.test_phase2 phase3.test_phase3 phase4a.test_phase4a -v
```

Compiler flags: `g++ -std=c++17 -O3 -DNDEBUG -Wall -Wextra; CaDiCaL: -O3 -DNDEBUG -DNCONTRACTS -DNTRACING`.

## Profile and implementation findings

The pre-change cProfile recorded 8.200 s in registry construction out of 9.295 s total across the 14 CNFs. Repeated combination canonicalization and set/sort membership dominated. Aggregate matched native registry time fell from 8200.137 ms to 1423.049 ms (5.76x). Flow remained cheap. `profiling/profile_categories.csv` covers every requested phase, `registry_dominant_operations.csv` records hashing/canonicalization/index operations, and `python_memory_allocations.csv` preserves per-instance traced allocation peaks and top allocation sites.

## Answers to the required questions

1. Native LKK median implementation speedup on the original set: 2.08x (per-instance values in `native_vs_python.csv`).
2. Registry time decreased by 5.76x in aggregate matched structural runs.
3. Python startup averaged 97.237 ms and is eliminated from the internal native path. In the instrumented profile, Python CNF parsing averaged 24.182 ms versus 3.293 ms for completed native reruns (7.34x); the latter parses once and feeds the same clauses to in-process CaDiCaL, eliminating the fallback reparse. The profile comparison is diagnostic because cProfile adds overhead.
4. Native LKK beat CaDiCaL on 11/14 original instances by PAR-2.
5. Native LKK beat Kissat on 5/14.
6. Native LKK beat both on 5/14.
7. Scale-10 pigeonhole reproduced: CaDiCaL/native=43.07x, Kissat/native=4.64x.
8. In the tested pigeonhole sweep, native beat both at scales [8, 9, 10, 11, 12]; this is the measured boundary, not an extrapolation.
9. Capacity-2 crossed Kissat at tested scales [6].
10. Exact-r scale 11 timed out for all solvers at 5 s. A separate, clearly labeled 30 s correctness diagnostic completed in 16357.4 ms: parse 2.988 ms, gates 0.299 ms, recovery 0.430 ms, registry 29.277 ms, then registry returned UNKNOWN at the unchanged 200,000 family-check budget; in-process CaDiCaL fallback consumed 16323.4 ms and solved UNSAT. Peak RSS was 21356 kB. Thus the 5 s failure is CDCL fallback difficulty after conservative registry abort, not FlowBoxTest.
11. Random 3-SAT native overhead rows are preserved in `boundary_sweep.csv`; observed gate/recovery/registry costs: [(150, 0.136051, 0.227157, 0.546248), (400, 0.450963, 0.686355, 4.22897), (800, 0.837251, 1.44646, 17.0183)].
12. The evidence shows registry overhead was substantially implementation-related; remaining time on FEASIBLE/UNKNOWN cases is principally CDCL fallback, while large exact family validation remains combinatorial algorithmic work.
13. **Yes, Phase 4B is warranted as a separate correctness-gated experiment**, because the exact rerun produced five wins over both solvers and the pigeonhole advantage persisted across scales 8–12. It is not warranted as a presumed improvement: capacity-2 crossed Kissat only at scale 6, Hall remained fallback-dominated, and exact-r scale 11 still timed out at 5 s.

## Negative results and limitations

- The native port preserves the existing false-open FastCheapGate behavior on random 3-SAT; it was not retuned using benchmark labels.
- Boundary sweeps use one seed/repetition and are diagnostic, unlike the three-seed exact rerun.
- RSS from `/usr/bin/time` is whole-process peak memory; internal native RSS is also recorded.
- CaDiCaL API fallback statistics are not exposed in the native JSON; direct locked baselines retain their available statistics.
- Results apply only to these generated families, solver pins, timeout, and hardware. No general complexity or novelty claim is made.
- Stopped runs `20260816Tphase4anative01Z` (external orchestration interruption), `02Z` (TO_VALIDATE bookkeeping bug), and `03Z` (empty checkpoint bug) are preserved and were not used for performance claims.

Raw artifacts: `phase3_rerun.csv`, `phase3_rerun_raw.csv`, `phase_timings.csv`, `boundary_sweep.csv`, `correctness.csv`, `flow_vs_exhaustive.csv`, `witness_validation.csv`, `profiling/`, `logs/`, and `failures/`.

Phase 4B was not started.
