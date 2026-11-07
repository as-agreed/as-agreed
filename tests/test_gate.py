"""The five behaviours of the command-line gate fixed before the first public commit, and the flags kept for the
project's own stage folders. Each test names the way the gate used to pass something it had not checked."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from as_agreed.__main__ import main as as_agreed  # noqa: E402
from as_agreed.hold import Policy  # noqa: E402


def gate(stages, *options):
    """Write the stage files to a fresh folder and run the gate over them."""
    with tempfile.TemporaryDirectory() as folder:
        for name, text in stages.items():
            (Path(folder) / name).write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "ledger.py"), "run", folder, *options],
                              capture_output=True, text=True)


HEDGED = "The vendor probably changed its bank details in October.\n"
FLAT = "The invoice total was checked against the purchase order.\n"
RECOMMENDED = ("--only", "hedged,not_found,sourced,dated,must", "--fail-on", "danger,dropped_must", "--against-all")


class FlagTypos(unittest.TestCase):
    """(a) `--fail-on danger,absent-must` printed ABSENT! and then "gate: pass"."""

    def test_a_misspelled_fail_on_value_stops_the_run(self):
        r = gate({"01-a.md": HEDGED, "02-b.md": HEDGED}, "--fail-on", "danger,absent-must")
        self.assertEqual(r.returncode, 2)
        self.assertIn("absent-must", r.stderr)
        self.assertNotIn("gate:", r.stdout)

    def test_a_misspelled_only_value_stops_the_run(self):
        r = gate({"01-a.md": HEDGED, "02-b.md": HEDGED}, "--only", "hedgd")
        self.assertEqual(r.returncode, 2)
        self.assertIn("hedgd", r.stderr)

    def test_an_empty_value_stops_the_run(self):
        self.assertEqual(gate({"01-a.md": HEDGED, "02-b.md": HEDGED}, "--fail-on", " , ").returncode, 2)


class NothingToCheck(unittest.TestCase):
    """(b) `run` turned "nothing selected" into a pass, so a folder with nothing to check passed."""

    def test_a_folder_with_nothing_to_check_is_not_a_pass(self):
        r = gate({"01-a.md": FLAT, "02-b.md": FLAT}, "--only", "hedged")
        self.assertEqual(r.returncode, 2)
        self.assertIn("gate: NOTHING TO CHECK", r.stdout)
        self.assertNotIn("gate: pass", r.stdout)

    def test_one_empty_hop_beside_a_checked_one_is_a_note(self):
        r = gate({"01-a.md": FLAT, "02-b.md": HEDGED, "03-c.md": HEDGED}, "--only", "hedged", "--against-all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing to check at stage 'b'", r.stdout)
        self.assertIn("gate: pass", r.stdout)

    def test_a_stage_with_nothing_upstream_still_counts_for_the_next(self):
        # the hedge first written at the empty hop must be checked at the next one
        r = gate({"01-a.md": FLAT, "02-b.md": HEDGED, "03-c.md": "The vendor definitely changed its bank details in October.\n"},
                 "--only", "hedged", "--fail-on", "danger", "--against-all")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("DANGER", r.stdout)


class Marks(unittest.TestCase):
    """(c) A `[!]` after the full stop, or on a line under --min-words, was dropped without a word."""

    def test_a_mark_after_the_full_stop_warns_and_names_the_line(self):
        r = gate({"01-a.md": FLAT + "Finance must confirm the bank details by phone. [!]\n", "02-b.md": FLAT}, *RECOMMENDED[:4])
        self.assertIn("line 2", r.stderr)
        self.assertIn("must-travel mark not registered", r.stderr)
        self.assertIn("marks: 1 must-travel mark(s) not registered", r.stdout)

    def test_a_mark_on_a_short_sentence_warns(self):
        r = gate({"01-a.md": FLAT + "Pay now, please [!].\n", "02-b.md": FLAT}, *RECOMMENDED[:4])
        self.assertIn("min-words", r.stderr)

    def test_a_mark_after_a_full_stop_does_not_mark_the_next_sentence(self):
        r = gate({"01-a.md": "Pay the vendor on Friday. [!] The invoice total is 18,400 dollars per the PDF.\n",
                  "02-b.md": "Nothing else was written here today.\n"}, "--only", "must")
        self.assertNotIn("DROPPED!", r.stdout)

    def test_a_mark_in_its_place_is_silent(self):
        r = gate({"01-a.md": "Finance must confirm the bank details by phone [!].\n",
                  "02-b.md": "Finance must confirm the bank details by phone.\n"}, *RECOMMENDED[:4])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("not registered", r.stderr + r.stdout)

    def test_a_mark_quoted_in_a_code_fence_is_silent(self):
        r = gate({"01-a.md": HEDGED + "```\nsome line. [!]\n```\n", "02-b.md": HEDGED}, *RECOMMENDED[:4])
        self.assertNotIn("not registered", r.stderr + r.stdout)


class CountedOnce(unittest.TestCase):
    """(d) With --against-all a sentence carried word for word was counted once per stage that held it."""

    def test_a_sentence_carried_verbatim_is_one_claim(self):
        r = gate({"01-a.md": HEDGED, "02-b.md": HEDGED, "03-c.md": HEDGED}, *RECOMMENDED)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("against 1 claims from ['a', 'b']", r.stdout)

    def test_a_must_mark_on_either_copy_survives_the_merge(self):
        r = gate({"01-a.md": "Finance must confirm the bank details by phone.\n",
                  "02-b.md": "Finance must confirm the bank details by phone [!].\n",
                  "03-c.md": "Nothing else was written here today.\n"},
                 "--only", "all", "--fail-on", "dropped_must", "--against-all")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("DROPPED!", r.stdout)


class MustTravelMatch(unittest.TestCase):
    """(e) Three shared words on a different line passed for a must-travel claim."""

    CLAIM = "Finance must confirm the vendor's new bank details by phone before any payment"
    OTHER = "The finance team will keep the bank statement and the payment file for the auditors.\n"

    def test_a_different_line_does_not_pass_for_a_must_travel_claim(self):
        r = gate({"01-a.md": self.CLAIM + " [!].\n", "02-b.md": self.OTHER}, "--only", "must", "--fail-on", "dropped_must")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("DROPPED!", r.stdout)

    def test_the_general_threshold_is_unchanged(self):
        r = gate({"01-a.md": self.CLAIM + ".\n", "02-b.md": self.OTHER}, "--only", "all", "--fail-on", "dropped")
        self.assertEqual(r.returncode, 0, r.stdout)  # still the documented third of the content words

    def test_a_close_copy_of_a_must_travel_claim_still_passes(self):
        r = gate({"01-a.md": self.CLAIM + " [!].\n", "02-b.md": "Before any payment, finance must confirm the new bank details by phone with the vendor.\n"},
                 "--only", "must", "--fail-on", "dropped_must")
        self.assertEqual(r.returncode, 0, r.stdout)


class OldWords(unittest.TestCase):
    """The project's own stage folders keep their command: the old words work on the command line, with a note."""

    def test_the_gate_command_of_the_project_still_runs(self):
        r = subprocess.run([sys.executable, str(ROOT / "ledger.py"), "run", str(ROOT / "examples" / "planted-failure"),
                            "--only", "hedged,not_found,sourced,dated,must", "--fail-on", "danger,absent_must", "--against-all"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("DROPPED!", r.stdout)
        self.assertIn("DANGER", r.stdout)
        self.assertIn("gate: FAIL (2 claims; fail-on = danger,dropped_must)", r.stdout)
        self.assertIn("old name of dropped_must", r.stderr)

    def test_absent_means_dropped(self):
        r = gate({"01-a.md": FLAT, "02-b.md": "Nothing else was written here today.\n"}, "--fail-on", "absent")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_the_sdk_takes_the_new_words_only(self):
        with self.assertRaises(ValueError):
            Policy(fail_on=("danger", "absent_must"))


class Counting(unittest.TestCase):
    def test_a_danger_is_counted_once_under_changed_and_danger(self):
        r = gate({"01-a.md": "The enterprise SLA is not confirmed by the vendor's account team.\n",
                  "02-b.md": "The enterprise SLA is confirmed by the vendor's account team.\n"}, "--fail-on", "changed,danger")
        self.assertIn("gate: FAIL (1 claims;", r.stdout)


class MarkTravelsByClaimId(unittest.TestCase):
    """(f) The mark lives in the file where `[!]` was written. A later stage that carried the sentence without it was
    registered as not must-travel, so under `--only must` (no `--against-all`) the hop after it selected nothing and the
    claim could vanish with the run still passing."""

    ORDER = "Finance must confirm the bank details by phone [!].\n"
    BARE = "Finance must confirm the bank details by phone.\n"
    GONE = "Nothing else was written here today.\n"
    OPTIONS = ("--only", "must", "--fail-on", "dropped_must")

    def test_a_mark_dropped_at_the_next_hop_still_binds_the_hop_after(self):
        r = gate({"01-order.md": self.ORDER, "02-brief.md": self.BARE, "03-send.md": self.GONE}, *self.OPTIONS)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("DROPPED!", r.stdout)
        self.assertIn("gate: FAIL", r.stdout)

    def test_a_marked_claim_that_arrives_at_every_stage_passes(self):
        r = gate({"01-order.md": self.ORDER, "02-brief.md": self.BARE, "03-send.md": self.BARE, "04-done.md": self.BARE},
                 *self.OPTIONS)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("DROPPED", r.stdout)
        self.assertNotIn("nothing to check", r.stdout)

    def test_only_must_still_checks_one_hop_at_a_time(self):
        r = gate({"01-order.md": self.ORDER, "02-brief.md": self.BARE, "03-send.md": self.GONE}, *self.OPTIONS)
        self.assertIn("against 1 claims from ['order']", r.stdout)
        self.assertIn("against 1 claims from ['brief']", r.stdout)
        self.assertNotIn("['order', 'brief']", r.stdout)

    def test_a_sentence_that_was_never_marked_stays_unmarked(self):
        r = gate({"01-order.md": self.BARE, "02-brief.md": self.BARE, "03-send.md": self.GONE}, "--only", "must")
        self.assertEqual(r.returncode, 2, r.stdout)  # no claim is must-travel anywhere: nothing to check
        self.assertIn("gate: NOTHING TO CHECK", r.stdout)

    def test_the_mark_is_carried_in_the_ledger_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, text in {"01-order.md": self.ORDER, "02-brief.md": self.BARE, "03-send.md": self.GONE}.items():
                (Path(folder) / name).write_text(text, encoding="utf-8")
            subprocess.run([sys.executable, str(ROOT / "ledger.py"), "run", folder, *self.OPTIONS], capture_output=True, text=True)
            rows = [json.loads(ln) for ln in (Path(folder) / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
        brief = [r for r in rows if r["kind"] == "claim" and r["stage"] == "brief"]
        self.assertEqual([r["must"] for r in brief], [True])


class NoCommitmentDeclared(unittest.TestCase):
    """(g) `reconcile` on a folder with no `commitments.json` registered no commitment, compared nothing against the
    as-agreed record on the commitment side, and still printed `gate: pass` and returned 0."""

    ORDER = "Pay the vendor invoice for October by Friday afternoon.\n"
    REPLY = {"obligor": "finance-ops", "obligee": "vendor", "act": "Pay the vendor invoice for October", "modality": "MUST",
             "note": "The vendor invoice for October was paid on Friday afternoon."}
    COMMITMENTS = [{"commitment_id": "pay-invoice", "act": "Pay the vendor invoice for October", "obligor": "finance-ops",
                    "obligee": "vendor", "modality": "MUST",
                    "carried_by": {"obligor": "obligor", "obligee": "obligee", "act": "act", "modality": "modality"}}]

    def reconcile(self, commitments):
        """Build a two-file folder (with a commitments.json when given) and run the folder reconcile; (code, stdout, stderr)."""
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "01-order.md").write_text(self.ORDER, encoding="utf-8")
            (Path(folder) / "02-research.md").write_text(self.REPLY["note"] + "\n", encoding="utf-8")
            (Path(folder) / "02-research.json").write_text(json.dumps(self.REPLY), encoding="utf-8")
            if commitments is not None:
                (Path(folder) / "commitments.json").write_text(json.dumps(commitments), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = as_agreed(["reconcile", folder])
        return code, out.getvalue(), err.getvalue()

    def test_a_folder_without_commitments_json_is_not_a_pass(self):
        code, out, err = self.reconcile(None)
        self.assertEqual(code, 2, out + err)
        self.assertIn("commitments.json", err)
        self.assertIn("no commitment was declared", err)
        self.assertIn("python3 ledger.py run <folder>", err)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("report: ledger.jsonl, counts.csv, flow.svg written to", out)  # the report is still written and printed

    def test_an_empty_commitments_json_is_not_a_pass(self):
        code, out, err = self.reconcile([])
        self.assertEqual(code, 2, out + err)
        self.assertIn("commitments.json", err)

    def test_a_folder_that_declares_a_commitment_returns_the_gates_own_code(self):
        code, out, err = self.reconcile(self.COMMITMENTS)
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("no commitment was declared", err)
        self.assertNotIn("commitments.json", err)
        self.assertIn("gate: pass", out)

    def test_a_run_that_held_something_keeps_its_own_code_without_commitments(self):
        # the claim side found two hedges hardened in the email: code 1 stands, and the line is still printed
        with tempfile.TemporaryDirectory() as folder:
            for name in ("01-research.md", "02-email.md"):
                (Path(folder) / name).write_text((ROOT / "examples" / "hello" / name).read_text(encoding="utf-8"), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = as_agreed(["reconcile", folder])
        self.assertEqual(code, 1, out.getvalue() + err.getvalue())
        self.assertIn("gate: HOLD", out.getvalue())
        self.assertIn("no commitment was declared", err.getvalue())
        self.assertIn("commitments.json", err.getvalue())

    def test_a_declared_commitment_that_is_held_returns_one_not_two(self):
        undeclared = [{k: v for k, v in self.COMMITMENTS[0].items() if k != "carried_by"}]  # nothing says which output carries it
        code, out, err = self.reconcile(undeclared)
        self.assertEqual(code, 1, out + err)
        self.assertNotIn("no commitment was declared", err)


if __name__ == "__main__":
    unittest.main()
