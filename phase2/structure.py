from __future__ import annotations

import itertools
import time
from dataclasses import dataclass

from phase2.cnf import CNF, Clause, RecoveryResult, combinations_as_clauses
from phase2.flow import CapacityInstance


@dataclass(frozen=True)
class GateResult:
    decision: str
    reason: str
    elapsed_ms: float
    signals: dict[str, int]


@dataclass(frozen=True)
class ExactBox:
    box_id: int
    variables: tuple[int, ...]
    demand: int
    evidence_clause_ids: tuple[int, ...]


@dataclass(frozen=True)
class ResourceConstraint:
    resource_id: int
    variables: tuple[int, ...]
    capacity: int
    evidence_clause_ids: tuple[int, ...]


@dataclass(frozen=True)
class RegistryResult:
    status: str
    reason: str
    boxes: tuple[ExactBox, ...]
    resources: tuple[ResourceConstraint, ...]
    family_checks: int
    elapsed_ms: float


@dataclass(frozen=True)
class StructuralModel:
    boxes: tuple[ExactBox, ...]
    resources: tuple[ResourceConstraint, ...]
    capacity_instance: CapacityInstance
    variable_to_resource: dict[int, int]


def fast_cheap_gate(cnf: CNF) -> GateResult:
    start = time.perf_counter_ns()
    positive = 0
    negative = 0
    mixed = 0
    literal_occurrences: dict[int, list[int]] = {}
    clause_set = set(cnf.clauses)
    mirror_pairs = 0
    for clause in cnf.clauses:
        if len(clause) >= 2 and all(literal > 0 for literal in clause):
            positive += 1
        elif len(clause) >= 2 and all(literal < 0 for literal in clause):
            negative += 1
        else:
            mixed += 1
        for literal in clause:
            counts = literal_occurrences.setdefault(abs(literal), [0, 0])
            counts[0 if literal > 0 else 1] += 1
    # Mirror gadgets (C v y) and (C v -y) are a cheap hidden-clause signal.
    for clause in cnf.clauses:
        values = set(clause)
        for literal in clause:
            mirrored = tuple(
                sorted((values - {literal}) | {-literal}, key=lambda x: (abs(x), x < 0))
            )
            if mirrored in clause_set and literal > 0:
                mirror_pairs += 1
    estimated_pairs = sum(pos * neg for pos, neg in literal_occurrences.values())
    signals = {
        "monotone_positive_clauses": positive,
        "monotone_negative_clauses": negative,
        "mixed_clauses": mixed,
        "mirror_pairs": mirror_pairs,
        "estimated_resolution_pairs": estimated_pairs,
    }
    structural = (positive > 0 and negative > 0) or mirror_pairs > 0
    decision = "RUN_LKK" if structural else "SKIP_TO_CDCL"
    reason = "monotone_cardinality_signal" if positive and negative else (
        "mirror_recovery_signal" if mirror_pairs else "no_cheap_structural_signal"
    )
    return GateResult(
        decision,
        reason,
        (time.perf_counter_ns() - start) / 1e6,
        signals,
    )


def benefit_gate(cnf: CNF, gate: GateResult, config: dict[str, int]) -> GateResult:
    start = time.perf_counter_ns()
    signal_count = (
        gate.signals["monotone_positive_clauses"]
        + gate.signals["monotone_negative_clauses"]
        + 2 * gate.signals["mirror_pairs"]
    )
    reasons: list[str] = []
    run = True
    if signal_count < int(config["minimum_signal_count"]):
        run = False
        reasons.append("signal_below_threshold")
    if len(cnf.clauses) > int(config["maximum_clauses"]):
        run = False
        reasons.append("input_clause_limit")
    if gate.signals["estimated_resolution_pairs"] > int(
        config["maximum_estimated_resolution_pairs"]
    ):
        run = False
        reasons.append("estimated_resolution_pair_limit")
    if run:
        reasons.append(
            f"deterministic_accept(signal_count={signal_count},clauses={len(cnf.clauses)})"
        )
    return GateResult(
        "RUN_LKK" if run else "SKIP_TO_CDCL",
        ";".join(reasons),
        (time.perf_counter_ns() - start) / 1e6,
        {"signal_count": signal_count},
    )


