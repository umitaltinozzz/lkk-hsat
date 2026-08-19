from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from phase2.cnf import parse_cnf, write_cnf
from phase2.flow import flow_box_test
from phase4b import lemmas


def document(clause_database, boxes=(), resources=(), lemma_list=()):
    return {
        "clause_database": list(clause_database),
        "harvested_boxes": list(boxes),
        "harvested_resources": list(resources),
        "lemmas": list(lemma_list),
    }


def record(identifier, clause, original=True, parent_a=None, parent_b=None, pivot=None, depth=0):
    return {"id": identifier, "clause": list(clause), "original": original,
            "parent_a": parent_a, "parent_b": parent_b, "pivot": pivot, "depth": depth}


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cnf = self.dir / "f.cnf"
        write_cnf(self.cnf, 3, [(1, 2), (-2, 3)], ("test",))

    def test_valid_resolution_is_replayed(self):
        doc = document([record(0, (1, 2)), record(1, (-2, 3)),
                        record(2, (1, 3), False, 0, 1, 2, 1)])
        proven, errors = lemmas.replay_database(self.cnf, doc)
        self.assertEqual(errors, [])
        self.assertIn((1, 3), proven)

    def test_clause_not_in_cnf_is_rejected(self):
        doc = document([record(0, (1, 2)), record(1, (2, 3))])
        _, errors = lemmas.replay_database(self.cnf, doc)
        self.assertTrue(any("not in the CNF" in e for e in errors))

    def test_wrong_resolvent_is_rejected(self):
        doc = document([record(0, (1, 2)), record(1, (-2, 3)),
                        record(2, (1,), False, 0, 1, 2, 1)])
        _, errors = lemmas.replay_database(self.cnf, doc)
        self.assertTrue(any("does not match" in e for e in errors))

    def test_forward_parent_is_rejected(self):
        doc = document([record(0, (1, 2)), record(1, (1, 3), False, 0, 2, 2, 1), record(2, (-2, 3))])
        _, errors = lemmas.replay_database(self.cnf, doc)
        self.assertTrue(any("forward resolution parents" in e for e in errors))


class FamilyTest(unittest.TestCase):
    def test_exact_r_family_shape(self):
        clauses = set(lemmas.exact_r_family([1, 2, 3], 1))
        self.assertIn((-1, -2), clauses)
        self.assertIn((1, 2, 3), clauses)
        self.assertEqual(len(clauses), 4)

    def test_capacity_family_shape(self):
        self.assertEqual(set(lemmas.capacity_family([1, 2, 3], 2)), {(-1, -2, -3)})

    def test_incomplete_family_is_reported(self):
        box = {"id": 0, "variables": [1, 2, 3], "demand": 1, "evidence_clause_ids": []}
        proven = {(-1, -2): 0, (-1, -3): 1, (-2, -3): 2}
        supported, errors = lemmas.verify_families(document([], [box]), proven)
        self.assertEqual(supported, set())
        self.assertEqual(len(errors), 1)


class SubModelTest(unittest.TestCase):
    """A box may only enter the sub-model when every variable of it is covered.

    Dropping an uncovered variable strengthens the at-least-r side into a claim
    the CNF does not make, which produced a false refutation of a satisfiable
    instance before the coverage rule was enforced.
    """

    def test_box_with_uncovered_variable_is_dropped(self):
        doc = document(
            [],
            [{"id": 0, "variables": [1, 2], "demand": 1, "evidence_clause_ids": []},
             {"id": 1, "variables": [3, 4], "demand": 1, "evidence_clause_ids": []}],
            [{"id": 0, "variables": [1, 3], "capacity": 1, "evidence_clause_ids": []}],
        )
        boxes, resources = lemmas.rebuild_sub_model(doc)
        self.assertEqual(boxes, [])
        self.assertEqual(resources, [])

    def test_fully_covered_pigeonhole_is_refuted(self):
        boxes = [{"id": i, "variables": [3 * i + 1, 3 * i + 2], "demand": 1,
                  "evidence_clause_ids": []} for i in range(3)]
        resources = [{"id": 0, "variables": [1, 4, 7], "capacity": 1, "evidence_clause_ids": []},
                     {"id": 1, "variables": [2, 5, 8], "capacity": 1, "evidence_clause_ids": []}]
        for box, pair in zip(boxes, ([1, 2], [4, 5], [7, 8])):
            box["variables"] = pair
        doc = document([], boxes, resources)
        result = lemmas.verify_structural_unsat(doc)
        self.assertEqual(result["result"], "UNSAT")
        self.assertEqual(result["rebuilt_boxes"], 3)

    def test_two_boxes_sharing_one_resource_is_not_refuted_when_covered(self):
        boxes = [{"id": 0, "variables": [1, 2], "demand": 1, "evidence_clause_ids": []},
                 {"id": 1, "variables": [3, 4], "demand": 1, "evidence_clause_ids": []}]
        resources = [{"id": 0, "variables": [1, 3], "capacity": 1, "evidence_clause_ids": []},
                     {"id": 1, "variables": [2, 4], "capacity": 1, "evidence_clause_ids": []}]
        result = lemmas.verify_structural_unsat(document([], boxes, resources))
        self.assertEqual(result["result"], "FEASIBLE")


