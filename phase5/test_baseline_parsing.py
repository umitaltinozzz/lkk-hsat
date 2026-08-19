"""The locked baselines must be able to read every corpus file.

The first attempt at the Phase 5 campaign was abandoned three hours in because
they could not. SATLIB appends a `%` line and a lone `0` after the last clause;
CaDiCaL stops at the `%` with "expected digit or '-'", and where only the zero
is present both solvers count it as an extra clause and abort with "too many
clauses". Every baseline measurement on 420 of the 1192 instances came back
UNKNOWN in about 17 ms, and the calibration stage then chose a timeout from
those non-answers.

The trailer is not part of the formula, so stripping it for the baselines cannot
change an answer. These tests hold that line: the corpus is checked for files
that need the treatment, and the treatment is checked for changing nothing else.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5.run_phase5 import (  # noqa: E402
    DEFINITE, direct_run, sanitized_for_baseline, satlib_trailer_start)

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "benchmarks" / "phase5"
CONFIG = REPO / "phase5" / "config.json"


class TrailerDetectionTest(unittest.TestCase):
    def test_trailer_is_found_and_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.cnf"
            src.write_text("p cnf 2 2\n1 2 0\n-1 -2 0\n%\n0\n")
            self.assertIsNotNone(satlib_trailer_start(src))
            out = sanitized_for_baseline(src, Path(tmp) / "clean.cnf")
            self.assertNotEqual(out, src)
            self.assertEqual(out.read_text(), "p cnf 2 2\n1 2 0\n-1 -2 0\n")

    def test_clean_file_is_passed_through_untouched(self):
        """No trailer must mean no copy, so ordinary instances are unaffected."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.cnf"
            src.write_text("p cnf 2 2\n1 2 0\n-1 -2 0\n")
            self.assertIsNone(satlib_trailer_start(src))
            self.assertEqual(sanitized_for_baseline(src, Path(tmp) / "clean.cnf"), src)

    def test_a_percent_inside_a_comment_is_not_a_trailer(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.cnf"
            src.write_text("c 50% density\np cnf 2 1\n1 2 0\n")
            self.assertIsNone(satlib_trailer_start(src))


@unittest.skipUnless(CORPUS.exists() and Path("/opt/solvers/bin/cadical").exists(),
                     "corpus or solvers missing")
class BaselineReadsCorpusTest(unittest.TestCase):
    # One per family that carries a trailer, plus one that does not.
    samples = ["satlib/uf250/uf250-029.cnf", "satlib/uuf200/uuf200-051.cnf",
               "satlib/pret/pret150_25.cnf", "satlib/pigeon-hole/hole6.cnf"]

    def test_both_baselines_answer(self):
        config = json.loads(CONFIG.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            for relative in self.samples:
                cnf = CORPUS / relative
                if not cnf.exists():
                    continue
                for key in ("cadical", "kissat"):
                    with self.subTest(instance=cnf.name, solver=key):
                        solver = config[key]
                        run = direct_run(
                            solver["executable"], solver["args"], cnf, 1, 120.0,
                            Path(tmp) / f"{cnf.stem}_{key}.log", Path(tmp))
                        self.assertIn(run["status"], DEFINITE,
                                      f"{key} could not decide {cnf.name}")


if __name__ == "__main__":
    unittest.main()
