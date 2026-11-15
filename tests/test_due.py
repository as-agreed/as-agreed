"""The due list (0.1.0): a step's binding names what it owes; an owed key it does not deliver is DROPPED by code."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import as_agreed.sdk as ab  # noqa: E402
from as_agreed.due import Due  # noqa: E402

PAY = dict(act="pay the invoice", obligor="finance", obligee="northwind", modality="MUST", commitment_id="pay",
           limit={"amount": {"max": 18400, "unit": "USD"}},
           carried_by={"obligor": "payer", "limit.amount": "amount"})
SHIP = dict(act="ship the order", obligor="warehouse", obligee="client", modality="MUST", commitment_id="ship",
            deadline="2026-12-01", carried_by={"deadline": "ship_by"})


def record(*terms):
    ad = ab.memory_adapter()
    return ab.register("Pay the invoice, up to 18,400 dollars; ship by 1 December.",
                       [ab.Commitment(**t) for t in terms], adapter=ad, source="order.md"), ad


def classes(verdicts):
    return {(v.unit, v.field): v.drift_class or v.undecided for v in verdicts}


ACT = {"serves": ["pay"], "kind": "takes_effect", "author": "finance"}


class OwedKeyMissing(unittest.TestCase):
    def test_an_owed_amount_missing_is_held_and_the_effect_never_runs(self):
        agreed, _ = record(PAY)
        paid = []

        def pay(note, *, idempotency_key):
            paid.append(note)

        @ab.step("commit", agreed, commit=pay, retries=0, binding=ACT)
        def prepare(text, reason=None):
            return {"payer": "finance", "memo": "October invoice"}

        with self.assertRaises(ab.DriftHold) as held:
            prepare("Pay the invoice.")
        self.assertEqual(paid, [])
        self.assertIn("DROPPED", str(held.exception))

    def test_the_amount_present_and_within_the_limit_passes(self):
        agreed, _ = record(PAY)
        paid = []

        @ab.step("commit", agreed, commit=lambda note, *, idempotency_key: paid.append(note), binding=ACT)
        def prepare(text, reason=None):
            return {"payer": "finance", "amount": 18000.0}

        prepare("Pay the invoice.")
        self.assertEqual(len(paid), 1)

    def test_a_binding_overrides_undeclared_for_what_is_owed(self):
        agreed, _ = record(PAY)
        got = classes(ab.check({"payer": "finance"}, agreed, delivered=True, on_missing_key="undeclared", binding=ACT))
        self.assertEqual(got[("pay", "limit.amount")], "DROPPED")


class OnlyWhatIsServed(unittest.TestCase):
    def test_a_commitment_the_step_does_not_serve_is_not_read(self):
        agreed, _ = record(PAY, SHIP)
        got = classes(ab.check({"payer": "finance", "amount": 18000.0}, agreed, delivered=True, binding=ACT))
        self.assertEqual({u for u, _ in got}, {"pay"})

    def test_without_a_binding_the_unserved_commitment_drops_as_in_003(self):
        agreed, _ = record(PAY, SHIP)
        got = classes(ab.check({"payer": "finance", "amount": 18000.0}, agreed, delivered=True))
        self.assertEqual(got[("ship", "deadline")], "DROPPED")

    def test_a_change_to_a_field_not_owed_still_counts(self):
        agreed, _ = record(PAY)
        brief = {"serves": ["pay"], "kind": "mentions"}
        got = classes(ab.check({"amount": 19800.0}, agreed, delivered=True, binding=brief))
        self.assertEqual(got, {("pay", "limit.amount"): "WIDENED"})

    def test_a_null_given_for_a_field_not_owed_still_counts(self):
        agreed, _ = record(PAY)
        got = classes(ab.check({"amount": None}, agreed, delivered=True, binding={"serves": ["pay"], "kind": "mentions"}))
        self.assertEqual(got, {("pay", "limit.amount"): "DROPPED"})

    def test_a_brief_owes_everything_but_the_obligee(self):
        agreed, _ = record(PAY)
        due = Due({"serves": ["pay"], "kind": "sets_terms"}, agreed)
        self.assertEqual(due.owed(agreed.commitment("pay")), ["obligor", "act", "modality", "limit.amount"])


class TheObligorFix(unittest.TestCase):
    """X60: an act owes the obligor only when the obligor wrote it or receives it."""

    def test_a_third_party_act_without_the_obligor_is_not_held_for_it(self):
        agreed, _ = record(PAY)
        bank = {"serves": ["pay"], "kind": "takes_effect", "author": "bank", "recipient": "northwind"}
        got = classes(ab.check({"amount": 18000.0}, agreed, delivered=True, binding=bank))
        self.assertNotIn(("pay", "obligor"), got)
        self.assertEqual(got[("pay", "limit.amount")], "PRESERVED")

    def test_the_same_act_written_by_the_obligor_is_held(self):
        agreed, _ = record(PAY)
        got = classes(ab.check({"amount": 18000.0}, agreed, delivered=True, binding=ACT))
        self.assertEqual(got[("pay", "obligor")], "DROPPED")

    def test_names_match_without_case_or_spacing(self):
        agreed, _ = record(PAY)
        due = Due({"serves": ["pay"], "kind": "takes_effect", "author": "  Finance "}, agreed)
        self.assertIn("obligor", due.owed(agreed.commitment("pay")))


class ABadBindingIsRefused(unittest.TestCase):
    def test_refusals(self):
        agreed, _ = record(PAY)
        for bad in ({"serves": ["nope"], "kind": "takes_effect"}, {"serves": "pay", "kind": "takes_effect"},
                    {"serves": ["pay"], "kind": "acts"}, {"serves": ["pay"], "kind": "mentions", "role": "x"}):
            with self.assertRaises(ValueError):
                ab.check({}, agreed, binding=bad)


if __name__ == "__main__":
    unittest.main()
