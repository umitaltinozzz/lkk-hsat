"""The budget must be charged for the work that is actually done.

Phase 4C exists because two places got this wrong in opposite directions:

* the exact-r family was charged in full before a single clause was tested, so a
  candidate rejected on its first lookup still cost 176 checks and the budget
  ran out having learned nothing;
* a resource-phase candidate was charged one check while performing |group| edge
  lookups, so the registry could spend four seconds with its budget apparently
  untouched.

Neither is a parameter and neither shows up in a profiler: both are a mismatch
between what the counter claims and what the code does. These tests turn that
class of defect into something a test run catches, by counting the real lookups
and comparing them with the charge.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from phase2.cnf import bounded_resolution, parse_cnf
from phase2.structure import benefit_gate, fast_cheap_gate, recover_exact_registry
from phase3.generator import BenchmarkRequest, generate_request
from phase4c.registry_indexed import recover_exact_registry_indexed

CONFIG = json.loads(Path("phase3/config.json").read_text())
UNBOUNDED = {"family_check_budget": 10 ** 12, "maximum_resource_capacity": 3}

FAMILIES = [
    BenchmarkRequest("pigeonhole_exact1", 7, 0, "acct"),
    BenchmarkRequest("exact_r_allocation", 9, 0, "acct"),
    BenchmarkRequest("overlapping_hall_violation", 6, 0, "acct"),
    BenchmarkRequest("capacity2_violation", 4, 0, "acct"),
    BenchmarkRequest("hidden_pigeonhole", 6, 100, "acct"),
    BenchmarkRequest("satisfiable_capacity_control", 10, 0, "acct"),
]


class CountingDict(dict):
    """A clause index that records how many membership questions were asked."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lookups = 0

    def __contains__(self, key):
        self.lookups += 1
        return super().__contains__(key)

    def get(self, key, default=None):
        self.lookups += 1
        return super().get(key, default)


class CountingSet(set):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lookups = 0

    def __contains__(self, key):
        self.lookups += 1
        return super().__contains__(key)


class InstrumentedRecovery:
    """Wraps a recovery so its clause index counts lookups."""

    def __init__(self, recovery):
        self._recovery = recovery
        self.index = CountingDict(recovery.clause_to_id)

    def __getattr__(self, name):
        return getattr(self._recovery, name)

    @property
    def clause_to_id(self):
        return self.index


def recovery_for(path: Path):
    cnf = parse_cnf(path)
    gate = fast_cheap_gate(cnf)
    if gate.decision != "RUN_LKK":
        return None
    if benefit_gate(cnf, gate, CONFIG["benefit_gate"]).decision != "RUN_LKK":
        return None
    recovery = bounded_resolution(cnf, CONFIG["recovery"])
    return recovery if recovery.status == "COMPLETE" else None


class AccountingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.cases = []
        for index, request in enumerate(FAMILIES):
            path = cls.dir / f"{index:02d}_{request.family}.cnf"
            generate_request(request, 95000 + index, path)
            recovery = recovery_for(path)
            if recovery is not None:
                cls.cases.append((path.name, recovery))

    def test_corpus_is_present(self):
        self.assertGreaterEqual(len(self.cases), 4)

    def test_every_clause_lookup_is_charged(self):
        """The invariant that both Phase 4C defects violated.

        Work the budget cannot see is what let the registry spend four seconds
        with its counter apparently untouched. Every question asked of the clause
        index must cost one check, so the lookup count can never exceed the
        charge. The charge may legitimately be higher, because the candidate scan
        charges for pairs it examines without consulting the clause index.
        """
        for name, recovery in self.cases:
            with self.subTest(instance=name):
                probe = InstrumentedRecovery(recovery)
                result = recover_exact_registry_indexed(probe, UNBOUNDED)
                self.assertGreater(probe.index.lookups, 0)
                self.assertGreaterEqual(
                    result.family_checks, probe.index.lookups,
                    f"{name}: charged {result.family_checks} but performed "
                    f"{probe.index.lookups} clause lookups -- unbilled work")

    def test_charge_stays_close_to_the_work_on_structured_instances(self):
        """Overcharging retires the budget before it has learned anything.

        On instances whose families dominate the search the charge should track
        the lookups closely. A large ratio means the search is paying for work it
        did not do, which is what stopped exact-r 11 from ever being decided.
        """
        for name, recovery in self.cases:
            with self.subTest(instance=name):
                probe = InstrumentedRecovery(recovery)
                result = recover_exact_registry_indexed(probe, UNBOUNDED)
                ratio = result.family_checks / max(1, probe.index.lookups)
                self.assertLess(ratio, 50.0,
                                f"{name}: charged {ratio:.1f}x the lookups")

    def test_accepted_registry_overcharges_and_that_is_why_4c_exists(self):
        """Documents the defect Phase 4C fixed, so a revert cannot go unnoticed.

        On a family-rich instance the accepted search charges far more than it
        looks up, because it prices the whole family before testing one clause.
        If this ever stops being true, the accepted implementation changed and
        the Phase 4C rationale needs revisiting.
        """
        worst = 0.0
        for _, recovery in self.cases:
            probe = InstrumentedRecovery(recovery)
            result = recover_exact_registry(probe, UNBOUNDED)
            if probe.index.lookups:
                worst = max(worst, result.family_checks / probe.index.lookups)
        self.assertGreater(worst, 1.0,
                           "the accepted registry no longer overcharges; "
                           "re-examine the Phase 4C rationale")

    def test_budget_is_actually_respected(self):
        """A budget that is charged correctly must also stop the search."""
        for name, recovery in self.cases:
            with self.subTest(instance=name):
                tight = {"family_check_budget": 50, "maximum_resource_capacity": 3}
                result = recover_exact_registry_indexed(recovery, tight)
                self.assertLessEqual(
                    result.family_checks, 50 + 1,
                    f"{name}: overran a 50-check budget")


if __name__ == "__main__":
    unittest.main()
