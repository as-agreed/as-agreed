"""Three fixed behaviours of the stage folder run and the registration of an order, each named for the way it used to
pass without a word: a must-travel mark on a short sentence vanished, a usage error of `reconcile` left a traceback and
the code of a hold, and a stage declared only as JSON was ignored while a stray `.md` became the commit step."""
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import as_agreed.sdk as ab  # noqa: E402
from as_agreed.__main__ import main as as_agreed  # noqa: E402
from as_agreed.commitment import cut  # noqa: E402
from as_agreed.importers.stages import stage_files, stage_name, stage_outputs  # noqa: E402

THREE = "Do not pay [!].\nHold the wire [must].\nThe vendor must confirm the new bank details by phone [!].\n"
ORDER = "Pay the October invoice from Northwind Freight, up to 18,400 dollars, only if it matches purchase order 4471.\n"
RESEARCH = "The invoice total is 18,400 dollars, per the invoice PDF.\n"
APPROVAL = "Approved for payment today by the finance desk.\n"
WIDENED = {"amount": 19800, "po": "4471"}


def commitments(modality="MUST"):
    return [{"commitment_id": "pay-invoice", "act": "pay the October invoice from Northwind Freight",
             "obligor": "finance-ops", "obligee": "northwind-freight", "modality": modality,
             "limit": {"amount": {"max": 18400, "unit": "USD"}}, "carried_by": {"limit.amount": "amount"}}]


def build(folder, files):
    """Write a dict of name to text (or to a JSON-able value) into the folder."""
    for name, body in files.items():
        (Path(folder) / name).write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")


