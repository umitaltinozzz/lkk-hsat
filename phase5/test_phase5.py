from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from phase2.cnf import write_cnf
from phase5 import classify, plots, selector
from phase5.run_phase5 import _stratified_sample, par2, split_of


class SplitTest(unittest.TestCase):
    def test_split_is_deterministic_and_content_addressed(self):
        digest = "a" * 64
        self.assertEqual(split_of(digest, 0.2), split_of(digest, 0.2))

    def test_split_respects_the_requested_fraction(self):
        digests = [f"{i:064x}" for i in range(4000)]
        calibration = sum(1 for d in digests if split_of(d, 0.2) == "calibration")
        self.assertAlmostEqual(calibration / len(digests), 0.2, delta=0.03)

    def test_nothing_is_calibration_at_zero_fraction(self):
        self.assertTrue(all(split_of(f"{i:064x}", 0.0) == "evaluation" for i in range(200)))


class Par2Test(unittest.TestCase):
    def test_solved_uses_runtime(self):
        self.assertEqual(par2(120.0, "SATISFIABLE", 5000), 120.0)

    def test_timeout_is_penalised(self):
        self.assertEqual(par2(5000.0, "TIMEOUT", 5000), 10000.0)

    def test_unknown_is_penalised_like_a_timeout(self):
        self.assertEqual(par2(10.0, "UNKNOWN", 5000), 10000.0)


class ClassifyTest(unittest.TestCase):
    def test_structural_refutation_wins(self):
        stratum, _ = classify.classify({"structural_result": "UNSAT", "gate_decision": "RUN_LKK"})
        self.assertEqual(stratum, "lkk_direct_structural_unsat")

    def test_closed_gate_means_no_structure(self):
        stratum, _ = classify.classify(
            {"gate_decision": "SKIP_TO_CDCL", "gate_reason": "no_cheap_structural_signal"})
        self.assertEqual(stratum, "no_useful_detected_structure")

    def test_benefit_gate_decline_means_no_structure(self):
        stratum, _ = classify.classify({"gate_decision": "RUN_LKK",
                                        "benefit_gate_decision": "SKIP_TO_CDCL",
                                        "benefit_gate_reason": "input_clause_limit"})
        self.assertEqual(stratum, "no_useful_detected_structure")

    def test_feasible_closed_registry_is_its_own_class(self):
        stratum, _ = classify.classify({"gate_decision": "RUN_LKK",
                                        "benefit_gate_decision": "RUN_LKK",
                                        "registry_status": "COMPLETE",
                                        "structural_result": "FEASIBLE"})
        self.assertEqual(stratum, "structural_not_closed_by_flow")

    def test_every_stratum_is_a_declared_one(self):
        samples = [
            {"gate_decision": "RUN_LKK", "benefit_gate_decision": "RUN_LKK",
             "boxes_found": 5, "capacity_groups": 3, "registry_status": "COMPLETE"},
            {"gate_decision": "RUN_LKK", "benefit_gate_decision": "RUN_LKK",
             "boxes_found": 5, "registry_status": "UNKNOWN",
             "registry_reason": "family_check_budget"},
            {"gate_decision": "RUN_LKK", "benefit_gate_decision": "RUN_LKK",
             "recovery_status": "COMPLETE", "registry_status": "UNKNOWN"},
        ]
        for sample in samples:
            stratum, _ = classify.classify(sample)
            self.assertIn(stratum, classify.STRATA)


