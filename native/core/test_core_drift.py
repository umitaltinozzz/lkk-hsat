"""Enforce the claim that the shared core preserves the accepted semantics.

Until Phase 4C the structural core was copied byte-for-byte into every engine
binary and nothing checked that the copies stayed identical -- the guarantee
lived only in a comment. The core now has one definition, and this test makes
the guarantee falsifiable: the Phase 5 engine, which is the shared core plus the
accepted registry, must report exactly the structural verdict the sealed Phase
4A binary reports, on every accepted CNF and on a spread of generated ones.

Phase 4C's indexed registry is held to a weaker but explicit contract: it may
only ever *decide more*. It must never contradict Phase 4A, but it is allowed to
return a structural result where Phase 4A returned UNKNOWN, since that is the
whole point of the change.

Run inside the Phase 4C image:
  python3 -m unittest native.core.test_core_drift
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SEALED = Path("/opt/solvers/bin/lkk-native4a")
SHARED_CORE = Path("/opt/solvers/bin/lkk-core-probe")
INDEXED = Path("/opt/solvers/bin/lkk-native4c")

CORPORA = [Path("results/phase3/20260816Tphase3benchmark01Z/cnfs"),
           Path("results/phase2/20260816Tphase2validation02Z/cnfs")]

# Compared field by field rather than as a blob, so a failure names the field.
STRUCTURAL_FIELDS = ("gate_decision", "gate_reason", "benefit_gate_decision",
                     "recovery_status", "recovery_abort_reason",
                     "structural_result", "final_result")
REGISTRY_FIELDS = ("registry_status", "registry_reason", "family_checks",
                   "boxes_found", "capacity_groups", "flow_nodes", "flow_edges")


def run(binary: Path, cnf: Path, witness: Path | None = None) -> dict:
    command = [str(binary), "--cnf", str(cnf), "--sha256", "x",
               "--fallback", "cadical"]
    if witness is not None:
        command += ["--witness", str(witness)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=300)
    line = next((x for x in reversed(completed.stdout.splitlines())
                 if x.startswith("{")), None)
    if line is None:
        raise AssertionError(f"{binary.name} produced no result for {cnf.name}:\n"
                             f"{completed.stdout[-500:]}{completed.stderr[-500:]}")
    return json.loads(line)


def corpus() -> list[Path]:
    return sorted(p for root in CORPORA if root.exists() for p in root.glob("*.cnf"))


@unittest.skipUnless(SEALED.exists() and SHARED_CORE.exists(),
                     "engine binaries are not present")
class CoreDriftTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instances = corpus()

    def test_corpus_is_present(self):
        self.assertGreater(len(self.instances), 20)

    def test_shared_core_matches_the_sealed_engine(self):
        """The extracted core must be behaviourally identical to Phase 4A."""
        for cnf in self.instances:
            with self.subTest(instance=cnf.name):
                sealed = run(SEALED, cnf)
                shared = run(SHARED_CORE, cnf)
                for field in (*STRUCTURAL_FIELDS, *REGISTRY_FIELDS):
                    self.assertEqual(sealed.get(field), shared.get(field),
                                     f"{field} drifted on {cnf.name}")

    def test_shared_core_writes_the_same_witness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checked = 0
            for cnf in self.instances:
                a, b = root / "sealed.json", root / "shared.json"
                sealed = run(SEALED, cnf, a)
                run(SHARED_CORE, cnf, b)
                if not sealed.get("witness_written"):
                    continue
                checked += 1
                with self.subTest(instance=cnf.name):
                    self.assertEqual(a.read_text(), b.read_text(),
                                     f"witness differs on {cnf.name}")
            self.assertGreater(checked, 0, "no witness was produced by any instance")


@unittest.skipUnless(SEALED.exists() and INDEXED.exists(),
                     "engine binaries are not present")
class IndexedRegistryContractTest(unittest.TestCase):
    """Phase 4C may decide more, but never differently."""

    @classmethod
    def setUpClass(cls):
        cls.instances = corpus()

    def test_never_contradicts_the_sealed_engine(self):
        for cnf in self.instances:
            with self.subTest(instance=cnf.name):
                sealed = run(SEALED, cnf)
                indexed = run(INDEXED, cnf)
                self.assertEqual(sealed["final_result"], indexed["final_result"],
                                 f"final answer changed on {cnf.name}")
                if sealed["structural_result"] != "UNKNOWN":
                    self.assertEqual(sealed["structural_result"],
                                     indexed["structural_result"],
                                     f"structural verdict changed on {cnf.name}")

    def test_only_ever_decides_more(self):
        gained = 0
        for cnf in self.instances:
            sealed = run(SEALED, cnf)
            indexed = run(INDEXED, cnf)
            if sealed["structural_result"] == "UNKNOWN" and \
                    indexed["structural_result"] != "UNKNOWN":
                gained += 1
            if sealed["structural_result"] != "UNKNOWN":
                self.assertNotEqual(indexed["structural_result"], "UNKNOWN",
                                    f"{cnf.name} regressed to UNKNOWN")
        self.assertGreater(gained, 0, "the indexed registry decided nothing new")

    def test_pre_registry_stages_are_untouched(self):
        """Phase 4C changes only the registry; everything before it must match."""
        for cnf in self.instances:
            with self.subTest(instance=cnf.name):
                sealed = run(SEALED, cnf)
                indexed = run(INDEXED, cnf)
                for field in ("gate_decision", "gate_reason",
                              "benefit_gate_decision", "recovery_status",
                              "recovery_abort_reason"):
                    self.assertEqual(sealed.get(field), indexed.get(field),
                                     f"{field} changed on {cnf.name}")


if __name__ == "__main__":
    unittest.main()
