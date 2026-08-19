from __future__ import annotations

import hashlib
import itertools
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


Clause = tuple[int, ...]


class CnfError(ValueError):
    pass


def canonical_clause(literals: Iterable[int]) -> Clause | None:
    values = set(int(lit) for lit in literals)
    if 0 in values:
        raise CnfError("zero is a DIMACS terminator, not a literal")
    if any(-lit in values for lit in values):
        return None
    return tuple(sorted(values, key=lambda lit: (abs(lit), lit < 0)))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class CNF:
    path: Path
    variables: int
    clauses: tuple[Clause, ...]
    comments: tuple[str, ...]
    sha256: str


def parse_cnf(path: Path) -> CNF:
    comments: list[str] = []
    clauses: list[Clause] = []
    current: list[int] = []
    header: tuple[int, int] | None = None
    with path.open("r", encoding="ascii") as stream:
        for line_number, raw in enumerate(stream, 1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("c"):
                comments.append(line[1:].strip())
                continue
            if line.startswith("p"):
                parts = line.split()
                if header is not None or len(parts) != 4 or parts[:2] != ["p", "cnf"]:
                    raise CnfError(f"{path}:{line_number}: invalid or duplicate header")
                header = (int(parts[2]), int(parts[3]))
                continue
            if header is None:
                raise CnfError(f"{path}:{line_number}: data before header")
            for token in line.split():
                literal = int(token)
                if literal == 0:
                    clause = canonical_clause(current)
                    if clause is None:
                        raise CnfError(f"{path}:{line_number}: tautological input clause")
                    clauses.append(clause)
                    current = []
                else:
                    if abs(literal) > header[0]:
                        raise CnfError(f"{path}:{line_number}: literal exceeds header")
                    current.append(literal)
    if header is None or current:
        raise CnfError(f"{path}: missing header or unterminated final clause")
    if len(clauses) != header[1]:
        raise CnfError(f"{path}: expected {header[1]} clauses, parsed {len(clauses)}")
    return CNF(path, header[0], tuple(clauses), tuple(comments), sha256_file(path))


def write_cnf(path: Path, variables: int, clauses: Iterable[Clause], comments: Iterable[str]) -> None:
    materialized = list(clauses)
    lines = [*(f"c {comment}" for comment in comments), f"p cnf {variables} {len(materialized)}"]
    lines.extend(" ".join(map(str, clause)) + " 0" for clause in materialized)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


@dataclass(frozen=True)
class ClauseRecord:
    clause_id: int
    clause: Clause
    depth: int
    original: bool
    parent_a: int | None = None
    parent_b: int | None = None
    pivot: int | None = None


@dataclass(frozen=True)
class RecoveryResult:
    status: str
    reason: str
    records: tuple[ClauseRecord, ...]
    derived_count: int
    estimated_bytes: int
    elapsed_ms: float

    @property
    def clause_to_id(self) -> dict[Clause, int]:
        return {record.clause: record.clause_id for record in self.records}


def bounded_resolution(cnf: CNF, budget: dict[str, int | float]) -> RecoveryResult:
    start = time.perf_counter_ns()
    time_budget_ms = float(budget["time_budget_ms"])
    memory_budget = int(budget["memory_budget_bytes"])
    derived_budget = int(budget["derived_clause_budget"])
    max_depth = int(budget["max_resolution_depth"])
    max_size = int(budget["max_clause_size"])
    pivot_occurrence_limit = int(budget.get("pivot_occurrence_limit", 2))

    records: list[ClauseRecord] = []
    clause_to_id: dict[Clause, int] = {}
    literal_index: dict[int, list[int]] = {}
    estimated_bytes = 0

    for clause in cnf.clauses:
        if clause in clause_to_id:
            continue
        clause_id = len(records)
        records.append(ClauseRecord(clause_id, clause, 0, True))
        clause_to_id[clause] = clause_id
        estimated_bytes += 64 + 8 * len(clause)
        for literal in clause:
            literal_index.setdefault(literal, []).append(clause_id)

    agenda = list(range(len(records)))
    agenda_index = 0
    seen_pairs: set[tuple[int, int]] = set()
    derived_count = 0
    original_occurrences: dict[int, int] = {}
    for clause in cnf.clauses:
        for literal in clause:
            variable = abs(literal)
            original_occurrences[variable] = original_occurrences.get(variable, 0) + 1

    def finish(status: str, reason: str) -> RecoveryResult:
        elapsed = (time.perf_counter_ns() - start) / 1e6
        return RecoveryResult(
            status, reason, tuple(records), derived_count, estimated_bytes, elapsed
        )

    while agenda_index < len(agenda):
        if (time.perf_counter_ns() - start) / 1e6 > time_budget_ms:
            return finish("ABORT", "time_budget")
        left_id = agenda[agenda_index]
        agenda_index += 1
        left = records[left_id]
        for literal in left.clause:
            if original_occurrences.get(abs(literal), 0) > pivot_occurrence_limit:
                continue
            for right_id in tuple(literal_index.get(-literal, ())):
                if left_id == right_id:
                    continue
                pair = tuple(sorted((left_id, right_id)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                right = records[right_id]
                depth = max(left.depth, right.depth) + 1
                if depth > max_depth:
                    continue
                left_set, right_set = set(left.clause), set(right.clause)
                pivots = sorted(
                    {abs(value) for value in left_set if -value in right_set}
                )
                if len(pivots) != 1:
                    continue
                pivot_var = pivots[0]
                pivot = pivot_var if pivot_var in left_set else -pivot_var
                resolvent = canonical_clause(
                    (left_set - {pivot}) | (right_set - {-pivot})
                )
                if resolvent is None or len(resolvent) > max_size or resolvent in clause_to_id:
                    continue
                if derived_count >= derived_budget:
                    return finish("ABORT", "derived_clause_budget")
                added_bytes = 64 + 8 * len(resolvent)
                if estimated_bytes + added_bytes > memory_budget:
                    return finish("ABORT", "memory_budget")
                clause_id = len(records)
                records.append(
                    ClauseRecord(
                        clause_id,
                        resolvent,
                        depth,
                        False,
                        left_id,
                        right_id,
                        pivot,
                    )
                )
                clause_to_id[resolvent] = clause_id
                estimated_bytes += added_bytes
                derived_count += 1
                agenda.append(clause_id)
                for value in resolvent:
                    literal_index.setdefault(value, []).append(clause_id)
    return finish("COMPLETE", "fixed_point")


def combinations_as_clauses(group: tuple[int, ...], size: int, sign: int) -> Iterable[Clause]:
    for subset in itertools.combinations(group, size):
        clause = canonical_clause(sign * variable for variable in subset)
        assert clause is not None
        yield clause