class SelectorTest(unittest.TestCase):
    PARAMS = {"min_boxes": 4, "min_clauses": 100000, "min_signal": 0}

    def test_open_structure_picks_kissat(self):
        engine, _ = selector.decide(
            {"boxes_found": 9, "registry_status": "UNKNOWN", "clauses": 500}, self.PARAMS)
        self.assertEqual(engine, "kissat")

    def test_closed_registry_stays_on_cadical(self):
        engine, _ = selector.decide(
            {"boxes_found": 9, "registry_status": "COMPLETE", "clauses": 500}, self.PARAMS)
        self.assertEqual(engine, "cadical")

    def test_no_structure_stays_on_cadical(self):
        engine, _ = selector.decide(
            {"boxes_found": 0, "registry_status": "UNKNOWN", "clauses": 500}, self.PARAMS)
        self.assertEqual(engine, "cadical")

    def test_large_formula_picks_kissat(self):
        engine, reason = selector.decide(
            {"boxes_found": 0, "registry_status": "UNKNOWN", "clauses": 250000}, self.PARAMS)
        self.assertEqual(engine, "kissat")
        self.assertIn("large_formula", reason)

    def test_fit_rejects_leaked_features(self):
        cases = [{"telemetry": {"boxes_found": 1, "instance": "leak.cnf"},
                  "cadical": {"status": "SATISFIABLE", "runtime_ms": 1},
                  "kissat": {"status": "SATISFIABLE", "runtime_ms": 2}}]
        with self.assertRaises(ValueError):
            selector.fit(cases, {"min_boxes": [1], "min_clauses": [1], "min_signal": [0]}, 1000)

    def test_fit_prefers_the_lower_regret_rule(self):
        # Kissat is better exactly when boxes are present and the registry is open.
        cases = []
        for boxes in (0, 8):
            cases.append({
                "telemetry": {"boxes_found": boxes, "registry_status": "UNKNOWN",
                              "clauses": 100},
                "cadical": {"status": "SATISFIABLE", "runtime_ms": 10 if boxes == 0 else 900},
                "kissat": {"status": "SATISFIABLE", "runtime_ms": 500 if boxes == 0 else 20},
            })
        fitted = selector.fit(cases, {"min_boxes": [1, 4, 100],
                                      "min_clauses": [1000000], "min_signal": [0]}, 5000)
        self.assertEqual(fitted["best"]["total_regret_ms"], 0)
        self.assertLessEqual(fitted["best"]["parameters"]["min_boxes"], 8)

    def test_evaluate_reports_regret_and_harm(self):
        cases = [{
            "telemetry": {"boxes_found": 0, "registry_status": "UNKNOWN", "clauses": 10},
            "cadical": {"status": "SATISFIABLE", "runtime_ms": 900},
            "kissat": {"status": "SATISFIABLE", "runtime_ms": 100},
        }]
        score = selector.evaluate(cases, self.PARAMS, 5000)
        self.assertEqual(score["harmful_selections"], 1)
        self.assertEqual(score["total_regret_ms"], 800)


class SampleTest(unittest.TestCase):
    def test_sampling_spreads_across_strata(self):
        rows = ([{"stratum": "a", "cnf_sha256": f"{i:064x}"} for i in range(50)]
                + [{"stratum": "b", "cnf_sha256": f"{i+100:064x}"} for i in range(5)])
        picked = _stratified_sample(rows, 10)
        self.assertEqual(len(picked), 10)
        self.assertGreaterEqual(sum(1 for p in picked if p["stratum"] == "b"), 4)

    def test_small_pool_is_returned_whole(self):
        rows = [{"stratum": "a", "cnf_sha256": "0" * 64}]
        self.assertEqual(len(_stratified_sample(rows, 10)), 1)


class PlotTest(unittest.TestCase):
    def test_plots_are_written_and_are_svg(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            written = plots.write_all(
                target,
                {"A": [1.0, 5.0, 20.0], "B": [2.0, 3.0]},
                {"x": {"pairs": [(1.0, 2.0, "i1"), (5.0, 3.0, "i2")],
                       "x_name": "A", "y_name": "B"}},
                [1.2, 0.8, 3.0], [0.5, 1.5], 5000.0)
            self.assertTrue(written)
            for name in written:
                body = (target / name).read_text()
                self.assertTrue(body.startswith("<svg"))
                self.assertTrue(body.rstrip().endswith("</svg>"))

    def test_empty_input_still_produces_valid_svg(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "c.svg"
            plots.cactus(target, {}, 1000.0)
            self.assertTrue(target.read_text().rstrip().endswith("</svg>"))


class NativeEngineTest(unittest.TestCase):
    """Exercises the real Phase 5 binary when it is present."""

    BINARY = Path("/opt/solvers/bin/lkk-native5")

    def setUp(self):
        if not self.BINARY.exists():
            self.skipTest("phase 5 native binary not present")
        self.dir = Path(tempfile.mkdtemp())
        self.sat = self.dir / "sat.cnf"
        write_cnf(self.sat, 3, [(1, 2), (-1, 3)], ("test",))

    def _run(self, *extra: str) -> str:
        completed = subprocess.run(
            [str(self.BINARY), "--cnf", str(self.sat), "--sha256", "x", *extra],
            capture_output=True, text=True, check=False)
        return completed.stdout

    def test_telemetry_only_does_not_solve(self):
        out = self._run("--telemetry-only", "--fallback", "none")
        self.assertIn('"telemetry_only":true', out)
        self.assertIn('"final_result":"UNKNOWN"', out)

    def test_cadical_fallback_solves(self):
        self.assertIn('"final_result":"SATISFIABLE"', self._run("--fallback", "cadical"))

    def test_selector_reports_its_choice(self):
        out = self._run("--fallback", "selector")
        self.assertIn('"selector_choice":', out)
        self.assertIn('"fallback_mode":"selector"', out)

    def test_stop_after_none_disables_the_front_end(self):
        out = self._run("--fallback", "cadical", "--stop-after", "none")
        self.assertIn('"stop_after":"none"', out)
        self.assertIn('"gate_decision":""', out)

    def test_unknown_stage_is_rejected(self):
        completed = subprocess.run(
            [str(self.BINARY), "--cnf", str(self.sat), "--sha256", "x",
             "--stop-after", "bogus"], capture_output=True, text=True, check=False)
        self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
