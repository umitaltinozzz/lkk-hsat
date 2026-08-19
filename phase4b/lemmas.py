"""Independent verification of LemmaBridge output.

Nothing here reuses the native generator. A lemma is only ever accepted when it
survives every check that applies to its origin:

* replay      -- the exported resolution database is re-derived from the CNF
                 alone, so each database clause is proved implied by resolution.
* family      -- a structural lemma must be one of the clauses that the
                 exact-r or capacity family it claims to come from requires,
                 and every clause of that family must be present in the
                 replayed database.
* flow        -- a conditional lemma is re-derived here with the Phase 2
                 max-flow implementation, on a sub-model rebuilt under the
                 coverage rule rather than the one the generator reported.
* implication -- `F and not(lemma)` is UNSAT for a locked solver that had no
                 part in producing the lemma.

A lemma that fails implication checking is a counterexample and stops the
campaign; a lemma that merely exhausts its verification budget is rejected.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from phase2.cnf import Clause, canonical_clause, parse_cnf, write_cnf
from phase2.flow import CapacityInstance, flow_box_test

STRUCTURAL_ORIGINS = {"exact_r_family", "capacity_family"}
RESOLVENT_ORIGIN = "bounded_resolution_resolvent"
CONDITIONAL_ORIGIN = "conditional_capacity_conflict"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_clause(literals: Iterable[int]) -> Clause:
    clause = canonical_clause(literals)
    if clause is None:
        raise ValueError("tautological clause in lemma document")
    return clause


def replay_database(cnf_path: Path, document: dict[str, Any]) -> tuple[dict[Clause, int], list[str]]:
    """Re-derive the exported database from the CNF; return proven clauses."""
    original = set(parse_cnf(cnf_path).clauses)
    records = document["clause_database"]
    proven: dict[Clause, int] = {}
    errors: list[str] = []
    clauses: list[Clause] = []
    for record in records:
        clause = as_clause(record["clause"])
        clauses.append(clause)
        if record["original"]:
            if clause not in original:
                errors.append(f"record {record['id']}: claimed original clause is not in the CNF")
                continue
        else:
            left, right, pivot = record["parent_a"], record["parent_b"], record["pivot"]
            if left is None or right is None or pivot is None or left >= record["id"] or right >= record["id"]:
                errors.append(f"record {record['id']}: malformed or forward resolution parents")
                continue
            left_clause, right_clause = clauses[left], clauses[right]
            if pivot not in left_clause or -pivot not in right_clause:
                errors.append(f"record {record['id']}: pivot {pivot} is not resolvable on its parents")
                continue
            resolvent = canonical_clause((set(left_clause) - {pivot}) | (set(right_clause) - {-pivot}))
            if resolvent != clause:
                errors.append(f"record {record['id']}: resolvent does not match the exported clause")
                continue
        proven[clause] = record["id"]
    return proven, errors


def family_clauses(variables: list[int], count: int, sign: int) -> list[Clause]:
    from itertools import combinations

    return [as_clause(sign * v for v in subset) for subset in combinations(sorted(variables), count)]


def exact_r_family(variables: list[int], demand: int) -> list[Clause]:
    """Clauses asserting exactly `demand` of `variables` are true."""
    return family_clauses(variables, demand + 1, -1) + family_clauses(
        variables, len(variables) - demand + 1, 1
    )


def capacity_family(variables: list[int], capacity: int) -> list[Clause]:
    """Clauses asserting at most `capacity` of `variables` are true."""
    return family_clauses(variables, capacity + 1, -1)


def verify_families(document: dict[str, Any], proven: dict[Clause, int]) -> tuple[set[Clause], list[str]]:
    """Confirm each harvested family is fully present, and return its clauses."""
    supported: set[Clause] = set()
    errors: list[str] = []
    for box in document.get("harvested_boxes", ()):
        required = exact_r_family(list(box["variables"]), int(box["demand"]))
        missing = [c for c in required if c not in proven]
        if missing:
            errors.append(f"box {box['id']}: {len(missing)} exact-r family clauses are not proven")
            continue
        supported.update(required)
    for resource in document.get("harvested_resources", ()):
        required = capacity_family(list(resource["variables"]), int(resource["capacity"]))
        missing = [c for c in required if c not in proven]
        if missing:
            errors.append(f"resource {resource['id']}: {len(missing)} capacity clauses are not proven")
            continue
        supported.update(required)
    return supported, errors


def rebuild_sub_model(document: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Independent re-derivation of the sub-model the generator may use.

    A box survives only when every one of its variables is covered by a
    retained resource group and those groups are distinct.
    """
    cover: dict[int, int] = {}
    for resource in document.get("harvested_resources", ()):
        for variable in resource["variables"]:
            cover[variable] = resource["id"]
    boxes = []
    used_resources: set[int] = set()
    for box in document.get("harvested_boxes", ()):
        if int(box["demand"]) <= 0:
            continue
        groups: set[int] = set()
        if any(v not in cover for v in box["variables"]):
            continue
        for variable in box["variables"]:
            groups.add(cover[variable])
        if len(groups) != len(box["variables"]):
            continue
        boxes.append(box)
        used_resources.update(groups)
    resources = [r for r in document.get("harvested_resources", ()) if r["id"] in used_resources]
    return boxes, resources