def recover_exact_registry(
    recovery: RecoveryResult, config: dict[str, int]
) -> RegistryResult:
    start = time.perf_counter_ns()
    if recovery.status != "COMPLETE":
        return RegistryResult(
            "UNKNOWN",
            f"recovery_{recovery.reason}",
            (),
            (),
            0,
            (time.perf_counter_ns() - start) / 1e6,
        )
    clause_to_id = recovery.clause_to_id
    clauses = tuple(clause_to_id)
    positives = [clause for clause in clauses if len(clause) >= 2 and all(x > 0 for x in clause)]
    negatives = [clause for clause in clauses if len(clause) >= 2 and all(x < 0 for x in clause)]
    budget = int(config["family_check_budget"])
    checks = 0
    candidates: dict[tuple[tuple[int, ...], int], tuple[int, ...]] = {}

    for positive in positives:
        pos_vars = {abs(value) for value in positive}
        for negative in negatives:
            checks += 1
            if checks > budget:
                return RegistryResult(
                    "UNKNOWN", "family_check_budget", (), (), checks,
                    (time.perf_counter_ns() - start) / 1e6,
                )
            neg_vars = {abs(value) for value in negative}
            if len(pos_vars & neg_vars) != 2:
                continue
            group = tuple(sorted(pos_vars | neg_vars))
            demand = len(negative) - 1
            if len(group) != len(positive) + len(negative) - 2:
                continue
            if not 0 < demand < len(group):
                continue
            required = [
                *combinations_as_clauses(group, demand + 1, -1),
                *combinations_as_clauses(group, len(group) - demand + 1, 1),
            ]
            checks += len(required)
            if checks > budget:
                return RegistryResult(
                    "UNKNOWN", "family_check_budget", (), (), checks,
                    (time.perf_counter_ns() - start) / 1e6,
                )
            if all(clause in clause_to_id for clause in required):
                candidates[(group, demand)] = tuple(clause_to_id[c] for c in required)

    # A Boolean assignment variable can belong to only one selected box in the
    # allocation model. Overlapping proven exact constraints are left unused.
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
        return RegistryResult(
            "UNKNOWN", "no_sound_exact_r_boxes", (), (), checks,
            (time.perf_counter_ns() - start) / 1e6,
        )

    variable_to_box = {
        variable: box.box_id for box in selected_boxes for variable in box.variables
    }
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
        for edge in sorted(edges, key=lambda item: tuple(sorted(item))):
            group = set(edge)
            changed = True
            while changed:
                changed = False
                occupied_boxes = {variable_to_box[v] for v in group}
                for candidate in all_variables:
                    if candidate in group or variable_to_box[candidate] in occupied_boxes:
                        continue
                    proposed = group | {candidate}
                    checks += 1
                    if checks > budget:
                        return RegistryResult(
                            "UNKNOWN", "family_check_budget", tuple(selected_boxes), (), checks,
                            (time.perf_counter_ns() - start) / 1e6,
                        )
                    if all(frozenset(combo) in edges for combo in itertools.combinations(proposed, clause_size)):
                        group = proposed
                        changed = True
                        break
            if len(group) <= capacity:
                continue
            group_tuple = tuple(sorted(group))
            required = tuple(
                tuple(sorted((-v for v in combo), key=lambda x: (abs(x), x < 0)))
                for combo in itertools.combinations(group_tuple, clause_size)
            )
            if all(clause in clause_to_id for clause in required):
                resource_candidates[(group_tuple, capacity)] = tuple(
                    clause_to_id[clause] for clause in required
                )

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
        return RegistryResult(
            "UNKNOWN",
            f"incomplete_or_ambiguous_resource_mapping:{missing}",
            tuple(selected_boxes),
            tuple(selected_resources),
            checks,
            (time.perf_counter_ns() - start) / 1e6,
        )
    return RegistryResult(
        "COMPLETE",
        "sound_exact_r_and_capacity_families",
        tuple(selected_boxes),
        tuple(selected_resources),
        checks,
        (time.perf_counter_ns() - start) / 1e6,
    )


def build_structural_model(registry: RegistryResult) -> StructuralModel:
    if registry.status != "COMPLETE":
        raise ValueError("registry is not complete")
    variable_to_resource: dict[int, int] = {}
    for resource in registry.resources:
        for variable in resource.variables:
            if variable in variable_to_resource:
                raise ValueError("ambiguous variable-to-resource mapping")
            variable_to_resource[variable] = resource.resource_id
    reachable = []
    for box in registry.boxes:
        resources = tuple(variable_to_resource[variable] for variable in box.variables)
        if len(set(resources)) != len(resources):
            raise ValueError("box contains multiple variables for one resource")
        reachable.append(resources)
    instance = CapacityInstance(
        tuple(box.demand for box in registry.boxes),
        tuple(resource.capacity for resource in registry.resources),
        tuple(reachable),
    )
    return StructuralModel(registry.boxes, registry.resources, instance, variable_to_resource)
