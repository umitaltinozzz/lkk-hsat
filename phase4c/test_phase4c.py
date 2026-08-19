"""Differential tests: the indexed registry must agree with the accepted one.

The contract Phase 4C claims is narrow and testable:

  * with an unbounded budget, the indexed search returns a result identical to
    `phase2.structure.recover_exact_registry` -- same status, same reason, same
    boxes, same resources, same evidence;
  * with the accepted budget, it never returns fewer boxes, and never returns a
    box the unbounded accepted search would not also have found.

Nothing here checks performance. If these tests fail, the change is wrong.
"""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from phase2.cnf import bounded_resolution, parse_cnf, write_cnf
from phase2.structure import (benefit_gate, fast_cheap_gate,
                              recover_exact_registry)
from phase3.generator import BenchmarkRequest, generate_request
from phase4c.registry_indexed import (build_pair_index,
                                      recover_exact_registry_indexed)

CONFIG = json.loads(Path("phase3/config.json").read_text())
UNBOUNDED = {"family_check_budget": 10 ** 12, "maximum_resource_capacity": 3}

FAMILIES = [
    *[BenchmarkRequest("pigeonhole_exact1", s, 0, "t") for s in (5, 6, 7, 8)],
    *[BenchmarkRequest("exact_r_allocation", s, 0, "t") for s in (5, 7, 9)],
    *[BenchmarkRequest("overlapping_hall_violation", s, 0, "t") for s in (4, 6, 8)],
    *[BenchmarkRequest("capacity2_violation", s, 0, "t") for s in (3, 4, 5)],
    *[BenchmarkRequest("hidden_pigeonhole", s, n, "t") for s in (5, 6, 7) for n in (0, 100)],
    *[BenchmarkRequest("satisfiable_capacity_control", s, 0, "t") for s in (6, 10, 14)],
    *[BenchmarkRequest("random_3sat", s, 0, "uniform") for s in (60, 120)],
]


def as_comparable(result):
    """Everything about a registry result that callers can observe."""
    return (
        result.status,
        result.reason,
        tuple((b.variables, b.demand, b.evidence_clause_ids) for b in result.boxes),
        tuple((r.variables, r.capacity, r.evidence_clause_ids) for r in result.resources),
    )


def recovery_for(path: Path):
    cnf = parse_cnf(path)
    gate = fast_cheap_gate(cnf)
    if gate.decision != "RUN_LKK":
        return None
    if benefit_gate(cnf, gate, CONFIG["benefit_gate"]).decision != "RUN_LKK":
        return None
    recovery = bounded_resolution(cnf, CONFIG["recovery"])
    return recovery if recovery.status == "COMPLETE" else None


class PairIndexTest(unittest.TestCase):
    def test_index_lists_every_containing_clause(self):
        negatives = [(-1, -2, -3), (-2, -3), (-4, -5)]
        index = build_pair_index(negatives)
        self.assertEqual(sorted(index[(2, 3)]), [0, 1])
        self.assertEqual(index[(4, 5)], [2])
        self.assertEqual(index.get((1, 4)), None)

    def test_every_pair_of_a_clause_is_indexed(self):
        index = build_pair_index([(-1, -2, -3)])
        self.assertEqual(sorted(index), [(1, 2), (1, 3), (2, 3)])


class EquivalenceTest(unittest.TestCase):
    """With no budget pressure the two searches must be indistinguishable."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.recoveries = []
        for index, request in enumerate(FAMILIES):
            path = cls.dir / f"{index:03d}_{request.family}_{request.scale}.cnf"
            generate_request(request, 91000 + index, path)
            recovery = recovery_for(path)
            if recovery is not None:
                cls.recoveries.append((path.name, recovery))

    def test_corpus_is_not_empty(self):
        self.assertGreater(len(self.recoveries), 8)

    def test_identical_under_unbounded_budget(self):
        for name, recovery in self.recoveries:
            with self.subTest(instance=name):
                expected = recover_exact_registry(recovery, UNBOUNDED)
                actual = recover_exact_registry_indexed(recovery, UNBOUNDED)
                self.assertEqual(as_comparable(expected), as_comparable(actual))

    def test_indexed_never_loses_boxes_at_the_accepted_budget(self):
        for name, recovery in self.recoveries:
            with self.subTest(instance=name):
                accepted = recover_exact_registry(recovery, CONFIG["registry"])
                indexed = recover_exact_registry_indexed(recovery, CONFIG["registry"])
                self.assertGreaterEqual(len(indexed.boxes), len(accepted.boxes))

    def test_indexed_boxes_are_sound_against_the_unbounded_search(self):
        """Any box the indexed search reports must be one the accepted search
        also finds when it is allowed to run to completion."""
        for name, recovery in self.recoveries:
            with self.subTest(instance=name):
                truth = recover_exact_registry(recovery, UNBOUNDED)
                indexed = recover_exact_registry_indexed(recovery, CONFIG["registry"])
                if indexed.status != "COMPLETE":
                    continue
                truth_boxes = {(b.variables, b.demand) for b in truth.boxes}
                for box in indexed.boxes:
                    self.assertIn((box.variables, box.demand), truth_boxes)

    def test_check_counts_are_not_comparable_but_decisions_are(self):
        """Check counts mean different things in the two implementations.

        The accepted search charges one check per candidate in the resource
        phase, even though testing that candidate costs |group| edge lookups.
        The indexed search charges each lookup, because undercharging is exactly
        what let the registry burn four seconds on a 24-box instance while its
        budget still looked untouched. The two counters therefore measure
        different units and must not be compared; what must agree is the
        decision, which the equivalence tests above pin down.
        """
        for name, recovery in self.recoveries:
            with self.subTest(instance=name):
                expected = recover_exact_registry(recovery, UNBOUNDED)
                actual = recover_exact_registry_indexed(recovery, UNBOUNDED)
                self.assertEqual(as_comparable(expected), as_comparable(actual))
                self.assertGreater(actual.family_checks, 0)


class RandomDifferentialTest(unittest.TestCase):
    """Random CNFs exercise the rejection paths the generated families miss."""

    def test_random_instances_agree_unbounded(self):
        directory = Path(tempfile.mkdtemp())
        checked = 0
        for seed in range(60):
            rng = random.Random(50_000 + seed)
            variables = rng.randint(6, 16)
            clauses = set()
            for _ in range(rng.randint(8, 40)):
                size = rng.randint(2, 4)
                if size > variables:
                    continue
                chosen = rng.sample(range(1, variables + 1), size)
                sign = rng.choice((1, -1, 0))
                if sign == 0:
                    literals = tuple(v if rng.getrandbits(1) else -v for v in chosen)
                else:
                    literals = tuple(sign * v for v in chosen)
                if any(-x in literals for x in literals):
                    continue
                clauses.add(tuple(sorted(literals, key=lambda x: (abs(x), x < 0))))
            if not clauses:
                continue
            path = directory / f"r{seed}.cnf"
            write_cnf(path, variables, sorted(clauses), ("random differential",))
            recovery = recovery_for(path)
            if recovery is None:
                continue
            checked += 1
            expected = recover_exact_registry(recovery, UNBOUNDED)
            actual = recover_exact_registry_indexed(recovery, UNBOUNDED)
            self.assertEqual(as_comparable(expected), as_comparable(actual),
                             f"mismatch on random seed {seed}")
        self.assertGreater(checked, 10)


if __name__ == "__main__":
    unittest.main()
