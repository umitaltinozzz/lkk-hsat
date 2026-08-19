from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from pathlib import Path

from phase2.cnf import Clause, canonical_clause, sha256_file, write_cnf
from phase2.flow import CapacityInstance, flow_box_test


@dataclass(frozen=True)
class AllocationSpec:
    family: str
    demands: tuple[int, ...]
    capacities: tuple[int, ...]
    reachable: tuple[tuple[int, ...], ...]
    hidden: bool = False
    budget_probe: bool = False


def benchmark_specs() -> tuple[AllocationSpec, ...]:
    return (
        AllocationSpec("pigeonhole_exact1", (1, 1, 1, 1), (1, 1, 1), ((0, 1, 2),) * 4),
        AllocationSpec("exact_r_allocation", (2, 2, 2), (1, 1, 1, 1, 1), ((0, 1, 2, 3, 4),) * 3),
        AllocationSpec(
            "bipartite_matching",
            (1, 1, 1, 1),
            (1, 1, 1, 1),
            ((0, 1), (0, 1), (2, 3), (2, 3)),
        ),
        AllocationSpec(
            "overlapping_hall_violation",
            (1, 1, 1, 1, 1),
            (1, 1, 1, 1),
            ((0, 1), (0, 1), (0, 1), (2, 3), (2, 3)),
        ),
        AllocationSpec("satisfiable_capacity_control", (1, 1, 1, 1), (2, 2, 2), ((0, 1, 2),) * 4),
        AllocationSpec("capacity_violation", (1, 1, 1, 1, 1), (2, 2), ((0, 1),) * 5),
        AllocationSpec("hidden_cardinality", (1, 1, 1, 1), (1, 1, 1), ((0, 1, 2),) * 4, True),
        AllocationSpec(
            "recovery_budget_abort_control",
            (1, 1, 1, 1),
            (1, 1, 1),
            ((0, 1, 2),) * 4,
            True,
            True,
        ),
    )


def _direct_allocation_cnf(spec: AllocationSpec) -> tuple[int, list[Clause], dict[tuple[int, int], int]]:
    next_variable = 1
    assignment: dict[tuple[int, int], int] = {}
    for box, resources in enumerate(spec.reachable):
        for resource in resources:
            assignment[(box, resource)] = next_variable
            next_variable += 1
    clauses: list[Clause] = []
    for box, resources in enumerate(spec.reachable):
        group = tuple(assignment[(box, resource)] for resource in resources)
        demand = spec.demands[box]
        for subset in itertools.combinations(group, demand + 1):
            clause = canonical_clause(-value for value in subset)
            assert clause is not None
            clauses.append(clause)
        for subset in itertools.combinations(group, len(group) - demand + 1):
            clause = canonical_clause(subset)
            assert clause is not None
            clauses.append(clause)
    for resource, capacity in enumerate(spec.capacities):
        group = tuple(
            assignment[(box, resource)]
            for box, resources in enumerate(spec.reachable)
            if resource in resources
        )
        for subset in itertools.combinations(group, capacity + 1):
            clause = canonical_clause(-value for value in subset)
            assert clause is not None
            clauses.append(clause)
    return next_variable - 1, clauses, assignment


def _hide_clause(clause: Clause, next_variable: int) -> tuple[list[Clause], int]:
    def canon(values: tuple[int, ...]) -> Clause:
        result = canonical_clause(values)
        assert result is not None
        return result

    if len(clause) <= 2:
        auxiliary = next_variable
        return [canon((*clause, auxiliary)), canon((*clause, -auxiliary))], next_variable + 1
    first_auxiliary = next_variable
    next_variable += 1
    hidden = [canon((clause[0], clause[1], first_auxiliary))]
    if len(clause) == 3:
        hidden.append(canon((-first_auxiliary, clause[2])))
        return hidden, next_variable
    previous = first_auxiliary
    for literal in clause[2:-2]:
        auxiliary = next_variable
        next_variable += 1
        hidden.append(canon((-previous, literal, auxiliary)))
        previous = auxiliary
    hidden.append(canon((-previous, clause[-2], clause[-1])))
    return hidden, next_variable


def _add_planted_noise(
    clauses: list[Clause],
    variables: int,
    noise_percent: int,
    rng: random.Random,
) -> tuple[int, list[Clause], int]:
    target = math.ceil(len(clauses) * noise_percent / 100)
    if target == 0:
        return variables, clauses, 0
    noise_variables = max(8, math.ceil(target / 2))
    pool = list(range(variables + 1, variables + noise_variables + 1))
    planted = {variable: bool(rng.getrandbits(1)) for variable in pool}
    existing = set(clauses)
    added = 0
    attempts = 0
    while added < target and attempts < target * 100 + 100:
        attempts += 1
        selected = rng.sample(pool, 3)
        literals = [variable if rng.getrandbits(1) else -variable for variable in selected]
        if not any((literal > 0) == planted[abs(literal)] for literal in literals):
            literals[0] = selected[0] if planted[selected[0]] else -selected[0]
        clause = canonical_clause(literals)
        assert clause is not None
        if clause in existing:
            continue
        clauses.append(clause)
        existing.add(clause)
        added += 1
    if added != target:
        raise RuntimeError("could not generate requested unique noise clauses")
    return variables + noise_variables, clauses, added


def generate_benchmark(
    spec: AllocationSpec,
    noise_percent: int,
    seed: int,
    output_path: Path,
) -> dict[str, object]:
    rng = random.Random(seed)
    variables, direct_clauses, _ = _direct_allocation_cnf(spec)
    structural_clause_count = len(direct_clauses)
    clauses: list[Clause] = []
    if spec.hidden:
        next_variable = variables + 1
        for clause in direct_clauses:
            hidden, next_variable = _hide_clause(clause, next_variable)
            clauses.extend(hidden)
        variables = next_variable - 1
    else:
        clauses = list(direct_clauses)
    encoded_clause_count = len(clauses)
    variables, clauses, noise_clauses = _add_planted_noise(
        clauses, variables, noise_percent, rng
    )
    flow = flow_box_test(CapacityInstance(spec.demands, spec.capacities, spec.reachable))
    expected = "UNSATISFIABLE" if flow.result == "UNSAT" else "SATISFIABLE"
    write_cnf(
        output_path,
        variables,
        clauses,
        (
            "Generated Phase 2 structural correctness benchmark",
            f"family={spec.family}",
            f"seed={seed}",
            f"noise_percent={noise_percent}",
            f"hidden={str(spec.hidden).lower()}",
            f"ground_truth={expected}",
        ),
    )
    return {
        "instance": output_path.name,
        "family": spec.family,
        "seed": seed,
        "noise_percent": noise_percent,
        "hidden": spec.hidden,
        "budget_probe": spec.budget_probe,
        "variables": variables,
        "clauses": len(clauses),
        "structural_clauses_direct": structural_clause_count,
        "structural_clauses_encoded": encoded_clause_count,
        "noise_clauses": noise_clauses,
        "boxes": len(spec.demands),
        "resources": len(spec.capacities),
        "demands": json_compact(spec.demands),
        "capacities": json_compact(spec.capacities),
        "reachable": json_compact(spec.reachable),
        "expected_result": expected,
        "cnf_sha256": sha256_file(output_path),
    }


def json_compact(value: object) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))
