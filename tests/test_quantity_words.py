"""Numbers in words (0.1.1): a declared value written in English number words counts as its number in the field
check, "three" equals 3, and a gate can read the figures a free text states, in digits and in words.

The case that asks for it is X64's one missed act, `p13` (invented data, `fixtures/quantity_words/`): a draft says
"well over three hundred commitments" where 271 was agreed, and a gate that reads figures by digits finds none.
A string of digits stays text, as in 0.1.0: only a value that reads wholly as number words is read as a number, and
only where the agreed side is a number or a numeric bound.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import as_agreed.sdk as ab  # noqa: E402
from as_agreed import drift  # noqa: E402
from as_agreed.commitment import Criterion  # noqa: E402
from as_agreed.hold import Policy, holds  # noqa: E402
from as_agreed.quantity import figures, from_words  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "quantity_words"
RECORD = json.loads((FIXTURES / "record-as-agreed-0.1.1.json").read_text(encoding="utf-8"))
ACTS = {a["request_id"]: a for a in (json.loads(line) for line in
                                     (FIXTURES / "acts.jsonl").read_text(encoding="utf-8").splitlines() if line)}
KEY = json.loads((FIXTURES / "key.json").read_text(encoding="utf-8"))
FAITHFUL = sorted(i for i, k in KEY.items() if k["kind"] == "faithful")
URL = re.compile(r"https?://[^\s<>()\"']+")


def term(act):
    return next(c for c in RECORD["commitments"] if c["commitment_id"] == act)


def outputs_of(req, text_figures=None):
    """What a gate hands the check for one act, as X64's gate builds it, with the figures read by the library."""
    out = {"actor": req["actor"], "target": req["target"]}
    out.update(req["args"])
    if "tag" in out:
        out["tag"] = str(out["tag"])
    for key in ("text", "body"):
        if key in req["args"]:
            text = req["args"][key]
            found = figures(text) if text_figures is None else text_figures
            out["text_figures"] = found or ["none"]
            out["text_links"] = sorted({u.rstrip(".,;:!?") for u in URL.findall(text)}) or ["none"]
    if req["act"] == "draft":
        out["channel"] = req["target"].split(":", 1)[1]
    return out


def field_check(req, text_figures=None):
    """The act checked against its term of the record, delivered, held on any change, loss or escalation, as the
    X64 gate holds it. Returns (passed, the held fields)."""
    agreed = ab.register(str(RECORD["order"]), [ab.Commitment.from_mapping(term(req["act"]))],
                         adapter=ab.memory_adapter(), source="record-as-agreed-0.1.1.json")
    link = f"gate-{req['act']}#1@1"
    verdicts = ab.check(outputs_of(req, text_figures), agreed, delivered=True, link=link,
                        binding={"serves": [req["act"]], "kind": "takes_effect", "author": req["actor"]})
    h = ab.hold(verdicts, agreed, policy=Policy("commit", "hold", on_undecided="hold"), link=link)
    held = [f"{v.field} {v.drift_class or v.undecided}" for v in verdicts
            if holds(v) or getattr(v, "undecided", None) == drift.ESCALATED]
    return bool(h.passed) and not held, held


class ReadingWords(unittest.TestCase):
    def test_the_map_cases(self):
        for words, n in (("three", 3), ("twenty-one", 21), ("three hundred", 300), ("one thousand two hundred", 1200)):
            self.assertEqual(from_words(words), n, words)

    def test_spellings_of_one_number(self):
        for words, n in (("Three", 3), ("  twenty one ", 21), ("twenty - one", 21), ("TWO HUNDRED AND SEVENTY-ONE", 271),
                         ("zero", 0), ("a hundred", 100), ("a thousand", 1000), ("twelve hundred", 1200),
                         ("one thousand and five", 1005), ("ten thousand", 10000),
                         ("one million two hundred thousand five", 1200005), ("nine billion", 9 * 10 ** 9)):
            self.assertEqual(from_words(words), n, words)

    def test_only_a_value_wholly_in_words_is_read(self):
        for value in ("300", "3", "3 hundred", "about three", "three or four", "three hundred and", "hundred",
                      "thousand", "three three", "twenty twenty", "two thousand thousand", "a", "and", "a dozen",
                      "dozen", "first", "hundreds", "minus three", "three.", "", "   ", None, 3, 3.0, True, ["three"]):
            self.assertIsNone(from_words(value), repr(value))


