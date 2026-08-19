"""Phase 4C: the accepted registry search, driven by an index instead of a scan.

The accepted Phase 2 registry pairs every monotone-positive clause with every
monotone-negative clause and keeps the pair only when the two share exactly two
variables. That is a full cross product, and `family_check_budget` is spent
almost entirely on pairs that share the wrong number of variables. On the
Phase 4B corpus this is what exhausted the budget before any box was found on
`hidden_pigeonhole` and `satisfiable_capacity_control`, and before the resource
phase could finish on both `overlapping_hall_violation` instances.

This module keeps the acceptance test, the candidate key, the selection order
and the meaning of a "check" exactly as they are. The only change is that pairs
which cannot possibly share exactly two variables are never examined: an
inverted index from a variable pair to the negative clauses containing it yields
precisely the candidate partners.

Equivalence claim, verified by differential testing in `test_phase4c.py`:
with an unbounded budget this returns a result identical to
`phase2.structure.recover_exact_registry`. With a finite budget it can only
examine more useful pairs, never fewer, so it finds a superset of the boxes.
"""

from __future__ import annotations

import itertools
import time
from collections import defaultdict

from phase2.cnf import Clause, RecoveryResult, combinations_as_clauses
from phase2.structure import ExactBox, RegistryResult, ResourceConstraint


def _finish(status: str, reason: str, boxes, resources, checks: int,
            start: int) -> RegistryResult:
    return RegistryResult(status, reason, boxes, resources, checks,
                          (time.perf_counter_ns() - start) / 1e6)


def verify_family(group: tuple[int, ...], demand: int, clause_to_id: dict,
                  checks: int, budget: int) -> tuple[tuple[int, ...] | None, int, bool]:
    """Check an exact-r family one clause at a time.

    The accepted search materialised every required combination and charged the
    budget for all of them before testing a single one, so a candidate rejected
    on its first clause still cost the full family. For a group of eleven with
    demand two that is 176 checks spent to learn nothing, which is why
    `exact_r_allocation` at scale 11 exhausts the budget however fast the
    candidate search itself becomes.

    Generating lazily and charging per membership test gives the same verdict,
    and an unbounded budget therefore yields the same candidate set.

    Returns (evidence or None, checks, budget_exhausted).
    """
    evidence: list[int] = []
    for clause in itertools.chain(
        combinations_as_clauses(group, demand + 1, -1),
        combinations_as_clauses(group, len(group) - demand + 1, 1),
    ):
        checks += 1
        if checks > budget:
            return None, checks, True
        found = clause_to_id.get(clause)
        if found is None:
            return None, checks, False
        evidence.append(found)
    return tuple(evidence), checks, False


def build_pair_index(negatives: list[Clause]) -> dict[tuple[int, int], list[int]]:
    """Map each variable pair to the negative clauses whose variables contain it.

    Clause length is bounded by the recovery configuration, so each clause
    contributes a bounded number of pairs and the index is linear in the
    database for practical inputs.
    """
    index: dict[tuple[int, int], list[int]] = defaultdict(list)
    for position, negative in enumerate(negatives):
        variables = sorted({abs(value) for value in negative})
        for pair in itertools.combinations(variables, 2):
            index[pair].append(position)
    return index


