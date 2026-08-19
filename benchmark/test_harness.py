import tempfile
import unittest
from pathlib import Path

from benchmark.harness import (
    HarnessError,
    parse_dimacs,
    parse_gnu_time_rss,
    parse_statistics,
    parse_status,
)


class HarnessTests(unittest.TestCase):
    def test_status_requires_consistent_output_and_exit(self):
        self.assertEqual(parse_status("s SATISFIABLE\n", 10, False), "SATISFIABLE")
        self.assertEqual(parse_status("s UNSATISFIABLE\n", 20, False), "UNSATISFIABLE")
        self.assertEqual(parse_status("s SATISFIABLE\n", 20, False), "ERROR")
        self.assertEqual(parse_status("", 10, False), "SATISFIABLE")
        self.assertEqual(parse_status("", None, True), "TIMEOUT")

    def test_statistics(self):
        parsed = parse_statistics(
            "c conflicts: 1,234 2.0 per second\n"
            "c decisions: 42\n"
            "c propagations: 9001\n"
        )
        self.assertEqual(parsed["conflicts"], 1234)
        self.assertEqual(parsed["decisions"], 42)
        self.assertEqual(parsed["propagations"], 9001)

    def test_gnu_time_rss_with_sat_exit_diagnostic(self):
        self.assertEqual(
            parse_gnu_time_rss("Command exited with non-zero status 10\n1768\n"),
            1768,
        )

    def test_dimacs_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            valid = repo / "valid.cnf"
            valid.write_text("c test\np cnf 2 2\n1 0\n-1 2 0\n", encoding="ascii")
            info = parse_dimacs(valid, repo)
            self.assertEqual((info.variables, info.clauses), (2, 2))

            invalid = repo / "invalid.cnf"
            invalid.write_text("p cnf 2 2\n1 0\n", encoding="ascii")
            with self.assertRaises(HarnessError):
                parse_dimacs(invalid, repo)


if __name__ == "__main__":
    unittest.main()
