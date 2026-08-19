# Phase 1 results - reproducible CDCL baseline

Date: 2026-08-16

## What was implemented

- Locked the newest non-RC official tags observed on 2026-08-16:
  - CaDiCaL 3.0.1, `rel-3.0.1`, commit
    `c60730422e758ef1cebe7aeddf2dda31c996bf04`.
  - Kissat 4.0.4, `rel-4.0.4`, commit
    `8af8e56f174b778aef3aa45af9f739b2a5f492c2`.
- Added an immutable Debian base image and a multi-stage build that checks out
  those exact commits and compiles static, unmodified solver binaries.
- Added a direct-DIMACS CLI harness with fixed seeds, repetitions, external
  timeout, deterministic interleaving, raw logs, CSV output, solver/CNF binary
  hashes, host/container metadata, peak RSS, and available conflicts, decisions,
  and propagations.
- Added a mandatory unmeasured agreement pass. Measurement does not start unless
  every solver returns the same definite SAT/UNSAT result. Definite measurement
  results are also checked against that validated result.
- Added strict DIMACS validation and four harness unit tests.
- Added two tiny SAT/UNSAT smoke CNFs. They validate the baseline machinery;
  they are not a performance benchmark and support no solver-speed conclusion.

No LKK gate, recovery, BoxTest, flow engine, lemma bridge, or architectural
optimization was implemented in this phase.

## What was tested

The final accepted smoke run is `20260816Tphase1baseline04Z`:

- Agreement pass: passed for both CNFs.
- Validated answers: one SAT and one UNSAT.
- Measurement runs: 24 (2 instances x 2 solvers x 3 seeds x 2 repetitions).
- Timeouts: 0.
- Measurement-answer mismatches: 0.
- Missing peak-RSS/statistics fields: 0.
- Harness unit tests: 4 passed.
- Container compiler: GCC 12.2.0; GNU Make 4.3.
- Solver binary SHA-256 values and complete machine details are recorded in the
  accepted run's metadata and CSV.

The observed wall times are dominated by process and GNU `time` startup because
the smoke CNFs are deliberately tiny. They must not be presented as comparative
CaDiCaL/Kissat performance results.

## Exact commands

From the repository root in PowerShell with Docker Desktop running:

```powershell
python -m unittest benchmark.test_harness -v
powershell -ExecutionPolicy Bypass -File .\scripts\build_phase1.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run_phase1.ps1 -RunId 20260816Tphase1baseline04Z
```

The build script passes the commits from `solvers.lock.json` as Docker build
arguments. The run configuration is `benchmark/config.phase1.json`: timeout 10
seconds, validation seed 1, measurement seeds 1/2/3, two repetitions.

## Raw result locations

- Accepted final run: `results/phase1/20260816Tphase1baseline04Z/`
  - `agreement.csv`: unmeasured pre-timing agreement runs.
  - `measurements.csv`: all accepted timing/statistics rows.
  - `logs/`: per-process raw solver output and GNU-time memory records.
  - `host-metadata.json` and `container-metadata.json`: hardware and environment.
  - `solvers.lock.json`, `solver-build-metadata.txt`, and `config.json`: exact
    provenance/configuration snapshots.
  - `summary.json`: machine-readable completion summary.
- Preserved harness/configuration failure:
  `results/phase1/20260816Tphase1baselineZ/` (Kissat was initially given an
  unsupported long option; the agreement gate correctly prevented timing).
- Preserved native Windows build failure:
  `results/phase1/build-notes/native-windows-kissat.md`.
- Intermediate successful runs `...baseline02Z` and `...baseline03Z` are kept.
  Run 02 exposed missing peak-RSS parsing for SAT-style exit codes; run 03 fixed
  that issue. Only run 04 is the accepted Phase 1 result.

## What worked

- Both official locked releases build without source changes in one Linux image.
- Both solvers directly consume the exact same mounted DIMACS paths.
- The agreement gate rejected an invalid solver invocation and later passed the
  corrected configuration.
- All requested Phase 1 outputs are present and independently inspectable.

## What failed or remains limited

- Native MinGW Kissat compilation failed on POSIX `SIGALRM`/`alarm` usage.
  Comparisons therefore use the common pinned Linux container, not a patched
  Windows build.
- The workspace initially supplied no benchmark CNFs. Only explicit smoke inputs
  were added, so Phase 1 establishes reproducibility but does not establish a
  performance gain or loss on any target family.
- Docker/WSL2 is part of the measured environment. Future comparisons must keep
  that hardware and environment fixed or start a separately reported campaign.

## Next experiment

Phase 2 should implement the LKK 3.0 structural pipeline exactly as specified,
with conservative UNKNOWN-to-CDCL fallback and no performance claims. Phase 1
artifacts remain the immutable external-CDCL baseline. No Phase 2 work has begun.
