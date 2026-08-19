from __future__ import annotations
import unittest
from pathlib import Path
from phase4a.run_phase4a import python_structural
import json

class Phase4ATests(unittest.TestCase):
    def test_locked_semantics_still_detect_small_php(self):
        cfg=json.loads(Path("phase3/config.json").read_text())
        got=python_structural(Path("results/phase3/20260816Tphase3benchmark01Z/cnfs/001_pigeonhole_exact1_boundary_low_scale8_noise0.cnf"),cfg)
        self.assertEqual(got["structural_result"],"UNSAT")

if __name__ == "__main__": unittest.main()