def sub_model_instance(
    boxes: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    assume: Iterable[int] = (),
) -> tuple[str, CapacityInstance | None]:
    """Build the capacity instance, optionally conditioned on true literals.

    Returns ``OK`` with an instance, ``OVERCOMMITTED`` when the conditioning
    alone already exceeds a demand or a capacity (which is itself a refutation
    of that conditioning), or ``INAPPLICABLE`` when a variable is not part of
    the sub-model at all.
    """
    index = {resource["id"]: position for position, resource in enumerate(resources)}
    variable_to_resource = {
        v: index[r["id"]] for r in resources for v in r["variables"] if r["id"] in index
    }
    demands = [int(box["demand"]) for box in boxes]
    reachable = [[variable_to_resource[v] for v in box["variables"]] for box in boxes]
    capacities = [int(resource["capacity"]) for resource in resources]
    box_of = {v: position for position, box in enumerate(boxes) for v in box["variables"]}
    for variable in assume:
        if variable not in box_of or variable not in variable_to_resource:
            return "INAPPLICABLE", None
        position = box_of[variable]
        resource = variable_to_resource[variable]
        if resource not in reachable[position]:
            return "INAPPLICABLE", None
        reachable[position].remove(resource)
        demands[position] -= 1
        capacities[resource] -= 1
    if any(d < 0 for d in demands) or any(c < 0 for c in capacities):
        return "OVERCOMMITTED", None
    keep = [i for i, d in enumerate(demands) if d > 0]
    return "OK", CapacityInstance(
        tuple(demands[i] for i in keep),
        tuple(capacities),
        tuple(tuple(reachable[i]) for i in keep),
    )


def verify_structural_unsat(document: dict[str, Any]) -> dict[str, Any]:
    """Re-derive the generator's unconditional sub-model refutation, if claimed."""
    boxes, resources = rebuild_sub_model(document)
    if not boxes:
        return {"rebuilt_boxes": 0, "rebuilt_resources": 0, "result": "NO_SUB_MODEL"}
    status, instance = sub_model_instance(boxes, resources)
    if status != "OK" or instance is None:
        return {"rebuilt_boxes": len(boxes), "rebuilt_resources": len(resources), "result": status}
    flow = flow_box_test(instance)
    return {
        "rebuilt_boxes": len(boxes),
        "rebuilt_resources": len(resources),
        "result": flow.result,
        "maximum_flow": flow.maximum_flow,
        "total_demand": flow.total_demand,
    }


def verify_conditional(document: dict[str, Any], lemma: dict[str, Any]) -> tuple[bool, str]:
    """Re-derive a conditional lemma: conditioning on its support must be infeasible."""
    boxes, resources = rebuild_sub_model(document)
    if not boxes:
        return False, "no_sub_model"
    status, base = sub_model_instance(boxes, resources)
    if status != "OK" or base is None or flow_box_test(base).result != "FEASIBLE":
        return False, "unconditioned_sub_model_not_feasible"
    support = list(lemma["support"])
    if sorted(-v for v in support) != sorted(lemma["clause"]):
        return False, "clause_is_not_the_negated_support"
    status, instance = sub_model_instance(boxes, resources, support)
    if status == "OVERCOMMITTED":
        return True, "conditioning_exceeds_a_proven_capacity_or_demand"
    if status != "OK" or instance is None:
        return False, "conditioning_not_applicable"
    if flow_box_test(instance).result != "UNSAT":
        return False, "conditioned_sub_model_is_feasible"
    return True, "conditioned_sub_model_infeasible"


def implication_check(
    cnf_path: Path,
    lemma_clause: Clause,
    solver_executable: Path,
    solver_args: tuple[str, ...],
    timeout_seconds: float,
    work_dir: Path,
    log_path: Path,
    seed: int = 1,
) -> dict[str, Any]:
    """Run a locked independent solver on `F and not(lemma)`; expect UNSAT."""
    cnf = parse_cnf(cnf_path)
    negation = [as_clause([-literal]) for literal in lemma_clause]
    probe = work_dir / f"{log_path.stem}.cnf"
    write_cnf(
        probe,
        cnf.variables,
        [*cnf.clauses, *negation],
        (
            "LemmaBridge implication probe",
            f"source={cnf_path.name}",
            "lemma=" + " ".join(map(str, lemma_clause)),
            "expected=UNSATISFIABLE",
        ),
    )
    rendered = [a.format(seed=seed, cnf=str(probe)) for a in solver_args]
    command = [
        "/usr/bin/timeout", "--signal=KILL", str(timeout_seconds),
        str(solver_executable), *rendered,
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter_ns()
    with log_path.open("wb") as stream:
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            timeout=timeout_seconds + 5, check=False,
        )
    elapsed = (time.perf_counter_ns() - start) / 1e6
    probe.unlink(missing_ok=True)
    verdict = {10: "SATISFIABLE", 20: "UNSATISFIABLE"}.get(completed.returncode, "UNKNOWN")
    if completed.returncode in {124, 137}:
        verdict = "TIMEOUT"
    return {"verdict": verdict, "runtime_ms": elapsed, "exit_code": completed.returncode,
            "log": log_path.as_posix()}
