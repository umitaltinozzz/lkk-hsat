from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from phase2.cnf import (
    CNF,
    ClauseRecord,
    RecoveryResult,
    canonical_clause,
    combinations_as_clauses,
    parse_cnf,
)
from phase2.flow import FlowResult
from phase2.structure import StructuralModel


class WitnessError(ValueError):
    pass


def create_witness(
    cnf: CNF,
    recovery: RecoveryResult,
    model: StructuralModel,
    flow: FlowResult,
) -> dict[str, Any]:
    if flow.result != "UNSAT" or flow.witness is None:
        raise WitnessError("an UNSAT flow result is required")
    return {
        "format": "lkk-hsat-capacity-witness-v1",
        "cnf_sha256": cnf.sha256,
        "cnf_variables": cnf.variables,
        "cnf_clause_count": len(cnf.clauses),
        "clause_database": [
            {
                "id": record.clause_id,
                "clause": list(record.clause),
                "depth": record.depth,
                "original": record.original,
                "parent_a": record.parent_a,
                "parent_b": record.parent_b,
                "pivot": record.pivot,
            }
            for record in recovery.records
        ],
        "boxes": [
            {
                "id": box.box_id,
                "variables": list(box.variables),
                "demand": box.demand,
                "evidence_clause_ids": list(box.evidence_clause_ids),
            }
            for box in model.boxes
        ],
        "resources": [
            {
                "id": resource.resource_id,
                "variables": list(resource.variables),
                "capacity": resource.capacity,
                "evidence_clause_ids": list(resource.evidence_clause_ids),
            }
            for resource in model.resources
        ],
        "variable_to_resource": {
            str(variable): resource
            for variable, resource in sorted(model.variable_to_resource.items())
        },
        "flow": {
            "total_demand": flow.total_demand,
            "maximum_flow": flow.maximum_flow,
            "nodes": flow.flow_nodes,
            "edges": flow.flow_edges,
            "reachable_boxes": list(flow.witness.reachable_boxes),
            "reachable_resources": list(flow.witness.reachable_resources),
            "source_cut_capacity": flow.witness.source_cut_capacity,
            "box_resource_cut_capacity": flow.witness.box_resource_cut_capacity,
            "resource_cut_capacity": flow.witness.resource_cut_capacity,
            "cut_capacity": flow.witness.cut_capacity,
        },
    }


def _rebuild_clause_database(cnf: CNF, data: list[dict[str, Any]]) -> list[tuple[int, ...]]:
    unique_originals: list[tuple[int, ...]] = []
    seen: set[tuple[int, ...]] = set()
    for clause in cnf.clauses:
        if clause not in seen:
            seen.add(clause)
            unique_originals.append(clause)
    clauses: list[tuple[int, ...]] = []
    for index, record in enumerate(data):
        if record["id"] != index:
            raise WitnessError("clause IDs are not sequential")
        clause = canonical_clause(record["clause"])
        if clause is None:
            raise WitnessError("tautological proof clause")
        if record["original"]:
            if index >= len(unique_originals) or clause != unique_originals[index]:
                raise WitnessError("original clause record does not match CNF")
            if record["depth"] != 0:
                raise WitnessError("original clause has nonzero depth")
        else:
            parent_a = int(record["parent_a"])
            parent_b = int(record["parent_b"])
            pivot = int(record["pivot"])
            if not (0 <= parent_a < index and 0 <= parent_b < index):
                raise WitnessError("invalid resolution parent")
            left, right = set(clauses[parent_a]), set(clauses[parent_b])
            if pivot not in left or -pivot not in right:
                raise WitnessError("resolution pivot is absent from its parents")
            complementary = {abs(value) for value in left if -value in right}
            if complementary != {abs(pivot)}:
                raise WitnessError("resolution step has extra complementary pivots")
            expected = canonical_clause((left - {pivot}) | (right - {-pivot}))
            if expected != clause:
                raise WitnessError("incorrect resolution result")
        clauses.append(clause)
    if clauses[: len(unique_originals)] != unique_originals:
        raise WitnessError("proof omits or reorders original clauses")
    return clauses


def _evidence_ids(clauses: list[Any], required: list[Any], what: str) -> set[int]:
    """Locate every required clause, or say which one the database lacks.

    A witness that names a family the database cannot supply is invalid, not
    malformed, so it has to fail as a WitnessError like every other rejection.
    """
    found = set()
    for clause in required:
        try:
            found.add(clauses.index(clause))
        except ValueError:
            raise WitnessError(
                f"{what} evidence missing from the clause database: {clause}") from None
    return found


