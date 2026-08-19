# Phase 2 results - structural correctness validation

Run ID: `20260816Tphase2validation02Z`  
Completed: 2026-08-16T15:45:43.455968+00:00

## What was implemented

- FastCheapGate with monotone-cardinality and mirror-gadget signals. It only routes to LKK or CDCL.
- A transparent deterministic BenefitGate with recorded accept/reject reasons.
- Bounded, non-destructive resolution recovery with time, memory, derived-clause, depth, clause-size, and pivot-occurrence limits. Every derived clause stores a checkable resolution trace.
- Conservative exact-r `B(G,r)` registration without assignment materialization, plus validated resource at-most-capacity families.
- Dinic FlowBoxTest using source-to-box demand edges, unit box-to-resource edges, and resource-to-sink capacity edges.
- Machine-checkable max-flow/min-cut UNSAT witnesses and an independent witness checker.
- Exhaustive subset BoxTest restricted to small correctness-oracle instances.
- External locked CaDiCaL fallback and independent locked CaDiCaL/Kissat cross-checks.

No LemmaBridge, CaDiCaL API integration, learned BenefitGate, or benchmark-performance optimization was implemented.

## Exact commands

```powershell
python -m unittest benchmark.test_harness phase2.test_phase2 -v
powershell -ExecutionPolicy Bypass -File .\scripts\run_phase2.ps1 -RunId 20260816Tphase2validation02Z
```

Configuration snapshot: `config.json`. Oracle seeds are 300000 through 300999.

## Correctness counts

- Random capacity oracle instances: 1000
- FlowBoxTest/exhaustive mismatches: 0
- Generated structural CNFs: 32
- CNFs checked by both locked solvers: 32
- LKK-hybrid/CDCL/ground-truth final mismatches: 0
- Valid structural UNSAT witnesses: 20
- External fallback uses: 12

## Gate behavior

- FastCheapGate: {'RUN_LKK': 32}
- BenefitGate: {'RUN_LKK': 32}
- Structural outcomes: {'UNSAT': 20, 'FEASIBLE': 8, 'UNKNOWN': 4}

Gate and benefit costs are recorded per instance in `hybrid_measurements.csv`. Neither gate makes a SAT/UNSAT decision.

## Recovery and budget behavior

- Recovery statuses: {'COMPLETE': 28, 'ABORT': 4}
- Recovery reasons/aborts: {'fixed_point': 28, 'derived_clause_budget': 4}
- Default budgets: 250.0 ms, 16777216 bytes, 5000 derived clauses, resolution depth 8, derived clause size 12.
- Four explicit negative controls set the derived-clause budget to zero; all four aborted, returned UNKNOWN, and used CDCL fallback.

Every recovery abort routed to UNKNOWN and then external CaDiCaL. Partial recovery data was never used to assert UNSAT.

## Raw outputs

- `correctness.csv`: expected, hybrid, CaDiCaL, and Kissat answers.
- `flow_vs_exhaustive.csv`: all randomized oracle inputs, results, and timings.
- `structural_benchmarks.csv`: generated family/noise inventory and hashes.
- `hybrid_measurements.csv`: separated gate, benefit, recovery, registry, flow, fallback, total, memory, and structural telemetry.
- `logs/`: raw solver logs, hybrid logs, metrics, memory records, and witnesses.
- `failures/`: permanent counterexamples or end-to-end mismatch records, if any.
- `metadata.json`: machine, solver, configuration, and immutability metadata.

## Phase 1 immutability

The accepted Phase 1 tree remained at 64 files with SHA-256 tree digest `1feedb4cfbe2094ccbb1d4646ee2468c07c9aad7969765f151b20d59751175ee` before and after this run.

## Limitations and negative results

- These are generated correctness instances, not evidence that LKK is faster than either CDCL solver.
- Exact-r discovery currently recognizes complete direct combinational clause families or families recovered by the bounded resolution policy. Other sound encodings normally return UNKNOWN.
- Resource recovery is deliberately conservative: every box variable must map unambiguously to one validated capacity group.
- A FEASIBLE flow result never implies general SAT; all such cases use CDCL fallback.
- Noise can cause gate rejection, incomplete registries, or budget aborts. Those outcomes are preserved in the CSV/logs and are correct UNKNOWN behavior.

## Next experiment

Phase 3 should expand randomized CNF-level correctness testing and compare FlowBoxTest with exhaustive BoxTest over additional small encodings. It must retain witness checking and locked-solver cross-checks. No Phase 3 work has begun.