def reconcile(folder, *options):
    """The command line, in process: (code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = as_agreed(["reconcile", str(folder), *options])
    return code, out.getvalue(), err.getvalue()


def lines(text):
    return [ln for ln in text.splitlines() if ln.strip()]


class MarkOnAShortSentence(unittest.TestCase):
    """Fix 1: a marked sentence under `min_words` registers no claim, and `cut` now says so."""

    def test_three_marked_sentences_register_one_claim_and_two_warnings(self):
        found = cut(THREE, found_at="order#1@1", version="1")
        self.assertEqual(len(found.claims), 1)
        self.assertTrue(found.claims[0].must)
        self.assertEqual(len(found.warnings), 2)
        self.assertEqual([w["line"] for w in found.warnings], [1, 2])  # each names its own line
        for w in found.warnings:
            self.assertEqual(set(w), {"kind", "format", "line", "text"})  # the shape of the placement warning
            self.assertEqual(w["kind"], "warning")
            self.assertEqual(w["format"], "as-agreed/1")
            self.assertIn("registered nothing", w["text"])
            self.assertIn("too few content words", w["text"])
            self.assertIn("min_words=3", w["text"])

    def test_the_warning_names_the_min_words_it_was_given(self):
        text = "Hold the wire for the vendor [!].\n"
        self.assertEqual(cut(text, found_at="order#1@1", version="1", min_words=3).warnings, [])
        found = cut(text, found_at="order#1@1", version="1", min_words=4)
        self.assertEqual(found.claims, [])
        self.assertEqual(len(found.warnings), 1)
        self.assertIn("min_words=4", found.warnings[0]["text"])

    def test_register_hands_both_warnings_to_stderr(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            agreed = ab.register(THREE, adapter=ab.memory_adapter(run_id="stages-1"))
        self.assertEqual(len(agreed.claims), 1)
        self.assertEqual(err.getvalue().count("too few content words"), 2, err.getvalue())

    def test_a_correctly_marked_long_sentence_registers_with_no_warning(self):
        found = cut("The vendor must confirm the new bank details by phone [!].\n", found_at="order#1@1", version="1")
        self.assertEqual(len(found.claims), 1)
        self.assertTrue(found.claims[0].must)
        self.assertEqual(found.warnings, [])

    def test_two_marked_sentences_on_one_line_are_counted_one_by_one(self):
        found = cut("Do not pay [!]. The vendor must confirm the new bank details by phone [!].\n",
                    found_at="order#1@1", version="1")
        self.assertEqual(len(found.claims), 1)
        self.assertEqual([w["line"] for w in found.warnings], [1])

    def test_the_placement_warning_is_unchanged(self):
        after = cut("Confirm the vendor's new bank details by phone before any payment. [!]\n", found_at="order#1@1", version="1")
        self.assertEqual([w["text"] for w in after.warnings],
                         ["must-travel mark not immediately before the final punctuation; nothing marked"])
        inside = cut("Confirm the vendor's [!] new bank details by phone before any payment.\n", found_at="order#1@1", version="1")
        self.assertEqual([w["text"] for w in inside.warnings],
                         ["must-travel mark not immediately before the final punctuation; nothing marked"])
        self.assertEqual([c.must for c in inside.claims], [False])


class ReconcileUsageErrors(unittest.TestCase):
    """Fix 2: a bad argument, a bad `commitments.json` and every AsAgreedError are code 2, one line, no traceback;
    code 1 stays for a standing hold."""

    def folder_with(self, folder, declared):
        build(folder, {"01-order.md": ORDER, "02-research.md": RESEARCH, "03-commit.md": APPROVAL})
        (Path(folder) / "commitments.json").write_text(declared if isinstance(declared, str) else json.dumps(declared),
                                                       encoding="utf-8")

    def assert_usage_error(self, code, out, err, *words):
        self.assertEqual(code, 2, out + err)
        self.assertEqual(len(lines(err)), 1, err)
        self.assertNotIn("Traceback", err)
        for w in words:
            self.assertIn(w, err)

    def test_retries_out_of_range(self):
        with tempfile.TemporaryDirectory() as folder:
            self.folder_with(folder, commitments())
            code, out, err = reconcile(folder, "--retries", "9")
        self.assert_usage_error(code, out, err, "retries must be an integer from 0 to 3", "9")

    def test_a_modality_that_is_not_on_the_ladder(self):
        with tempfile.TemporaryDirectory() as folder:
            self.folder_with(folder, commitments("SHALL"))
            code, out, err = reconcile(folder)
        self.assert_usage_error(code, out, err, "modality 'SHALL' is not on the ladder")

    def test_a_malformed_commitments_json(self):
        with tempfile.TemporaryDirectory() as folder:
            self.folder_with(folder, "[{not json")
            code, out, err = reconcile(folder)
        self.assert_usage_error(code, out, err, "commitments.json", "line 1")  # the file is named

    def test_a_commitments_json_that_is_not_a_list(self):
        with tempfile.TemporaryDirectory() as folder:
            self.folder_with(folder, {"act": "pay"})
            code, out, err = reconcile(folder)
        self.assert_usage_error(code, out, err, "must hold a list")

    def test_an_error_of_the_library_keeps_its_own_code(self):
        # an order that yields no claim and no commitment: EmptyAsAgreed, code 2 in SPEC §6
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {"01-order.md": "Hello.\n", "02-research.md": RESEARCH})
            code, out, err = reconcile(folder)
        self.assert_usage_error(code, out, err, "needs at least one commitment")

    def test_a_standing_hold_is_still_code_one(self):
        with tempfile.TemporaryDirectory() as out_dir:
            code, out, err = reconcile(ROOT / "examples" / "planted-widened", "--out", out_dir)
        self.assertEqual(code, 1, out + err)
        self.assertIn("gate: HOLD", out)


class StageFolder(unittest.TestCase):
    """Fix 3: a numerically prefixed `.json` with no same-stem prose is a stage; the commit is the highest prefix."""

    COMPLETE = {"01-order.md": ORDER, "02-research.md": RESEARCH, "03-commit.md": APPROVAL, "03-commit.json": WIDENED}

    def names(self, folder):
        notes = []
        return [Path(p).name for p in stage_files(str(folder), notes)], notes

    def test_a_json_only_stage_is_a_link_and_its_amount_is_read_at_must(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {"01-order.md": ORDER, "02-research.md": RESEARCH, "03-commit.json": WIDENED,
                           "commitments.json": commitments("MUST")})
            names, _ = self.names(folder)
            code, out, err = reconcile(folder)
        self.assertEqual(names, ["01-order.md", "02-research.md", "03-commit.json"])
        self.assertEqual(code, 1, out + err)
        self.assertEqual(err, "")
        self.assertRegex(out, r"WIDENED\s+commitment pay-invoice limit\.amount\s+19800 where at most 18400 USD was agreed")
        self.assertRegex(out, r"(?m)^commit#1@1\n(?:  .*\n)*?  WIDENED")  # found at the commit, not at research
        self.assertRegex(out, r"gate: HOLD[^\n]*\n  DANGER commitment pay-invoice limit\.amount WIDENED \(tier 1\) at commit#1@1")

    def test_a_json_only_stage_is_a_link_and_its_amount_is_read_at_should(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {"01-order.md": ORDER, "02-research.md": RESEARCH, "03-commit.json": WIDENED,
                           "commitments.json": commitments("SHOULD")})
            code, out, err = reconcile(folder)
        self.assertEqual(code, 1, out + err)  # it exited 0 while the amount was never read
        self.assertRegex(out, r"(?m)^commit#1@1\n(?:  .*\n)*?  WIDENED")
        self.assertRegex(out, r"gate: HOLD[^\n]*\n  DANGER commitment pay-invoice limit\.amount WIDENED \(tier 1\) at commit#1@1")

    def test_a_json_stage_sits_in_prefix_order_and_its_outputs_are_the_json(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {"01-order.md": ORDER, "02-research.json": {"po": "4471"}, "03-commit.md": APPROVAL})
            names, notes = self.names(folder)
            self.assertEqual(names, ["01-order.md", "02-research.json", "03-commit.md"])
            self.assertEqual(notes, [])
            self.assertEqual(stage_outputs(str(Path(folder) / "02-research.json")), {"po": "4471"})
            self.assertEqual(stage_name(str(Path(folder) / "02-research.json")), "research")

    def test_a_json_with_a_same_stem_prose_file_stays_that_stages_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {**self.COMPLETE, "02-research.json": {"po": "4471"}, "commitments.json": commitments()})
            names, notes = self.names(folder)
            self.assertEqual(names, ["01-order.md", "02-research.md", "03-commit.md"])
            self.assertEqual(notes, [])  # commitments.json is never a stage and is not warned about
            self.assertEqual(stage_outputs(str(Path(folder) / "03-commit.md")), WIDENED)

    def test_a_json_stage_next_to_a_txt_stage_of_the_same_stem_is_not_a_second_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {"01-order.txt": ORDER, "02-research.txt": RESEARCH, "02-research.json": {"po": "4471"}})
            names, _ = self.names(folder)
        self.assertEqual(names, ["01-order.txt", "02-research.txt"])

    def test_numeric_order_and_names_are_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {"10-a.md": APPROVAL, "2-b.md": RESEARCH, "1-order.md": ORDER})
            names, _ = self.names(folder)
        self.assertEqual(names, ["1-order.md", "2-b.md", "10-a.md"])
        self.assertEqual([stage_name(n) for n in names], ["order", "b", "a"])

    def test_a_stray_md_does_not_become_the_commit_step(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {**self.COMPLETE, "commitments.json": commitments("SHOULD")})
            names_before, _ = self.names(folder)
            code_before, out_before, err_before = reconcile(folder)
            (Path(folder) / "notes.md").write_text("Remember to ring the vendor about the surcharge on Monday.\n", encoding="utf-8")
            names_after, notes = self.names(folder)
            code_after, out_after, err_after = reconcile(folder, "--out", str(Path(folder) / "second"))
        self.assertEqual(names_after, names_before)
        self.assertEqual(names_after[-1], "03-commit.md")
        self.assertEqual(code_before, 1, out_before + err_before)  # the widened amount at the commit gates ...
        self.assertEqual(code_after, 1, out_after + err_after)  # ... with or without the stray file
        self.assertRegex(out_after, r"gate: HOLD[^\n]*\n  DANGER commitment pay-invoice limit\.amount WIDENED \(tier 1\) at commit#1@1")
        self.assertNotIn("notes#", out_after)  # no link was made of it
        self.assertEqual(err_before, "")
        self.assertEqual(len(lines(err_after)), 1, err_after)  # named once, on stderr
        self.assertIn("notes.md", err_after)
        self.assertIn("not a stage", err_after)
        self.assertEqual(len(notes), 1)

    def test_every_unprefixed_prose_file_is_named_once(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {**self.COMPLETE, "notes.md": "Ring the vendor on Monday.\n", "todo.txt": "Ring the vendor on Monday.\n",
                           "commitments.json": commitments()})
            code, out, err = reconcile(folder)
        for name in ("notes.md", "todo.txt"):
            self.assertEqual(err.count(name), 1, err)
        self.assertEqual(len(lines(err)), 2, err)
        self.assertNotIn("commitments.json", err)

    def test_a_json_that_is_not_a_stage_is_not_warned_about(self):
        # an unprefixed `.json` was never a stage, so it cannot have become the commit; the scripted retry beside a
        # commit holds a number a prose stage already holds; neither is a mistake worth a line on stderr
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {**self.COMPLETE, "03-commit-retry.json": {"amount": 18400}, "settings.json": {"a": 1},
                           "commitments.json": commitments()})
            names, notes = self.names(folder)
            code, out, err = reconcile(folder)
        self.assertEqual(names, ["01-order.md", "02-research.md", "03-commit.md"])
        self.assertEqual(notes, [])
        self.assertEqual(err, "")
        self.assertNotIn("retry#", out)

    def test_fewer_than_two_stages_is_code_two_and_names_the_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, {"01-order.md": ORDER, "notes.md": "Ring the vendor on Monday.\n"})
            code, out, err = reconcile(folder)
            self.assertEqual(code, 2, out + err)
            self.assertIn(folder, err)
            self.assertIn("need at least two stage files", err)
            self.assertIn("notes.md", err)  # and the file that was not counted is still named
        code, out, err = reconcile(Path(folder) / "missing")
        self.assertEqual(code, 2)
        self.assertIn("need at least two stage files", err)
        self.assertIn("missing", err)


class ShippedExamples(unittest.TestCase):
    """The three shipped examples keep the codes `reconcile` gave them before the three fixes (recorded 2026-11-01 on
    Python 3.14.6 and 3.9.6): 1, 1 and 1. The scripted retry beside the commit is not a stage, so no link is made of
    it, and nothing is said of it."""

    def run_example(self, name):
        with tempfile.TemporaryDirectory() as out_dir:
            return reconcile(ROOT / "examples" / name, "--out", out_dir)

    def test_hello_holds_on_the_hardened_hedges(self):
        code, out, err = self.run_example("hello")
        self.assertEqual(code, 1, out + err)
        self.assertIn("gate: HOLD (2 reasons)", out)
        self.assertIn("no commitment was declared", err)

    def test_planted_failure_holds_on_the_dropped_phone_check(self):
        code, out, err = self.run_example("planted-failure")
        self.assertEqual(code, 1, out + err)
        self.assertIn("gate: HOLD (3 reasons)", out)
        self.assertNotIn("commit-retry#", out)
        self.assertEqual(err, "")  # the scripted retry is no stage and no mistake: nothing is said of it

    def test_planted_widened_holds_on_the_widened_amount(self):
        code, out, err = self.run_example("planted-widened")
        self.assertEqual(code, 1, out + err)
        self.assertRegex(out, r"gate: HOLD \(1 reasons\)\n  DANGER commitment pay-invoice limit\.amount WIDENED \(tier 1\) at commit#1@1")
        self.assertNotIn("commit-retry#", out)
        self.assertEqual(err, "")

    def test_the_examples_folders_are_left_as_they_were(self):
        before = sorted(p.name for p in (ROOT / "examples" / "planted-widened").iterdir())
        self.run_example("planted-widened")
        self.assertEqual(sorted(p.name for p in (ROOT / "examples" / "planted-widened").iterdir()), before)


if __name__ == "__main__":
    unittest.main()