def check_witness(cnf_path: Path, witness_path: Path) -> dict[str, Any]:
    cnf = parse_cnf(cnf_path)
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    if witness.get("format") != "lkk-hsat-capacity-witness-v1":
        raise WitnessError("unknown witness format")
    if witness.get("cnf_sha256") != cnf.sha256:
        raise WitnessError("CNF hash mismatch")
    clauses = _rebuild_clause_database(cnf, witness["clause_database"])

    box_variables: set[int] = set()
    demands: list[int] = []
    box_groups: list[tuple[int, ...]] = []
    for expected_id, box in enumerate(witness["boxes"]):
        if box["id"] != expected_id:
            raise WitnessError("non-sequential box ID")
        group = tuple(sorted(map(int, box["variables"])))
        demand = int(box["demand"])
        if box_variables.intersection(group):
            raise WitnessError("overlapping exact-r boxes")
        box_variables.update(group)
        # The cut below compares the cut capacity against the summed demands and
        # never against an upper bound, so a box only has to prove
        # sum(group) >= demand. A box may say so directly, in which case the
        # at-most-r half of the family is neither required nor accepted; a box
        # that omits the marker still has to prove the full exact-r family, so
        # witnesses written before this distinction existed are read unchanged.
        kind = box.get("kind", "exact")
        if kind not in ("exact", "atleast"):
            raise WitnessError(f"unknown box kind: {kind}")
        required = list(combinations_as_clauses(group, len(group) - demand + 1, 1))
        if kind == "exact":
            required += combinations_as_clauses(group, demand + 1, -1)
        evidence = set(map(int, box["evidence_clause_ids"]))
        if any(not 0 <= clause_id < len(clauses) for clause_id in evidence):
            raise WitnessError("box evidence ID out of range")
        if any(clauses[clause_id] not in required for clause_id in evidence):
            raise WitnessError("irrelevant box evidence")
        if _evidence_ids(clauses, required, f"{kind} demand") != evidence:
            raise WitnessError(f"incomplete {kind} demand evidence")
        demands.append(demand)
        box_groups.append(group)

    resource_variables: set[int] = set()
    capacities: list[int] = []
    resource_groups: list[tuple[int, ...]] = []
    for expected_id, resource in enumerate(witness["resources"]):
        if resource["id"] != expected_id:
            raise WitnessError("non-sequential resource ID")
        group = tuple(sorted(map(int, resource["variables"])))
        capacity = int(resource["capacity"])
        if resource_variables.intersection(group):
            raise WitnessError("overlapping resources")
        resource_variables.update(group)
        required = list(combinations_as_clauses(group, capacity + 1, -1))
        evidence = set(map(int, resource["evidence_clause_ids"]))
        if _evidence_ids(clauses, required, "capacity") != evidence:
            raise WitnessError("incomplete capacity evidence")
        capacities.append(capacity)
        resource_groups.append(group)
    if box_variables != resource_variables:
        raise WitnessError("box/resource variable coverage differs")

    variable_to_resource = {
        int(variable): int(resource)
        for variable, resource in witness["variable_to_resource"].items()
    }
    expected_mapping = {
        variable: resource_id
        for resource_id, group in enumerate(resource_groups)
        for variable in group
    }
    if variable_to_resource != expected_mapping:
        raise WitnessError("variable-to-resource mapping is incorrect")
    reachable = [tuple(variable_to_resource[v] for v in group) for group in box_groups]
    if any(len(set(resources)) != len(resources) for resources in reachable):
        raise WitnessError("a box has duplicate edges to a resource")

    flow = witness["flow"]
    reached_boxes = set(map(int, flow["reachable_boxes"]))
    reached_resources = set(map(int, flow["reachable_resources"]))
    source_cut = sum(d for index, d in enumerate(demands) if index not in reached_boxes)
    edge_cut = sum(
        1
        for box in reached_boxes
        for resource in reachable[box]
        if resource not in reached_resources
    )
    resource_cut = sum(
        capacity for index, capacity in enumerate(capacities) if index in reached_resources
    )
    cut_capacity = source_cut + edge_cut + resource_cut
    total_demand = sum(demands)
    if not (
        source_cut == flow["source_cut_capacity"]
        and edge_cut == flow["box_resource_cut_capacity"]
        and resource_cut == flow["resource_cut_capacity"]
        and cut_capacity == flow["cut_capacity"]
        and total_demand == flow["total_demand"]
        and cut_capacity < total_demand
    ):
        raise WitnessError("invalid capacity cut")
    return {
        "valid": True,
        "cnf_sha256": cnf.sha256,
        "boxes": len(box_groups),
        "resources": len(resource_groups),
        "total_demand": total_demand,
        "cut_capacity": cut_capacity,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check an LKK capacity witness")
    parser.add_argument("cnf", type=Path)
    parser.add_argument("witness", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(check_witness(args.cnf, args.witness), sort_keys=True))
        return 0
    except (WitnessError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
