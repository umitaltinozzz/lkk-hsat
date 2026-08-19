# Worked example: `hole7.cnf`, from DIMACS to a checked refutation

Every number below is read out of the actual files, not written by hand.

## 1. The formula

- File: `benchmarks/phase5/satlib/pigeon-hole/hole7.cnf` (SATLIB)
- SHA-256: `225791cbb02429d71bae87f331460d456ee11fecc384a6af049813f42e6319ab`
- 56 variables, 204 clauses
- 8 all-positive clauses, 196 all-negative clauses

It asks whether 8 pigeons fit into 7 holes, one pigeon per hole. Variable
`7*(p-1)+h` means *pigeon p occupies hole h*. The encoding says only two things:

```
each pigeon occupies some hole      7 6 5 4 3 2 1 0
no two pigeons share a hole         -1 -8 0
```

There are 8 clauses of the first kind (one per pigeon, width 7) and
196 of the second (one per hole per pair of pigeons).

Note what is **absent**: nothing says a pigeon occupies *at most* one hole.
The refutation does not need it, so the standard encoding omits it - and
requiring it anyway is the defect Phase 6 fixed.

## 2. What the front end recovered

**8 demand boxes**, one per pigeon. Each says *at least one of
these seven variables is true*, proved by a single positive clause:

| box | demand | kind | variables | evidence |
|---|---|---|---|---|
| 0 | >= 1 | atleast | 1, 2, 3, 4, 5, 6, 7 | clause 196 |
| 1 | >= 1 | atleast | 8, 9, 10, 11, 12, 13, 14 | clause 197 |
| 2 | >= 1 | atleast | 15, 16, 17, 18, 19, 20, 21 | clause 198 |
| 3 | >= 1 | atleast | 22, 23, 24, 25, 26, 27, 28 | clause 199 |
| 4 | >= 1 | atleast | 29, 30, 31, 32, 33, 34, 35 | clause 200 |
| 5 | >= 1 | atleast | 36, 37, 38, 39, 40, 41, 42 | clause 201 |
| 6 | >= 1 | atleast | 43, 44, 45, 46, 47, 48, 49 | clause 202 |
| 7 | >= 1 | atleast | 50, 51, 52, 53, 54, 55, 56 | clause 203 |

**7 capacity groups**, one per hole. Each says *at most one
of these eight variables is true*, proved by all 28 pairwise clauses:

| group | capacity | variables | evidence clauses |
|---|---|---|---|
| 0 | <= 1 | 1, 8, 15, 22, 29, 36, 43, 50 | 28 clauses |
| 1 | <= 1 | 2, 9, 16, 23, 30, 37, 44, 51 | 28 clauses |
| 2 | <= 1 | 3, 10, 17, 24, 31, 38, 45, 52 | 28 clauses |
| 3 | <= 1 | 4, 11, 18, 25, 32, 39, 46, 53 | 28 clauses |
| 4 | <= 1 | 5, 12, 19, 26, 33, 40, 47, 54 | 28 clauses |
| 5 | <= 1 | 6, 13, 20, 27, 34, 41, 48, 55 | 28 clauses |
| 6 | <= 1 | 7, 14, 21, 28, 35, 42, 49, 56 | 28 clauses |

Boxes partition the 56 variables by pigeon; groups partition them by hole.
Both partitions cover every variable, which is what the flow argument needs.

## 3. The flow network

```
           demand                         capacity
  source -----------> box p ---------> group h -----------> sink
            r_p = 1     (pigeon)  1        (hole)   c_h = 1
```
- total demand into the boxes: **8**
- maximum flow the network admits: **7**
- 17 nodes, 71 edges

The minimum cut, reported so a checker can recompute it:

| cut component | capacity |
|---|---|
| source side | 0 |
| box to group edges | 0 |
| group to sink | 7 |
| **total cut** | **7** |

**7 < 8**, so the demands cannot all be met, so the formula is
unsatisfiable. That is the whole proof: eight pigeons, seven holes.

## 4. Why this is sound

Let a be any satisfying assignment and f(p,h) count the variables in box p and
group h that a sets true. Because the groups are disjoint and cover every box
variable, sum over h of f(p,h) >= 1 for each pigeon. Because the boxes are
disjoint, sum over p of f(p,h) <= 1 for each hole. So a would give a feasible
flow meeting every demand - which the cut above rules out. Hence no such a
exists.

Only the *lower* bounds on boxes and *upper* bounds on groups are used. The
at-most-r half of an exact-r family never appears, which is why demanding it
as evidence was wrong.

## 5. Independent verification

`phase2/witness.py` does not import the engine. It re-parses the CNF, rebuilds
every clause the witness cites, re-derives the cut and compares:

```
$ python3 -m phase2.witness hole7.cnf hole7_witness.json
{"boxes": 8, "cnf_sha256": "225791cb...", "cut_capacity": 7, "resources": 7, "total_demand": 8, "valid": true}
```

The witness carries 204 clause records, each marked original or derived
with its resolution parents, so nothing is taken on trust.

## 6. Measured cost

| solver | time | result |
|---|---|---|
| CaDiCaL 3.0.1 | 117.2 ms | UNSATISFIABLE |
| Kissat 4.0.4 | 66.4 ms | UNSATISFIABLE |
| **LKK-HSAT front end** | **17.8 ms** | **UNSATISFIABLE, with the witness above** |

The gap widens with n, because resolution proofs of the pigeonhole principle
are exponential (Haken 1985) while the counting argument is not. At 14 pigeons
and 13 holes the front end answers in 27.76 ms and both baselines exhaust a
600 s budget - see `results/phase6/scaling.json`.
