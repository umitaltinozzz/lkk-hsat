import tempfile
import unittest
from pathlib import Path

from phase2.cnf import parse_cnf
from phase2.flow import CapacityInstance, flow_box_test
from phase3.generator import (
    BenchmarkRequest,
    allocation_for_request,
    generate_request,
)


class Phase3Tests(unittest.TestCase):
    def test_overlapping_violation_is_local_not_total_capacity(self):
        spec = allocation_for_request(BenchmarkRequest("overlapping_hall_violation", 5))
        self.assertEqual(sum(spec.demands), sum(spec.capacities))
        self.assertEqual(
            flow_box_test(CapacityInstance(spec.demands, spec.capacities, spec.reachable)).result,
            "UNSAT",
        )

    def test_small_generated_instances_parse(self):
        requests = (
            BenchmarkRequest("pigeonhole_exact1", 5),
            BenchmarkRequest("exact_r_allocation", 5),
            BenchmarkRequest("capacity2_violation", 2),
            BenchmarkRequest("random_3sat", 30, 0, "planted_sat"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, request in enumerate(requests):
                path = Path(directory) / f"test{index}.cnf"
                row = generate_request(request, 100 + index, path)
                cnf = parse_cnf(path)
                self.assertEqual(len(cnf.clauses), row["clauses"])
                self.assertEqual(cnf.variables, row["variables"])


if __name__ == "__main__":
    unittest.main()
