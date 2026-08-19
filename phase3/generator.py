from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from phase2.cnf import Clause, canonical_clause, sha256_file, write_cnf
from phase2.generator import (
    AllocationSpec,
    _add_planted_noise,
    _direct_allocation_cnf,
    _hide_clause,
    json_compact,
)
from phase2.flow import CapacityInstance, flow_box_test


@dataclass(frozen=True)
class BenchmarkRequest:
    family: str
    scale: int
    noise_percent: int = 0
    variant: str = "default"


def allocation_for_request(request: BenchmarkRequest) -> AllocationSpec:
    scale = request.scale
    if request.family == "pigeonhole_exact1":
        return AllocationSpec(
            request.family,
            (1,) * scale,
            (1,) * (scale - 1),
            (tuple(range(scale - 1)),) * scale,
        )
    if request.family == "exact_r_allocation":
        resources = scale
        boxes = resources // 2 + 1
        return AllocationSpec(
            request.family,
            (2,) * boxes,
            (1,) * resources,
            (tuple(range(resources)),) * boxes,
        )
    if request.family == "overlapping_hall_violation":
        core_resources = scale
        core_boxes = scale + 1
        # Add an equally sized satisfiable component so the violation is local,
        # not just visible from total demand versus total capacity.
        offset = core_resources
        filler_resources = tuple(range(offset, offset + scale + 1))
        reachable = [(tuple(range(core_resources))) for _ in range(core_boxes)]
        reachable.extend(filler_resources for _ in range(scale))
        return AllocationSpec(
            request.family,
            (1,) * (core_boxes + scale),
            (1,) * (2 * scale + 1),
            tuple(reachable),
        )
    if request.family == "capacity2_violation":
        resources = scale
        boxes = 2 * resources + 1
        return AllocationSpec(
            request.family,
            (1,) * boxes,
            (2,) * resources,
            (tuple(range(resources)),) * boxes,
        )
    if request.family == "hidden_pigeonhole":
        return AllocationSpec(
            request.family,
            (1,) * scale,
            (1,) * (scale - 1),
            (tuple(range(scale - 1)),) * scale,
            True,
        )
    if request.family == "satisfiable_capacity_control":
        return AllocationSpec(
            request.family,
            (1,) * scale,
            (1,) * scale,
            (tuple(range(scale)),) * scale,
        )
    raise ValueError(f"not an allocation family: {request.family}")


def generate_allocation(
    request: BenchmarkRequest, seed: int, output_path: Path
) -> dict[str, object]:
    spec = allocation_for_request(request)
    variables, direct_clauses, _ = _direct_allocation_cnf(spec)
    clauses: list[Clause] = []
    if spec.hidden:
        next_variable = variables + 1
        for clause in direct_clauses:
            hidden, next_variable = _hide_clause(clause, next_variable)
            clauses.extend(hidden)
        variables = next_variable - 1
    else:
        clauses = list(direct_clauses)
    encoded_structural_clauses = len(clauses)
    variables, clauses, noise_added = _add_planted_noise(
        clauses, variables, request.noise_percent, random.Random(seed)
    )
    flow = flow_box_test(CapacityInstance(spec.demands, spec.capacities, spec.reachable))
    expected = "UNSATISFIABLE" if flow.result == "UNSAT" else "SATISFIABLE"
    write_cnf(
        output_path,
        variables,
        clauses,
        (
            "Phase 3 generated performance instance",
            f"family={request.family}",
            f"scale={request.scale}",
            f"variant={request.variant}",
            f"noise_percent={request.noise_percent}",
            f"expected={expected}",
        ),
    )
    return {
        "instance": output_path.name,
        "family": request.family,
        "scale": request.scale,
        "variant": request.variant,
        "noise_percent": request.noise_percent,
        "seed": seed,
        "variables": variables,
        "clauses": len(clauses),
        "direct_structural_clauses": len(direct_clauses),
        "encoded_structural_clauses": encoded_structural_clauses,
        "noise_clauses": noise_added,
        "boxes": len(spec.demands),
        "resources": len(spec.capacities),
        "demands": json_compact(spec.demands),
        "capacities": json_compact(spec.capacities),
        "reachable": json_compact(spec.reachable),
        "expected_result": expected,
        "cnf_sha256": sha256_file(output_path),
    }


def generate_random_3sat(
    request: BenchmarkRequest, seed: int, output_path: Path
) -> dict[str, object]:
    rng = random.Random(seed)
    variables = request.scale
    ratio = 4.26
    clause_count = round(variables * ratio)
    planted = request.variant == "planted_sat"
    assignment = {
        variable: bool(rng.getrandbits(1)) for variable in range(1, variables + 1)
    }
    clauses: list[Clause] = []
    seen: set[Clause] = set()
    while len(clauses) < clause_count:
        selected = rng.sample(range(1, variables + 1), 3)
        literals = [value if rng.getrandbits(1) else -value for value in selected]
        if planted and not any(
            (literal > 0) == assignment[abs(literal)] for literal in literals
        ):
            first = selected[0]
            literals[0] = first if assignment[first] else -first
        clause = canonical_clause(literals)
        assert clause is not None
        if clause in seen:
            continue
        seen.add(clause)
        clauses.append(clause)
    expected = "SATISFIABLE" if planted else "TO_VALIDATE"
    write_cnf(
        output_path,
        variables,
        clauses,
        (
            "Phase 3 random 3-SAT negative control",
            f"seed={seed}",
            f"ratio={ratio}",
            f"variant={request.variant}",
            f"expected={expected}",
        ),
    )
    return {
        "instance": output_path.name,
        "family": "random_3sat",
        "scale": variables,
        "variant": request.variant,
        "noise_percent": 0,
        "seed": seed,
        "variables": variables,
        "clauses": clause_count,
        "direct_structural_clauses": 0,
        "encoded_structural_clauses": 0,
        "noise_clauses": clause_count,
        "boxes": 0,
        "resources": 0,
        "demands": "[]",
        "capacities": "[]",
        "reachable": "[]",
        "expected_result": expected,
        "cnf_sha256": sha256_file(output_path),
    }


def generate_request(
    request: BenchmarkRequest, seed: int, output_path: Path
) -> dict[str, object]:
    if request.family == "random_3sat":
        return generate_random_3sat(request, seed, output_path)
    return generate_allocation(request, seed, output_path)
