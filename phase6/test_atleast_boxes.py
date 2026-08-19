"""Phase 6: one-sided demand boxes.

The registry used to require the full exact-r family for a demand box. The cut
argument never reads the at-most-r half, so requiring it rejected the family the
whole technique was built for: the standard pigeonhole encoding states that each
pigeon occupies some hole and says nothing about at most one.

These tests pin the three things that must hold for that relaxation to be worth
having: it is off unless asked for, it does not change any answer the sealed
configuration already gave, and the witnesses it produces satisfy the
independent checker rather than a checker taught to accept them.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase2 import witness as witness_tools  # noqa: E402

NATIVE = Path(os.environ.get("PHASE5_NATIVE", "/opt/solvers/bin/lkk-native5"))
HOLES = Path("benchmarks/phase5/satlib/pigeon-hole")


def sha256_of(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cnf: Path, atleast: bool, witness: Path | None = None) -> dict:
    command = [str(NATIVE), "--cnf", str(cnf), "--sha256", sha256_of(cnf),
               "--telemetry-only", "--fallback", "none"]
    if atleast:
        command.append("--atleast-boxes")
    if witness is not None:
        command += ["--witness", str(witness)]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    return json.loads(done.stdout)


@unittest.skipUnless(NATIVE.exists() and HOLES.exists(), "engine or corpus missing")
class AtLeastBoxTest(unittest.TestCase):
    holes = ["hole6", "hole7", "hole8", "hole9", "hole10"]

    def test_off_by_default_leaves_pigeonhole_undecided(self):
        """The flag must be opt-in, or Phase 5's numbers stop reproducing."""
        for name in self.holes:
            with self.subTest(name):
                out = run(HOLES / f"{name}.cnf", atleast=False)
                self.assertEqual(out["boxes_found"], 0)
                self.assertEqual(out["registry_reason"], "no_sound_exact_r_boxes")

    def test_pigeonhole_is_refuted_when_enabled(self):
        for index, name in enumerate(self.holes):
            with self.subTest(name):
                out = run(HOLES / f"{name}.cnf", atleast=True)
                self.assertEqual(out["structural_result"], "UNSAT")
                # hole_n is n+1 pigeons into n holes; a box per pigeon and a
                # group per hole is the only reading that can close.
                self.assertEqual(out["boxes_found"], index + 7)
                self.assertEqual(out["capacity_groups"], index + 6)

    def test_witness_satisfies_the_independent_checker(self):
        """The checker is the one from Phase 2, not one taught to agree."""
        work = Path(tempfile.mkdtemp())
        try:
            for name in self.holes:
                with self.subTest(name):
                    cnf = HOLES / f"{name}.cnf"
                    path = work / f"{name}.json"
                    run(cnf, atleast=True, witness=path)
                    report = witness_tools.check_witness(cnf, path)
                    self.assertTrue(report["valid"])
                    # The refutation is exactly the counting argument: one more
                    # unit of demand than the cut can carry.
                    self.assertEqual(report["total_demand"], report["cut_capacity"] + 1)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_checker_rejects_a_one_sided_box_that_does_not_say_so(self):
        """Dropping the marker must not smuggle weaker evidence past the checker."""
        work = Path(tempfile.mkdtemp())
        try:
            cnf = HOLES / "hole6.cnf"
            path = work / "hole6.json"
            run(cnf, atleast=True, witness=path)
            payload = json.loads(path.read_text())
            self.assertTrue(any(b.get("kind") == "atleast" for b in payload["boxes"]))
            for box in payload["boxes"]:
                box.pop("kind", None)
            path.write_text(json.dumps(payload))
            with self.assertRaises(witness_tools.WitnessError):
                witness_tools.check_witness(cnf, path)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_checker_rejects_an_inflated_demand(self):
        """The demand is what the cut is compared against; it must be evidenced."""
        work = Path(tempfile.mkdtemp())
        try:
            cnf = HOLES / "hole6.cnf"
            path = work / "hole6.json"
            run(cnf, atleast=True, witness=path)
            payload = json.loads(path.read_text())
            payload["boxes"][0]["demand"] = 2
            payload["flow"]["total_demand"] += 1
            path.write_text(json.dumps(payload))
            with self.assertRaises(witness_tools.WitnessError):
                witness_tools.check_witness(cnf, path)
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