class ConditionalTest(unittest.TestCase):
    def setUp(self):
        # Two boxes needing one each; resource 0 has capacity 1 and resource 1
        # capacity 1. Feasible, but forcing both boxes onto resource 0 is not.
        self.boxes = [{"id": 0, "variables": [1, 2], "demand": 1, "evidence_clause_ids": []},
                      {"id": 1, "variables": [3, 4], "demand": 1, "evidence_clause_ids": []}]
        self.resources = [{"id": 0, "variables": [1, 3], "capacity": 1, "evidence_clause_ids": []},
                          {"id": 1, "variables": [2, 4], "capacity": 1, "evidence_clause_ids": []}]

    def test_conflicting_pair_is_confirmed(self):
        doc = document([], self.boxes, self.resources)
        ok, reason = lemmas.verify_conditional(doc, {"clause": [-1, -3], "support": [1, 3]})
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "conditioning_exceeds_a_proven_capacity_or_demand")

    def test_unit_conflict_found_through_the_flow_test(self):
        # Feasible as a whole, but making variable 2 true forces box 1 onto
        # resources that then have no capacity left.
        boxes = [{"id": 0, "variables": [1, 2], "demand": 1, "evidence_clause_ids": []},
                 {"id": 1, "variables": [3, 4], "demand": 1, "evidence_clause_ids": []}]
        resources = [{"id": 0, "variables": [1], "capacity": 1, "evidence_clause_ids": []},
                     {"id": 1, "variables": [2, 3], "capacity": 1, "evidence_clause_ids": []},
                     {"id": 2, "variables": [4], "capacity": 0, "evidence_clause_ids": []}]
        doc = document([], boxes, resources)
        self.assertEqual(lemmas.verify_structural_unsat(doc)["result"], "FEASIBLE")
        ok, reason = lemmas.verify_conditional(doc, {"clause": [-2], "support": [2]})
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "conditioned_sub_model_infeasible")
        ok, _ = lemmas.verify_conditional(doc, {"clause": [-1], "support": [1]})
        self.assertFalse(ok)

    def test_non_conflicting_pair_is_rejected(self):
        doc = document([], self.boxes, self.resources)
        ok, _ = lemmas.verify_conditional(doc, {"clause": [-1, -4], "support": [1, 4]})
        self.assertFalse(ok)

    def test_clause_must_be_the_negated_support(self):
        doc = document([], self.boxes, self.resources)
        ok, reason = lemmas.verify_conditional(doc, {"clause": [-1, -2], "support": [1, 3]})
        self.assertFalse(ok)
        self.assertEqual(reason, "clause_is_not_the_negated_support")


class InstanceTest(unittest.TestCase):
    def test_conditioning_reduces_demand_and_capacity(self):
        boxes = [{"id": 0, "variables": [1, 2], "demand": 1, "evidence_clause_ids": []}]
        resources = [{"id": 0, "variables": [1], "capacity": 1, "evidence_clause_ids": []},
                     {"id": 1, "variables": [2], "capacity": 1, "evidence_clause_ids": []}]
        status, instance = lemmas.sub_model_instance(boxes, resources, [1])
        self.assertEqual(status, "OK")
        self.assertEqual(instance.demands, ())
        self.assertEqual(instance.capacities, (0, 1))
        self.assertEqual(flow_box_test(instance).result, "FEASIBLE")


if __name__ == "__main__":
    unittest.main()