class ReadingFigures(unittest.TestCase):
    def test_the_map_example(self):
        self.assertIn(300, figures("well over three hundred commitments"))

    def test_digits_and_words_in_order_each_once(self):
        self.assertEqual(figures("three apples, 4 pears, twenty-one plums and three more apples"), [3, 4, 21])

    def test_dates_versions_ratios_percents_and_thousands(self):
        self.assertEqual(
            figures("We shipped v0.1.1 on 2026-11-21: 9 of 10, then 9/10 and nine out of ten, 45% of 1,200 runs, "
                    "on Python 3.9 and 3.14."),
            ["0.1.1", "2026-11-21", "9 of 10", "45%", 1200, 3.9, 3.14])

    def test_a_link_is_not_read_and_words_that_are_not_numbers_are_not(self):
        self.assertEqual(figures("A new release is out: https://example.org/v0.1.1/42. The first update."), [])

    def test_a_padded_reference_stays_text(self):
        self.assertEqual(figures("ticket 0042 closed"), ["0042"])

    def test_not_text_has_no_figures(self):
        self.assertEqual(figures(None), [])
        self.assertEqual(figures(300), [])


class TheFieldCheck(unittest.TestCase):
    def test_a_numeric_bound_reads_words(self):
        self.assertEqual(drift.limit_entry("n", {"max": 5}, "three").drift_class, drift.PRESERVED)
        self.assertEqual(drift.limit_entry("n", {"max": 5}, "seven").drift_class, drift.WIDENED)
        self.assertEqual(drift.limit_entry("n", {"min": 300}, "three hundred").drift_class, drift.PRESERVED)
        self.assertEqual(drift.limit_entry("n", {"min": 300}, "two hundred").drift_class, drift.WIDENED)

    def test_a_restated_bound_reads_words_and_converts_units(self):
        self.assertEqual(drift.limit_entry("n", {"max": 5}, {"max": "five"}).drift_class, drift.PRESERVED)
        self.assertEqual(drift.limit_entry("n", {"max": 5}, {"max": "six"}).drift_class, drift.WIDENED)
        self.assertEqual(drift.limit_entry("w", {"max": 2000, "unit": "g"}, {"max": "two", "unit": "kg"}).drift_class,
                         drift.PRESERVED)

    def test_a_set_of_numbers_reads_words(self):
        self.assertEqual(drift.limit_entry("issue", {"in": [12, 15]}, "twelve").drift_class, drift.PRESERVED)
        self.assertEqual(drift.limit_entry("issue", {"in": [12, 15]}, "seven").drift_class, drift.WIDENED)
        self.assertEqual(drift.limit_entry("f", {"in": ["271"]}, "two hundred seventy-one").drift_class,
                         drift.PRESERVED)
        self.assertEqual(drift.limit_entry("issue", {"not_in": [13]}, "thirteen").drift_class, drift.WIDENED)
        self.assertEqual(drift.limit_entry("issue", {"in": [12, 15]}, {"in": ["twelve", "fifteen"]}).drift_class,
                         drift.PRESERVED)

    def test_a_set_of_words_keeps_its_words(self):
        self.assertEqual(drift.limit_entry("tier", {"in": ["one", "two"]}, "one").drift_class, drift.PRESERVED)
        self.assertEqual(drift.limit_entry("channel", {"in": ["x", "linkedin"]}, "one").drift_class, drift.WIDENED)

    def test_an_exact_number_reads_words(self):
        c = Criterion("three reviewers signed", "machine_checkable", field="reviewers", equals=3)
        self.assertEqual(drift.criterion_value("ac", c, "three").drift_class, drift.PRESERVED)
        self.assertEqual(drift.criterion_value("ac", c, "four").drift_class, drift.WEAKENED)
        self.assertEqual(drift.condition_entry("condition.copies", 3, "three").drift_class, drift.PRESERVED)

    def test_words_are_read_only_where_the_agreed_side_is_a_number(self):
        self.assertEqual(drift.condition_entry("condition.copies", "3", "three").drift_class, drift.RECONDITIONED)
        self.assertEqual(drift.condition_entry("condition.copies", "three", 3).drift_class, drift.RECONDITIONED)

    def test_a_string_of_digits_stays_text_as_in_0_1_0(self):
        c = Criterion("the account is 12", "machine_checkable", field="account", equals=12)
        self.assertEqual(drift.criterion_value("ac", c, "12").drift_class, drift.PRESERVED)
        self.assertEqual(drift.criterion_value("ac", c, "012").drift_class, drift.WEAKENED)
        self.assertEqual(drift.limit_entry("acct", {"in": [12]}, "0012").drift_class, drift.WIDENED)
        self.assertEqual(drift.limit_entry("n", {"max": 5}, "about three").undecided, drift.ESCALATED)

    def test_through_the_taught_import(self):
        agreed = ab.register("Send at most five reminders.",
                             [ab.Commitment(act="send reminders", obligor="a", obligee="b", modality="MUST",
                                            commitment_id="c1", limit={"reminders": {"max": 5}},
                                            carried_by={"limit.reminders": "reminders"})],
                             adapter=ab.memory_adapter(), source="o.md")
        pol = Policy("commit", "hold", on_undecided="hold")
        kept = ab.hold(ab.check({"reminders": "three"}, agreed, delivered=True), agreed, policy=pol)
        held = ab.hold(ab.check({"reminders": "seven"}, agreed, delivered=True), agreed, policy=pol)
        self.assertTrue(kept.passed, kept.lines)
        self.assertFalse(held.passed)


