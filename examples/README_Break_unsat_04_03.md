# `Break_unsat_04_03.xml.cnf` â€” a SAT Competition 2024 instance, in full

The CNF itself is beside this file. It is the smallest instance in the 2024 main
track that our campaign decided, small enough to read by hand: **221 variables,
1085 clauses, 11 KB**. Everything below is taken from the campaign artifacts,
not written by hand.

## What the file looks like

```
p cnf 221 1085
55 37 19 13 7 1 0        <- at least one of these six holds
-55 -37 0                <- ... and no two of them hold together
-55 -19 0
-37 -19 0
-13 -7 0
-13 -1 0
...
61 43 31 25 19 1 0       <- the next such group
-61 -43 0
```

That shape is a **cardinality constraint written out in clauses**: one positive
clause saying *at least one*, plus every pairwise negative clause saying *at most
one*. Together they say **exactly one**. The name suggests a break-scheduling
problem converted from XCSP; the `_unsat_` in it is the expected answer.

This is exactly the structure LKK-HSAT is built to recover, and on this instance
it does recover it.

## What the front end found

| | |
|---|---|
| gate | `RUN_LKK` |
| benefit gate | `RUN_LKK` |
| recovery | `COMPLETE` |
| **exact-r boxes found** | **24** |
| **capacity groups found** | **22** |
| registry | `UNKNOWN` |
| structural result | `UNKNOWN` |
| front-end cost | 4.19 ms |

So the recovery step worked: 24 demand boxes and 22 capacity groups came back out
of a formula that only ever mentions clauses. The structure really is in there
and we really do find it.

## Why it still did not decide anything

```
registry_reason: incomplete_or_ambiguous_resource_mapping:
  [25, 26, 27, 28, 29, 30, 73, 74, ... 114, 116]      (36 variables)
```

The flow argument needs every box variable to sit in **exactly one** capacity
group â€” that is what makes a true variable one unit of flow from its box to its
resource. Here 36 of the box variables belong to no selected group at all, so the
model cannot be closed and the registry refuses to guess. The engine hands over
to CDCL.

This is the central limitation of the technique, and this instance is the
cheapest place to see it: **the structure is found, the model cannot be built
from it.** Not "there is nothing here" â€” 24 boxes is a lot â€” but "what is here
does not form a clean allocation network".

## What it cost

| mode | time | conflicts |
|---|---|---|
| CaDiCaL 3.0.1 | 66.9 ms | 0 |
| Kissat 4.0.4 | 67.7 ms | 555 |
| LKK + CaDiCaL | 35.4 ms | 458 |
| LKK + Kissat | 118.0 ms | 412 |
| LKK + selector | 117.7 ms | 412 |

All five agree it is unsatisfiable. The front end cost 4.19 ms and bought
nothing on this instance. Note the spread between arms that ran the *same*
fallback with the same front-end verdict â€” 35 ms against 118 ms â€” which is the
run-to-run variance discussed in the results chapter, not a property of the
front end.

## Reproducing

```powershell
docker run --rm -v "${PWD}:/work" -w /work `
  --entrypoint /opt/solvers/bin/lkk-native5 lkk-hsat-phase5:native `
  --cnf /work/examples/Break_unsat_04_03.xml.cnf `
  --sha256 (Get-FileHash examples\Break_unsat_04_03.xml.cnf -Algorithm SHA256).Hash.ToLower() `
  --telemetry-only --fallback none --atleast-boxes
```

Source: SAT Competition 2024 main track,
[zenodo.org/records/15095752](https://zenodo.org/records/15095752).
Per-instance metadata: [benchmark-database.de](https://benchmark-database.de?track=main_2024).

## Anatomy of the file

DIMACS carries nothing but numbers, yet the layers survive compilation and can be
read back. Counted from the file itself:

| clause shape | count | meaning |
|---|---|---|
| 6 positive | 60 | at least one of these six |
| 2 negative | 612 | not both of these two |
| 1 positive + 1 negative | 312 | implication (channelling) |
| 1 positive + 2 negative | 74 | logic gates |
| 3 positive | 20 | counter network |
| **1 negative** | **1** | `-221 0` |

Variable blocks, by how many clauses mention them:

```
1  - 72    decision variables   (924 clauses)
73 - 96    channelling          (184)
97 - 116   XOR gates            (85)
118- 177   auxiliary            (360)
178- 221   counter network      (121)
```

The most informative line is the last one, `-221 0`. Variable 221 is the top
output of the counter built from variables 178-221, so that single unit clause
asserts a **cardinality bound**: the count must not reach this level. The
`unsat` in the filename is the answer at that bound.

So the instance is an **optimisation problem compiled to SAT** - plausibly
"schedule the fixtures with at most k breaks" - where a counter network was
synthesised and one unit clause pinned the bound.

## Why this instance is the argument for the whole technique

Those 60 at-least-one clauses together with the 612 pairwise negatives are
**60 exactly-one constraints**. In the original model each was a single line.
Compiling to CNF scattered each one across 672 clauses and discarded the fact
that they were constraints at all; to CDCL they are now just clauses.

The recovery step puts them back together - 24 boxes and 22 groups here, a subset
of the 60, because boxes must be pairwise disjoint and the selection is greedy.

And then it stops, because 36 box variables lie in no capacity group. The reason
is now visible in the anatomy above: the counter network at 178-221 is not part of
any allocation structure. It is a different kind of object, and the flow model has
no vocabulary for it.