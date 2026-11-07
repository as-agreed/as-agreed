import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(example, *options):
    return subprocess.run(
        [sys.executable, str(ROOT / "ledger.py"), "run", str(ROOT / "examples" / example), *options],
        capture_output=True, text=True)


class Examples(unittest.TestCase):
    def test_hello_flags_the_two_hardened_hedges(self):
        r = run("hello", "--only", "hedged,not_found,sourced,dated")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.count("DANGER"), 2)

    def test_planted_failure_is_caught_before_the_payment(self):
        r = run("planted-failure", "--against-all", "--only", "hedged,not_found,sourced,dated,must",
                "--fail-on", "danger,dropped_must")
        self.assertEqual(r.returncode, 1)
        self.assertIn("gate: pass", r.stdout)  # the research step carried the must-travel claim
        self.assertIn("DROPPED!", r.stdout)    # the payment note dropped it
        self.assertIn("DANGER", r.stdout)      # and hardened a hedge into a fact

    def test_a_widened_limit_is_not_a_claim_the_ledger_can_see(self):
        # the widened amount is a typed field, so only the commitment check holds it: `reconcile`, tested beside the demo
        r = run("planted-widened", "--against-all", "--only", "hedged,not_found,sourced,dated,must",
                "--fail-on", "danger,dropped_must")
        self.assertEqual(r.returncode, 0)
        self.assertIn("gate: pass", r.stdout)


if __name__ == "__main__":
    unittest.main()
