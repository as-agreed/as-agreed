"""The run's flag (D-181, D-191): the verdict first, the value second, review time only against a baseline."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import as_agreed.sdk as ab  # noqa: E402

PAY = dict(act="pay the invoice", obligor="finance", obligee="northwind", modality="MUST", commitment_id="pay",
           limit={"amount": {"max": 18400, "unit": "USD"}}, carried_by={"act": "act", "limit.amount": "amount"})
ACT = {"serves": ["pay"], "kind": "takes_effect", "author": "bank"}  # owes the act and the amount, not the obligor


def out(**kw):
    return {"act": "pay the invoice", **kw}


def run(first, then=None, retries=0, binding=ACT):
    agreed = ab.register("Pay the invoice, up to 18,400 dollars.", [ab.Commitment(**PAY)],
                         adapter=ab.memory_adapter(), source="order.md")

    @ab.step("commit", agreed, commit=lambda note, *, idempotency_key: None, retries=retries, binding=binding)
    def prepare(text, reason=None):
        return then if reason and then is not None else first

    try:
        prepare("Pay it.")
    except ab.DriftHold:
        pass
    return agreed


def flag_of(agreed, **kw):
    with tempfile.TemporaryDirectory() as out:
        return ab.report(agreed, out, quiet=True, **kw).flag_lines


class TheFlag(unittest.TestCase):
    def test_a_held_run_leads_with_the_verdict(self):
        lines = flag_of(run(out(amount=19800.0)))
        self.assertTrue(lines[0].startswith("Kept as agreed: 1 of 2 binding terms, checked at delivery; 1 held (pay "
                                            "limit.amount WIDENED: 19800 where at most 18400 USD was agreed); "
                                            "0 unchecked"), lines[0])
        self.assertEqual(lines[1], "0 of 2 terms kept that would have been lost")

    def test_a_term_held_then_kept_counts_with_its_money(self):
        lines = flag_of(run(out(amount=19800.0), then=out(amount=18000.0), retries=1))
        self.assertTrue(lines[0].startswith("Kept as agreed: 2 of 2 binding terms"), lines[0])
        self.assertEqual(lines[1], "1 of 2 terms kept that would have been lost, 18,400 USD at stake")

    def test_a_missing_owed_key_is_held_and_named(self):
        lines = flag_of(run(out(memo="October")))
        self.assertIn("1 held (pay limit.amount DROPPED", lines[0])

    def test_review_time_only_against_a_baseline(self):
        agreed = run(out(amount=18000.0))
        self.assertEqual(len(flag_of(agreed)), 2)
        self.assertEqual(flag_of(agreed, baseline_minutes=240)[2], "Review returned: 4 h 0 min (your baseline per run)")
        held = run(out(amount=19800.0))
        self.assertTrue(flag_of(held, baseline_minutes=30)[2].startswith("Review returned: none; 1 terms wait"))

    def test_each_step_records_the_flag(self):
        agreed = run(out(amount=18000.0))
        rows = [r for r in agreed.adapter.records(agreed.adapter.run_id) if r.get("kind") == "flag"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["lines"][0].startswith("Kept as agreed: 2 of 2"))

    def test_a_loss_only_recorded_upstream_is_not_counted_as_caught(self):
        from as_agreed.hold import flag
        rows = [{"kind": "commitment", "commitment_id": "pay", "limit": PAY["limit"]},
                {"kind": "hold", "link": "brief#1@1", "passed": False, "policy": {"on_loss": "record"}},
                {"kind": "verdict", "unit_kind": "commitment", "must": True, "unit": "pay", "field": "limit.amount",
                 "link": "brief#1@1", "drift_class": "WIDENED", "state": "as_briefed"},
                {"kind": "verdict", "unit_kind": "commitment", "must": True, "unit": "pay", "field": "limit.amount",
                 "link": "commit#1@1", "drift_class": "PRESERVED", "state": "as_delivered"}]
        self.assertEqual(flag(rows)[1], "0 of 1 terms kept that would have been lost")


if __name__ == "__main__":
    unittest.main()