class X64(unittest.TestCase):
    def test_the_fixture_is_x64s_twenty_faithful_acts_and_p13(self):
        self.assertEqual(len(FAITHFUL), 20)
        self.assertEqual(KEY["p13"]["kind"], "drift")
        self.assertEqual(sorted(ACTS), sorted(FAITHFUL + ["p13"]))

    def test_p13_is_held(self):
        req = ACTS["p13"]
        agreed = term("draft")["limit"]["text_figures"]
        found = figures(req["args"]["body"])
        self.assertEqual(found, ["0.1.1", 300])
        self.assertEqual(drift.limit_entry("text_figures", agreed, found).drift_class, drift.WIDENED)
        passed, held = field_check(req)
        self.assertFalse(passed)
        self.assertEqual(held, ["limit.text_figures WIDENED"])

    def test_p13_passed_when_figures_were_read_by_digits_alone(self):
        # X64's gate read "0.1.1" and nothing else from the body; that is how the act got through
        passed, held = field_check(ACTS["p13"], text_figures=["0.1.1"])
        self.assertTrue(passed, held)

    def test_no_number_word_in_a_faithful_body_is_outside_the_agreed_figures(self):
        agreed = term("draft")["limit"]["text_figures"]
        for rid in FAITHFUL:
            body = ACTS[rid]["args"].get("body") or ACTS[rid]["args"].get("text") or ""
            in_words = figures(re.sub(r"\S*\d\S*", " ", body))
            for f in in_words:
                self.assertEqual(drift.limit_entry("text_figures", agreed, [f]).drift_class, drift.PRESERVED,
                                 f"{rid}: {f!r}")

    def test_no_faithful_act_is_held(self):
        held = {}
        for rid in FAITHFUL:
            passed, fields = field_check(ACTS[rid])
            if not passed:
                held[rid] = fields
        self.assertEqual(held, {})


if __name__ == "__main__":
    unittest.main()
