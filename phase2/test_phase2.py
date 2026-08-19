import json
import random
import tempfile
import unittest
from pathlib import Path

from phase2.cnf import bounded_resolution, parse_cnf
from phase2.flow import CapacityInstance, exhaustive_box_test, flow_box_test
from phase2.generator import benchmark_specs, generate_benchmark
from phase2.structure import build_structural_model, recover_exact_registry
from phase2.structure import benefit_gate, fast_cheap_gate
from phase2.witness import check_witness, create_witness


RECOVERY = {
    "time_budget_ms": 1000,
    "memory_budget_bytes": 16 * 1024 * 1024,
    "derived_clause_budget": 5000,
    "max_resolution_depth": 8,
    "max_clause_size": 12,
    "pivot_occurrence_limit": 2,
}
REGISTRY = {"family_check_budget": 200000, "maximum_resource_capacity": 3}


class Phase2Tests(unittest.TestCase):
    def test_flow_matches_exhaustive_random_small(self):
        for seed in range(100):
            rng = random.Random(seed)
            resources = rng.randint(1, 5)
            boxes = rng.randint(1, 5)
            reachable = []
            demands = []
            for _ in range(boxes):
                degree = rng.randint(1, resources)
                neighbors = tuple(sorted(rng.sample(range(resources), degree)))
                reachable.append(neighbors)
                demands.append(rng.randint(1, min(3, degree)))
            instance = CapacityInstance(
                tuple(demands),
                tuple(rng.randint(1, 3) for _ in range(resources)),
                tuple(reachable),
            )
            self.assertEqual(exhaustive_box_test(instance)[0], flow_box_test(instance).result)

    def test_visible_unsat_produces_checkable_witness(self):
        spec = benchmark_specs()[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cnf_path = root / "pigeon.cnf"
            generate_benchmark(spec, 0, 1, cnf_path)
            cnf = parse_cnf(cnf_path)
            recovery = bounded_resolution(cnf, RECOVERY)
            registry = recover_exact_registry(recovery, REGISTRY)
            self.assertEqual(registry.status, "COMPLETE", registry.reason)
            model = build_structural_model(registry)
            flow = flow_box_test(model.capacity_instance)
            self.assertEqual(flow.result, "UNSAT")
            witness_path = root / "witness.json"
            witness_path.write_text(
                json.dumps(create_witness(cnf, recovery, model, flow)), encoding="utf-8"
            )
            self.assertTrue(check_witness(cnf_path, witness_path)["valid"])

    def test_hidden_cardinality_is_recovered(self):
        spec = benchmark_specs()[-1]
        with tempfile.TemporaryDirectory() as directory:
            cnf_path = Path(directory) / "hidden.cnf"
            generate_benchmark(spec, 0, 2, cnf_path)
            cnf = parse_cnf(cnf_path)
            recovery = bounded_resolution(cnf, RECOVERY)
            self.assertEqual(recovery.status, "COMPLETE", recovery.reason)
            self.assertGreater(recovery.derived_count, 0)
            registry = recover_exact_registry(recovery, REGISTRY)
            self.assertEqual(registry.status, "COMPLETE", registry.reason)
            self.assertEqual(flow_box_test(build_structural_model(registry).capacity_instance).result, "UNSAT")

    def test_recovery_budget_abort_is_conservative(self):
        spec = benchmark_specs()[-2]
        with tempfile.TemporaryDirectory() as directory:
            cnf_path = Path(directory) / "hidden.cnf"
            generate_benchmark(spec, 0, 3, cnf_path)
            cnf = parse_cnf(cnf_path)
            budget = dict(RECOVERY)
            budget["derived_clause_budget"] = 0
            recovery = bounded_resolution(cnf, budget)
            self.assertEqual((recovery.status, recovery.reason), ("ABORT", "derived_clause_budget"))
            registry = recover_exact_registry(recovery, REGISTRY)
            self.assertEqual(registry.status, "UNKNOWN")

    def test_gate_and_benefit_reject_without_solving(self):
        with tempfile.TemporaryDirectory() as directory:
            cnf_path = Path(directory) / "mixed.cnf"
            cnf_path.write_text("p cnf 3 1\n1 -2 3 0\n", encoding="ascii")
            cnf = parse_cnf(cnf_path)
            gate = fast_cheap_gate(cnf)
            self.assertEqual(gate.decision, "SKIP_TO_CDCL")
            forced_gate = type(gate)(
                "RUN_LKK",
                "test",
                0.0,
                {
                    "monotone_positive_clauses": 1,
                    "monotone_negative_clauses": 1,
                    "mirror_pairs": 0,
                    "estimated_resolution_pairs": 100,
                },
            )
            benefit = benefit_gate(
                cnf,
                forced_gate,
                {
                    "minimum_signal_count": 2,
                    "maximum_clauses": 10,
                    "maximum_estimated_resolution_pairs": 1,
                },
            )
            self.assertEqual(benefit.decision, "SKIP_TO_CDCL")


if __name__ == "__main__":
    unittest.main()
