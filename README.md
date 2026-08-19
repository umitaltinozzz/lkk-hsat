# Mostly Nothing

**A structural front end for SAT solving. Switching the entire thing on, across
954 public benchmark instances, changes the score by 0.03 % and does not solve a
single additional instance.**

That is the headline result, and it took a 24-hour campaign, 4,770 measured runs
and a control arm to establish it honestly. An earlier reading of the same data
looked like a win; a control arm showed the win belonged to our DIMACS parser.

The exception is the one the theory predicts. On the pigeonhole principle --- where
resolution proofs are provably exponential --- the front end answers in 27.76 ms
while both locked baselines exhaust a 600-second budget. It fires exactly where
counting beats resolution, and nowhere else in the public corpus.

This repository is that measurement, including the parts that did not work.

---

## LKK-HSAT

A structural front end for CDCL SAT solving. It recovers cardinality structure
from a CNF by bounded resolution, assembles what it finds into an allocation
model, and **decides that model by max-flow** rather than re-encoding it back
into clauses. When the flow is infeasible the formula is unsatisfiable, and the
front end emits a witness that an independent checker verifies against the
original file.

The full write-up is **[report/paper/main.pdf](report/paper/main.pdf)** (62 pages).

---

## Where it works

`hole_n` is the pigeonhole principle: `n+1` pigeons into `n` holes. Haken's
theorem makes every resolution proof of it exponential in `n`, so a CDCL solver
must eventually fall over. The counting argument settles it in one line.

| Pigeons/holes | Source | LKK front end | Kissat 4.0.4 | CaDiCaL 3.0.1 |
|---|---|---|---|---|
| 7/6 | SATLIB | 4.83 ms | 0.01 s | 0.02 s |
| 8/7 | SATLIB | 6.09 ms | 0.03 s | 0.07 s |
| 9/8 | SATLIB | 7.27 ms | 0.13 s | 0.54 s |
| 10/9 | SATLIB | 10.61 ms | 0.32 s | 3.52 s |
| 11/10 | SATLIB | 13.72 ms | 2.52 s | 81.38 s |
| 12/11 | generated | 14.11 ms | 46.00 s | **timeout (600 s)** |
| 13/12 | generated | 24.98 ms | 502.42 s | **timeout (600 s)** |
| 14/13 | generated | 27.76 ms | **timeout (600 s)** | **timeout (600 s)** |

Every LKK answer is `UNSAT` with a witness that `phase2/witness.py`
independently validated, in each case with `total demand = cut capacity + 1`.
Instances up to ten holes are SATLIB's own files.

## Where it does not

A 24-hour campaign ran five configurations over **954 public instances**
(SATLIB + SAT Competition 2024 main track), 4,770 measured runs at a 60 s
timeout, plus a six-variant ablation.

| Mode | PAR-2 (s) | Ratio | Solved |
|---|---|---|---|
| **Kissat** | **20,746.7** | **1.0000** | 791 |
| LKK + Kissat | 21,149.7 | 1.0194 | 787 |
| LKK + selector | 21,171.5 | 1.0205 | 787 |
| CaDiCaL | 21,630.2 | 1.0426 | 786 |
| LKK + CaDiCaL | 22,019.4 | 1.0613 | 781 |

Plain Kissat wins. Every arm carrying the front end trails it. The ablation is
blunter still: switching the pipeline from fully disabled to fully enabled moves
PAR-2 by **0.03 %** and does not change the solved count at all.

A control arm settles what remained of the ambiguity. `A_lkk_disabled` runs our
parser and an in-process CaDiCaL with every structural stage **off**, and beats
the standalone binary by a median of **−48.8 ms**; with the whole front end
**on** the figure is **−48.6 ms**. The apparent advantage is a parser effect,
not a structural one.

So: the technique is **neutral** on general public benchmarks and decisive on
the family the theory points at. That is a scope, measured rather than asserted.

## Correctness

| Check | Result |
|---|---|
| Instances checked | 954 |
| Arms disagreeing with each other | **0** |
| Instances with an externally declared answer | 338 |
| Arms disagreeing with the declared answer | **0** |
| Regression tests | 59 passing |

