"""The flag tells the truth on chains in the data lab's shape (ASAG-50; the lab's three misses of 0.1.0).

- a binding term lost at a link no hold stopped stays lost when a later link restores it, and the gate holds on it;
- a loss a hold stopped at its own link, kept by a later link that passed, still counts as kept and saved;
- the headline counts the fields code can decide (typed); obligor, obligee, act and modality that no link declared go on
  their own line, as not checked by code;
- an untyped term (no typed field, declared at no link) is listed on a line with the words "not restated, untyped":
  a MUST or MUST_NOT one is held at the commit, as in 0.1.0; a SHOULD or MAY one is recorded and never held.

The chains in tests/fixtures/flag_truth/ are invented, in the lab's shape (typed keys carried by `carried_by`, one
untyped prose term); none is a row of the lab's datasets.
"""
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
FIX = ROOT / "tests" / "fixtures" / "flag_truth"

import as_agreed.sdk as ab  # noqa: E402
from as_agreed.__main__ import main as cli  # noqa: E402

# the data lab's parser of the flag's first line (plans/2026-11-22-data-lab/lab/lab.py), copied: the shape must hold
FLAG = re.compile(r"^Kept as agreed: (\d+) of (\d+) binding terms, checked at ([^;]+); (\d+) held(?: \((.*?)\))?; "
                  r"(?:(\d+) changed and not held(?: \((.*?)\))?; )?(\d+) unchecked$")
NAMED = re.compile(r"(?:^|; )(\S+) (\S+) ([A-Z_]+): ")
UNTYPED = "not restated, untyped"


def reconcile(chain):
    """`as-agreed reconcile <chain> --out <temp>`, as the lab runs it: the exit code and the printed lines."""
    with tempfile.TemporaryDirectory() as out:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = cli(["reconcile", str(FIX / chain), "--out", out])
    return code, buf.getvalue().splitlines()


def headline(lines):
    found = [ln for ln in lines if ln.startswith("Kept as agreed:")]
    assert len(found) == 1, found
    return found[0]


def parsed(lines):
    m = FLAG.match(headline(lines))
    assert m, headline(lines)
    return {"kept": int(m.group(1)), "terms": int(m.group(2)), "held": int(m.group(4)),
            "held_fields": NAMED.findall(m.group(5) or ""), "unchecked": int(m.group(8))}


def danger(lines):
    return [ln.strip() for ln in lines if ln.startswith("  DANGER")]


def untyped_line(lines):
    found = [ln for ln in lines if UNTYPED in ln]
    assert len(found) == 1, found
    return found[0]


class MiddleHopLoss(unittest.TestCase):
    def test_a_drop_at_hop_2_restored_at_hop_4_is_held(self):
        code, lines = reconcile("drop-middle")
        self.assertEqual(code, 1, "\n".join(lines))
        f = parsed(lines)
        self.assertEqual((f["kept"], f["terms"], f["held"], f["unchecked"]), (2, 3, 1, 0), headline(lines))
        self.assertEqual(f["held_fields"], [("guests", "limit.guests", "DROPPED")])
        self.assertEqual(len(danger(lines)), 1, danger(lines))
        self.assertTrue(danger(lines)[0].startswith("DANGER commitment guests limit.guests DROPPED (tier 1) at "
                                                    "kitchen#1@1 "), danger(lines))
        self.assertIn("lost at a link no hold stopped", danger(lines)[0])

    def test_the_sdk_commit_holds_on_a_loss_only_recorded_upstream(self):
        s = json.loads((FIX / "saved.json").read_text(encoding="utf-8"))
        agreed = ab.register(s["order"], [ab.Commitment(**s["commitment"])], adapter=ab.memory_adapter(),
                             source="order.md")
        kitchen = ab.step("kitchen", agreed)(lambda text, reason=None: s["kitchen"]["first"])
        handover = ab.step("handover", agreed, commit=lambda out, *, idempotency_key: None, retries=0)(
            lambda text, reason=None: s["handover"])
        kitchen("Cook it.")
        with self.assertRaises(ab.DriftHold):
            handover("Hand it over.")
        with tempfile.TemporaryDirectory() as out:
            lines = ab.report(agreed, out, quiet=True).flag_lines
        self.assertIn("1 held (guests limit.guests DROPPED", lines[0])

    def test_a_loss_stopped_by_a_hold_and_kept_later_is_still_saved(self):
        s = json.loads((FIX / "saved.json").read_text(encoding="utf-8"))
        agreed = ab.register(s["order"], [ab.Commitment(**s["commitment"])], adapter=ab.memory_adapter(),
                             source="order.md")
        kitchen = ab.step("kitchen", agreed, on_loss="hold", retries=1)(
            lambda text, reason=None: s["kitchen"]["retry"] if reason else s["kitchen"]["first"])
        handover = ab.step("handover", agreed, commit=lambda out, *, idempotency_key: None, retries=0)(
            lambda text, reason=None: s["handover"])
        kitchen("Cook it.")
        handover("Hand it over.")  # passes: the kitchen's own hold stopped the loss, so the commit does not hold on it
        with tempfile.TemporaryDirectory() as out:
            rep = ab.report(agreed, out, quiet=True)
        self.assertEqual(rep.code, 0, rep.gate_line)
        self.assertEqual(rep.flag_lines[0], "Kept as agreed: 1 of 1 binding terms, checked at delivery; 0 held; "
                                            "0 unchecked")
        self.assertEqual(rep.flag_lines[1], "1 of 1 terms kept that would have been lost")