def recover_exact_registry_indexed(
    recovery: RecoveryResult, config: dict[str, int]
) -> RegistryResult:
    start = time.perf_counter_ns()
    if recovery.status != "COMPLETE":
        return _finish("UNKNOWN", f"recovery_{recovery.reason}", (), (), 0, start)

    clause_to_id = recovery.clause_to_id
    clauses = tuple(clause_to_id)
    positives = [c for c in clauses if len(c) >= 2 and all(x > 0 for x in c)]
    negatives = [c for c in clauses if len(c) >= 2 and all(x < 0 for x in c)]
    budget = int(config["family_check_budget"])
    checks = 0
    candidates: dict[tuple[tuple[int, ...], int], tuple[int, ...]] = {}
    rejected: set[tuple[tuple[int, ...], int]] = set()

    pair_index = build_pair_index(negatives)
    negative_variables = [frozenset(abs(v) for v in n) for n in negatives]

    for positive in positives:
        pos_vars = {abs(value) for value in positive}
        # A negative sharing three or more variables appears under several pairs;
        # examining it once keeps the check count faithful to the original.
        examined: set[int] = set()
        for pair in itertools.combinations(sorted(pos_vars), 2):
            for position in pair_index.get(pair, ()):
                if position in examined:
                    continue
                examined.add(position)
                checks += 1
                if checks > budget:
                    return _finish("UNKNOWN", "family_check_budget", (), (), checks, start)
                negative = negatives[position]
                neg_vars = negative_variables[position]
                if len(pos_vars & neg_vars) != 2:
                    continue
                group = tuple(sorted(pos_vars | neg_vars))
                demand = len(negative) - 1
                if len(group) != len(positive) + len(negative) - 2:
                    continue
                if not 0 < demand < len(group):
                    continue
                # The same group is reachable from many positive/negative pairs.
                # Verifying it once, and remembering the refusals too, keeps the
                # budget for groups that have not been decided yet.
                key = (group, demand)
                if key in candidates or key in rejected:
                    continue
                evidence, checks, exhausted = verify_family(
                    group, demand, clause_to_id, checks, budget)
                if exhausted:
                    return _finish("UNKNOWN", "family_check_budget", (), (), checks, start)
                if evidence is None:
                    rejected.add(key)
                else:
                    candidates[key] = evidence

    selected_boxes: list[ExactBox] = []
    used_variables: set[int] = set()
    for (group, demand), evidence in sorted(
        candidates.items(), key=lambda item: (-len(item[0][0]), item[0])
    ):
        if used_variables.intersection(group):
            continue
        selected_boxes.append(ExactBox(len(selected_boxes), group, demand, evidence))
        used_variables.update(group)

    if not selected_boxes:
        return _finish("UNKNOWN", "no_sound_exact_r_boxes", (), (), checks, start)

    variable_to_box = {v: b.box_id for b in selected_boxes for v in b.variables}
    max_capacity = int(config["maximum_resource_capacity"])
    resource_candidates: dict[tuple[tuple[int, ...], int], tuple[int, ...]] = {}

    for clause_size in range(2, max_capacity + 2):
        capacity = clause_size - 1
        edges: set[frozenset[int]] = set()
        for clause in negatives:
            if len(clause) != clause_size:
                continue
            variables = frozenset(abs(value) for value in clause)
            if not variables.issubset(variable_to_box):
                continue
            if len({variable_to_box[v] for v in variables}) != len(variables):
                continue
            edges.add(variables)
        all_variables = sorted(set().union(*edges)) if edges else []
        # Growing a clique can only ever add a vertex adjacent to everything
        # already in the group. Restricting the sweep to that neighbourhood
        # examines the same candidates the scan would accept, without paying for
        # the ones it would certainly reject.
        neighbours: dict[int, set[int]] = defaultdict(set)
        if clause_size == 2:
            for edge in edges:
                a, b = sorted(edge)
                neighbours[a].add(b)
                neighbours[b].add(a)
        for edge in sorted(edges, key=lambda item: tuple(sorted(item))):
            group = set(edge)
            # The candidate pool and the occupied-box set are properties of the
            # group, and adding one vertex changes each by one intersection or
            # one insertion. Rebuilding them from scratch on every growth step
            # made a k-vertex clique cost O(k^2) sweeps instead of O(k). The
            # sweep contents and their order are unchanged, so the vertex that
            # gets accepted first is the same one.
            occupied_boxes = {variable_to_box[v] for v in group}
            pool: set[int] | None = None
            if clause_size == 2:
                pool = set(all_variables)
                for member in group:
                    pool &= neighbours[member]
            changed = True
            while changed:
                changed = False
                sweep = sorted(pool) if pool is not None else all_variables
                members = sorted(group)
                exhausted = False
                for candidate in sweep:
                    if candidate in group or variable_to_box[candidate] in occupied_boxes:
                        continue
                    # `group` is already a clique, so every subset that omits the
                    # candidate is known to be an edge and only the subsets
                    # containing it can fail: C(|group|, size-1) lookups rather
                    # than C(|group|+1, size), stopping at the first miss.
                    #
                    # Each lookup is charged, because charging one check for a
                    # candidate that costs |group| lookups is what let the
                    # registry spend four seconds on a 24-box instance while the
                    # budget still looked untouched. Charging the work actually
                    # done keeps the budget a bound on effort, deterministically.
                    accepted = True
                    for rest in itertools.combinations(members, clause_size - 1):
                        checks += 1
                        if checks > budget:
                            exhausted = True
                            break
                        if frozenset((candidate, *rest)) not in edges:
                            accepted = False
                            break
                    if exhausted:
                        return _finish("UNKNOWN", "family_check_budget",
                                       tuple(selected_boxes), (), checks, start)
                    if accepted:
                        group.add(candidate)
                        occupied_boxes.add(variable_to_box[candidate])
                        if pool is not None:
                            pool &= neighbours[candidate]
                        changed = True
                        break
            if len(group) <= capacity:
                continue
            group_tuple = tuple(sorted(group))
            # These lookups were previously free of charge, which is the same
            # defect as the two Phase 4C fixed elsewhere: work the budget cannot
            # see. Charged one per membership test, stopping at the first miss.
            evidence: list[int] = []
            accepted_group = True
            for combo in itertools.combinations(group_tuple, clause_size):
                clause = tuple(sorted((-v for v in combo), key=lambda x: (abs(x), x < 0)))
                checks += 1
                if checks > budget:
                    return _finish("UNKNOWN", "family_check_budget",
                                   tuple(selected_boxes), (), checks, start)
                found = clause_to_id.get(clause)
                if found is None:
                    accepted_group = False
                    break
                evidence.append(found)
            if accepted_group:
                resource_candidates[(group_tuple, capacity)] = tuple(evidence)

    selected_resources: list[ResourceConstraint] = []
    resource_variables: set[int] = set()
    for (group, capacity), evidence in sorted(
        resource_candidates.items(), key=lambda item: (-len(item[0][0]), item[0][1], item[0][0])
    ):
        if resource_variables.intersection(group):
            continue
        selected_resources.append(
            ResourceConstraint(len(selected_resources), group, capacity, evidence)
        )
        resource_variables.update(group)

    if used_variables != resource_variables:
        missing = sorted(used_variables - resource_variables)
        return _finish("UNKNOWN", f"incomplete_or_ambiguous_resource_mapping:{missing}",
                       tuple(selected_boxes), tuple(selected_resources), checks, start)
    return _finish("COMPLETE", "sound_exact_r_and_capacity_families",
                   tuple(selected_boxes), tuple(selected_resources), checks, start)