Every structural refutation is re-verified by `phase2/witness.py`, which does not
import the engine.

## Worked examples

- [`examples/WORKED_EXAMPLE_hole7.md`](examples/WORKED_EXAMPLE_hole7.md) — one
  instance end to end: CNF, recovered boxes and capacity groups, the flow
  network, the minimum cut, the soundness argument, the checker's verdict.
- [`examples/README_Break_unsat_04_03.md`](examples/README_Break_unsat_04_03.md) —
  the smallest competition instance the campaign decided, with the full CNF
  beside it. It shows the central limitation at a size you can read by hand: 24
  boxes and 22 capacity groups recovered in 4.19 ms, then 36 box variables in no
  group, so the model cannot close.

## Repository layout

| Path | What it is |
|---|---|
| `native/core/` | The accepted semantics: parser, gates, recovery, registry, flow, witness. One header shared by every engine, with a drift test against the sealed Phase 4A binary. |
| `phase2/witness.py` | The independent witness checker. It does not import the engine. |
| `phase4a/` … `phase4c/` | Sealed phases, each with its own Dockerfile, corpus and correctness campaign. |
| `phase5/` | The public-benchmark campaign: acquisition, hash manifests, classification, tuning, telemetry. |
| `phase6/` | One-sided demand boxes, and the pigeonhole scaling measurement. |
| `report/paper/main.pdf` | Final 62-page technical report. Paper source and local build tooling are intentionally not published. |
| `results/` | Campaign artifacts. Summary CSV/JSON tracked; raw solver logs not. |

## Reproducing

Everything runs in Docker with digest-pinned base images and locked solver
versions (`solvers.lock.json`).

```powershell
.\scripts\run_phase5.ps1           # the full campaign (~24 h)

docker build -f Dockerfile.phase5 -t lkk-hsat-phase5:native .
docker run --rm -v "${PWD}:/work" -w /work --entrypoint python3 `
  lkk-hsat-phase5:native -m phase6.scaling --min-holes 6 --max-holes 13

docker run --rm -v "${PWD}:/work" -w /work --entrypoint python3 `
  lkk-hsat-phase5:native -m unittest phase6.test_atleast_boxes phase5.test_baseline_parsing
docker run --rm -v "${PWD}:/work" -w /work --entrypoint python3 `
  lkk-hsat-phase4c:native -m unittest native.core.test_core_drift
```

The 6.4 GB benchmark corpus is not in the repository. `phase5/manifests` carries
the SHA-256 of every file and `phase5/acquire.py` re-downloads it.

---

## Status — work in progress

Known gaps, to be updated here as they close.

- **Single seed.** The campaign ran `evaluation_seeds: 1`. Run-to-run variance is
  large — on one instance the same engine with the same fallback took 16,450 ms
  and 7,193 ms — so the timing comparisons support "no material difference"
  and must not be read directionally. Re-running with three seeds is the fix;
  `config.json` already carries `seeds: [1,2,3]`.
- **The 60 s timeout is short for competition instances**, where the usual budget
  is ~5000 s. Of the SC2024 runs 91 % time out, so that set contributes little
  discriminating information; the comparison is effectively carried by SATLIB.
- **352 instances find structure but cannot close it** — every box variable must
  sit in exactly one capacity group, and they do not. Whether a weaker closure
  condition is still sound is open.
- **Obstructions not handled.** The flow model captures Hall-type counting
  arguments. Parity families (`dubois`, `pret`) are unsatisfiable for reasons
  Gaussian elimination sees and max-flow cannot. A second obstruction detector is
  the obvious next phase.
- **Untested comparisons:** RoundingSat, satsuma, Sat4j-PB, and modern CaDiCaL
  with congruence closure.

## Prior art

Cardinality detection has well-established antecedents — Biere, Le Berre, Lonca
and Manthey (SAT 2014) solved detection and left its use for solving as future
work; Régin (AAAI 1994) is the origin of `alldifferent` filtering; the flow and
Hall arguments are classical. What is claimed here is narrower: *deciding* the
recovered structure instead of re-encoding it, and emitting a certificate a
checker can verify against the original formula. See the paper's related-work
chapter for the full accounting.