class NotRestatedUntyped(unittest.TestCase):
    def test_a_clean_chain_is_held_only_on_the_untyped_line(self):
        code, lines = reconcile("clean-untyped-must")
        self.assertEqual(code, 1, "\n".join(lines))
        f = parsed(lines)
        self.assertEqual((f["kept"], f["terms"], f["held"], f["unchecked"]), (3, 3, 0, 0), headline(lines))
        self.assertEqual(len(danger(lines)), 1, danger(lines))
        self.assertTrue(danger(lines)[0].startswith("DANGER commitment prose_1 UNDECLARED"), danger(lines))
        line = untyped_line(lines)
        self.assertIn("1 held (prose_1 MUST: Label every tray with its allergens)", line)
        self.assertNotIn("recorded", line)

    def test_a_must_not_prose_term_dropped_after_hop_1_holds_the_chain(self):
        code, lines = reconcile("must-not-prose-dropped")
        self.assertEqual(code, 1, "\n".join(lines))
        self.assertEqual(parsed(lines)["held"], 0)
        self.assertIn("1 held (prose_1 MUST_NOT: share the guest list with suppliers)", untyped_line(lines))

    def test_a_must_not_term_carried_at_hop_1_then_gone_is_held_as_a_typed_loss(self):
        # tier 1 read its act at hop 1, so code decides it: it counts in the headline, as 0.1.0 held it
        code, lines = reconcile("must-not-carried-dropped")
        self.assertEqual(code, 1, "\n".join(lines))
        f = parsed(lines)
        self.assertEqual(f["held_fields"], [("guest_list", "act", "DROPPED")], headline(lines))
        self.assertFalse([ln for ln in lines if UNTYPED in ln], "the term was restated at hop 1")

    def test_a_should_prose_term_not_restated_is_listed_and_not_held(self):
        code, lines = reconcile("should-prose")
        self.assertEqual(code, 0, "\n".join(lines))
        self.assertEqual(danger(lines), [])
        self.assertEqual(parsed(lines)["held"], 0)
        line = untyped_line(lines)
        self.assertIn("1 recorded, not held (prose_1 SHOULD: Use reusable trays where possible)", line)
        self.assertNotIn("1 held", line)


class TypedHeadline(unittest.TestCase):
    def test_the_headline_counts_typed_terms_and_untyped_fields_have_their_own_line(self):
        _, lines = reconcile("clean-untyped-must")
        self.assertEqual(headline(lines), "Kept as agreed: 3 of 3 binding terms, checked at delivery; 0 held; "
                                          "0 unchecked")
        # 4 binding commitments, each with obligor, obligee, act and modality that no link declared
        self.assertIn("Not checked by code: 16 untyped fields of binding terms (obligor, obligee, act, modality), "
                      "declared at no link", lines)

    def test_a_chain_with_no_typed_term_still_prints_a_flag_the_lab_reads(self):
        code, lines = reconcile("prose-only")
        self.assertEqual(code, 1)
        self.assertEqual(headline(lines), "Kept as agreed: no binding term was checked in this run")
        self.assertIn("1 held (prose_1 MUST: Label every tray with its allergens)", untyped_line(lines))

    def test_a_review_baseline_counts_untyped_fields_as_waiting(self):
        with tempfile.TemporaryDirectory() as out:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                cli(["reconcile", str(FIX / "clean-untyped-must"), "--out", out, "--baseline-minutes", "30"])
        self.assertIn("Review returned: none; 16 terms wait for your read (your baseline: 30 min per run)",
                      buf.getvalue().splitlines())


if __name__ == "__main__":
    unittest.main()
