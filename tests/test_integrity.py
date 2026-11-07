"""The as-agreed reference cannot change after registration (SPEC §2.2, §3): a step, `check` and `hold` refuse with
NoCommitment a record that is not registered in the adapter's sink for the run, or whose commitments in memory no longer
match the rows registered for it. Each refusal writes nothing."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import as_agreed.sdk as ab  # noqa: E402
from as_agreed.adapters.base import JsonlSink  # noqa: E402
from as_agreed.errors import DriftHold  # noqa: E402

ORDER = "Pay the vendor invoice, at most 18400 USD, by 2026-12-01."


def payment() -> "ab.Commitment":
    return ab.Commitment(act="pay the vendor invoice", obligor="ap-desk", obligee="vendor", modality="MUST",
                         limit={"amount": {"max": 18400, "unit": "USD"}}, deadline="2026-12-01",
                         carried_by={"limit.amount": "amount"})


def registered(run_id="int-1"):
    ad = ab.memory_adapter(run_id=run_id)
    agreed = ab.register(ORDER, [payment()], adapter=ad)
    return ad, agreed


class Base(unittest.TestCase):
    def setUp(self):
        self.ad, self.agreed = registered()
        self.cid = self.agreed.commitments[0].commitment_id
        self.paid = []

        @ab.step("pay", self.agreed, commit=lambda out, idempotency_key: self.paid.append(out))
        def pay(amount):
            return {"amount": amount}

        self.pay = pay

    def rows(self):
        return list(self.ad.sink.read())

    def assertRefused(self, call, *needles):
        before = self.rows()
        with self.assertRaises(ab.NoCommitment) as cm:
            call()
        self.assertEqual(self.rows(), before, "a refusal must write nothing")
        self.assertEqual(self.paid, [], "a refusal must commit nothing")
        for n in needles:
            self.assertIn(n, str(cm.exception))
        self.assertEqual(cm.exception.code, 2)
        return str(cm.exception)


class StepRefusesAChangedReference(Base):
    def test_untouched_record_still_runs_and_commits(self):
        result = self.pay(18000)
        self.assertEqual(result, {"amount": 18000})
        self.assertEqual(self.paid, [{"amount": 18000}])
        kinds = {r["kind"] for r in self.rows()}
        self.assertTrue({"link", "verdict", "hold"} <= kinds)

    def test_a_payment_over_the_limit_is_drift_on_an_untouched_record(self):
        with self.assertRaises(DriftHold):
            self.pay(19800)
        self.assertEqual(self.paid, [])

    def test_a_raised_limit_in_memory_no_longer_lets_the_payment_pass(self):
        self.agreed.commitments[0].limit["amount"]["max"] = 999999
        self.assertRefused(lambda: self.pay(19800), self.cid)

    def test_clearing_the_commitments_in_memory_is_refused(self):
        self.agreed.commitments.clear()
        self.assertRefused(lambda: self.pay(19800), self.cid)

    def test_the_error_names_the_commitment_and_says_the_record_is_the_reference(self):
        self.agreed.commitments[0].limit["amount"]["max"] = 999999
        msg = self.assertRefused(lambda: self.pay(19800))
        self.assertIn(self.cid, msg)
        self.assertIn(self.agreed.id, msg)
        self.assertIn("is the reference", msg)
        self.assertIn("cannot be changed after registration", msg)

    def test_a_commitment_added_in_memory_is_refused_and_named(self):
        extra = ab.Commitment(act="send the receipt", obligor="ap-desk", obligee="vendor", modality="MAY")
        self.agreed.commitments.append(extra)
        msg = self.assertRefused(lambda: self.pay(18000), extra.commitment_id)
        self.assertIn("never registered", msg)

    def test_a_commitment_duplicated_in_memory_is_refused(self):
        self.agreed.commitments.append(self.agreed.commitments[0])
        self.assertRefused(lambda: self.pay(18000), self.cid)

    def test_a_commitment_replaced_under_another_id_names_both(self):
        old = self.agreed.commitments[0]
        self.agreed.commitments[0] = ab.Commitment(
            act=old.act, obligor=old.obligor, obligee=old.obligee, modality=old.modality,
            limit={"amount": {"max": 999999}}, commitment_id="swapped", carried_by={"limit.amount": "amount"})
        self.assertRefused(lambda: self.pay(19800), self.cid, "swapped")

    def test_each_field_of_the_fingerprint_is_guarded(self):
        edits = {
            "act": lambda c: setattr(c, "act", "pay nothing"),
            "obligor": lambda c: setattr(c, "obligor", "someone-else"),
            "obligee": lambda c: setattr(c, "obligee", "someone-else"),
            "modality": lambda c: setattr(c, "modality", "MAY"),
            "deadline": lambda c: setattr(c, "deadline", "2027-12-01"),
            "purpose": lambda c: setattr(c, "purpose", "a new purpose"),
            "condition": lambda c: setattr(c, "condition", "only on Fridays"),
            "limit removed": lambda c: setattr(c, "limit", None),
            "limit unit": lambda c: c.limit["amount"].__setitem__("unit", "EUR"),
            "criterion added": lambda c: setattr(c, "acceptance_criteria",
                                                 [ab.Criterion("a receipt is attached")]),
        }
        for label, edit in edits.items():
            with self.subTest(label):
                ad, agreed = registered(run_id="int-" + label.replace(" ", "-"))
                edit(agreed.commitments[0])

                @ab.step("pay", agreed, commit=True)
                def pay(amount):
                    return {"amount": amount}

                before = list(ad.sink.read())
                with self.assertRaises(ab.NoCommitment) as cm:
                    pay(18000)
                self.assertIn(agreed.commitments[0].commitment_id, str(cm.exception))
                self.assertEqual(list(ad.sink.read()), before)

    def test_a_long_lived_run_cannot_relax_the_reference_between_requests(self):
        ad, agreed = registered(run_id="int-run")
        paid = []

        @ab.step("pay", commit=lambda out, idempotency_key: paid.append(out))
        def pay(amount):
            return {"amount": amount}

        with ab.run(agreed):
            self.assertEqual(pay(18000), {"amount": 18000})
            agreed.commitments[0].limit["amount"]["max"] = 999999
            before = list(ad.sink.read())
            with self.assertRaises(ab.NoCommitment) as cm:
                pay(19800)
        self.assertIn(agreed.commitments[0].commitment_id, str(cm.exception))
        self.assertEqual(paid, [{"amount": 18000}])
        self.assertEqual(list(ad.sink.read()), before)

    def test_the_async_step_refuses_too(self):
        paid = []

        @ab.step("pay-async", self.agreed, commit=lambda out, idempotency_key: paid.append(out))
        async def pay(amount):
            return {"amount": amount}

        self.agreed.commitments[0].limit["amount"]["max"] = 999999
        before = self.rows()
        with self.assertRaises(ab.NoCommitment) as cm:
            asyncio.run(pay(19800))
        self.assertIn(self.cid, str(cm.exception))
        self.assertEqual((paid, self.rows()), ([], before))

    def test_a_record_registered_in_another_sink_is_refused(self):
        other = ab.memory_adapter(run_id="int-1")  # same run id, a sink that never saw the registration

        @ab.step("pay", self.agreed, adapter=other, commit=True)
        def pay(amount):
            return {"amount": amount}

        with self.assertRaises(ab.NoCommitment):
            pay(18000)
        self.assertEqual(other.sink.read(), [])


class FullRecordSurvivesTheRoundTrip(unittest.TestCase):
    """The comparison rebuilds each commitment from its recorded row; a field that did not survive the round trip
    would refuse every honest run."""

    def test_a_commitment_with_every_field_declared_is_not_refused(self):
        ad = ab.memory_adapter(run_id="int-full")
        parent = ab.Commitment(act="close the quarter", obligor="finance", obligee="board", modality="MUST",
                               commitment_id="parent-1")
        child = ab.Commitment(
            act="pay the vendor invoice", obligor="ap-desk", obligee="vendor", modality="MUST_NOT",
            condition={"approved": "true", "region": {"in": ["EU", "UK"]}},
            limit={"amount": {"max": "18,400", "unit": "USD"}, "region": ["UK", "EU"], "window": {"until": "2026-12-01"},
                   "note": "no partial payments"},
            deadline="2026-12-01", purpose="keep the supplier", parent_id="parent-1",
            acceptance_criteria=[ab.Criterion("a receipt is attached", "machine_checkable", field="receipt"),
                                 ab.Criterion("the controller agrees")],
            carried_by={"limit.amount": "amount", "deadline": "due"}, version=2)
        prose = ab.Commitment(act="keep the audit trail", obligor="ap-desk", obligee="audit", modality="SHOULD",
                              condition="when the vendor asks")
        agreed = ab.register(ORDER, [parent, child, prose], adapter=ad, version=2, claims=["The vendor is in Berlin."])
        before = list(ad.sink.read())
        verdicts = ab.check("nothing declared here", agreed)
        self.assertTrue(verdicts)

        @ab.step("note", agreed)
        def note():
            return "an intermediate note, nothing committed"

        note()
        self.assertGreater(len(ad.sink.read()), len(before))

    def test_a_second_registration_at_a_higher_version_keeps_both_records_valid(self):
        ad = ab.memory_adapter(run_id="int-two")
        first = ab.register(ORDER, [payment()], adapter=ad)
        second = ab.register(ORDER + " Add a receipt.", [payment()], adapter=ad, version=2)
        self.assertTrue(ab.check("nothing declared", first))
        self.assertTrue(ab.check("nothing declared", second))
        first.commitments[0].limit["amount"]["max"] = 999999
        with self.assertRaises(ab.NoCommitment):
            ab.check("nothing declared", first)
        self.assertTrue(ab.check("nothing declared", second))


class TheWholeRecordIsGuarded(Base):
    """`fingerprint()` leaves out `carried_by`, `parent_id`, `version`, `source` and `captured_at`; the comparison reads
    the commitment's whole record, because `carried_by` decides which output keys a verdict reads."""

    def test_repointing_carried_by_to_another_key_is_refused_and_named(self):
        self.agreed.commitments[0].carried_by = {"limit.amount": "other_key"}
        msg = self.assertRefused(lambda: self.pay(19800), self.cid, "carried_by")
        self.assertIn("cannot be changed after registration", msg)

    def test_each_field_outside_the_fingerprint_is_guarded(self):
        edits = {
            "carried_by removed": lambda c: setattr(c, "carried_by", None),
            "parent_id": lambda c: setattr(c, "parent_id", "someone-elses-parent"),
            "version": lambda c: setattr(c, "version", 7),
            "source": lambda c: setattr(c, "source", {"artifact": "forged.md", "offset": 0}),
            "captured_at": lambda c: setattr(c, "captured_at", "2020-01-01T00:00:00Z"),
        }
        for label, edit in edits.items():
            with self.subTest(label):
                ad, agreed = registered(run_id="int-" + label.replace(" ", "-"))
                edit(agreed.commitments[0])

                @ab.step("pay", agreed, commit=True)
                def pay(amount):
                    return {"amount": amount}

                before = list(ad.sink.read())
                with self.assertRaises(ab.NoCommitment) as cm:
                    pay(18000)
                self.assertIn(agreed.commitments[0].commitment_id, str(cm.exception))
                self.assertIn(label.split()[0], str(cm.exception), "the message names the field that differs")
                self.assertEqual(list(ad.sink.read()), before)

    def test_check_and_hold_refuse_a_repointed_carried_by(self):
        verdicts = ab.check({"amount": 19800}, self.agreed)
        self.agreed.commitments[0].carried_by = {"limit.amount": "other_key"}
        for call in (lambda: ab.check({"amount": 19800}, self.agreed), lambda: ab.hold(verdicts, self.agreed)):
            with self.assertRaises(ab.NoCommitment) as cm:
                call()
            self.assertIn(self.cid, str(cm.exception))


class FaithfulRegistrationCompares(unittest.TestCase):
    """What `build` fills in at registration (`source`, `captured_at`) and what a sink does to a record (JSON, key
    order) must never make an honest record look changed."""

    def run_untouched(self, ad, agreed):
        before = len(ad.sink.read())
        self.assertTrue(ab.check("nothing declared", agreed))

        @ab.step("note", agreed)
        def note():
            return "an intermediate note"

        note()
        self.assertGreater(len(ad.sink.read()), before)

    def test_source_given_on_the_commitment(self):
        ad = ab.memory_adapter(run_id="int-src-given")
        c = ab.Commitment(act="pay the vendor invoice", obligor="ap-desk", obligee="vendor", modality="MUST",
                          source={"artifact": "contract.md", "offset": 12}, captured_at="2026-10-01T09:00:00Z",
                          parent_id="p1")
        agreed = ab.register(ORDER, [c], adapter=ad)
        self.assertEqual(agreed.commitments[0].source, {"artifact": "contract.md", "offset": 12})
        self.run_untouched(ad, agreed)

    def test_source_given_as_text_on_the_commitment(self):
        ad = ab.memory_adapter(run_id="int-src-text")
        c = ab.Commitment(act="pay the vendor invoice", obligor="ap-desk", obligee="vendor", modality="MUST",
                          source="contract.md")
        agreed = ab.register(ORDER, [c], adapter=ad)
        self.run_untouched(ad, agreed)

    def test_source_omitted_so_build_fills_it_in(self):
        ad = ab.memory_adapter(run_id="int-src-omitted")
        agreed = ab.register(ORDER, [payment()], source="order.md", adapter=ad)
        self.assertEqual(agreed.commitments[0].source["artifact"], "order.md")
        self.assertIsNotNone(agreed.commitments[0].captured_at)
        self.run_untouched(ad, agreed)
        bare = ab.memory_adapter(run_id="int-src-default")
        self.run_untouched(bare, ab.register(ORDER, [payment()], adapter=bare))  # no source anywhere: "order"

    def test_a_commitment_given_as_a_mapping_is_registered_and_compares(self):
        ad = ab.memory_adapter(run_id="int-mapping")
        agreed = ab.register(ORDER, [{"act": "pay the vendor invoice", "obligor": "ap-desk", "obligee": "vendor",
                                      "modality": "MUST", "limit": {"amount": {"max": 18400}}}], adapter=ad)
        self.run_untouched(ad, agreed)

    def test_the_order_of_entries_is_not_a_change(self):
        ad = ab.memory_adapter(run_id="int-order")
        c = ab.Commitment(act="pay the vendor invoice", obligor="ap-desk", obligee="vendor", modality="MUST",
                          limit={"amount": {"max": 18400, "unit": "USD"}, "region": ["UK", "EU"]},
                          condition={"approved": "yes", "stage": "final"}, deadline="2026-12-01",
                          carried_by={"limit.amount": "amount", "deadline": "due", "condition.stage": "stage"})
        agreed = ab.register(ORDER, [c], adapter=ad)
        held = agreed.commitments[0]
        held.carried_by = dict(reversed(list(held.carried_by.items())))
        held.condition = dict(reversed(list(held.condition.items())))
        held.limit = dict(reversed(list(held.limit.items())))
        held.limit["amount"] = {"unit": "USD", "max": 18400}
        self.assertTrue(ab.check("nothing declared", agreed))
        held.carried_by["deadline"] = "elsewhere"
        with self.assertRaises(ab.NoCommitment):
            ab.check("nothing declared", agreed)

    def test_a_record_read_back_from_a_jsonl_file_compares(self):
        with tempfile.TemporaryDirectory() as d:
            ad = ab.PlainAdapter(d, run_id="int-jsonl", sink=JsonlSink(d + "/ledger.jsonl"))
            c = ab.Commitment(act="régler la facture du fournisseur", obligor="ap-desk", obligee="fournisseur",
                              modality="MUST", limit={"amount": {"max": 18400, "unit": "EUR"}}, deadline="2026-12-01",
                              carried_by={"limit.amount": "amount"}, source=("contract.md"))
            agreed = ab.register(ORDER, [c], adapter=ad)
            self.run_untouched(ad, agreed)
            agreed.commitments[0].carried_by = {"limit.amount": "other_key"}
            with self.assertRaises(ab.NoCommitment):
                ab.check("nothing declared", agreed)


class CheckAndHoldRefuseAnUnregisteredRecord(unittest.TestCase):
    def hand_built(self, ad):
        agreed, _ = ab.build(ORDER, [payment()], run_id=ad.run_id, ts=ad.clock())
        agreed.adapter = ad
        return agreed

    def test_check_on_a_hand_built_record_raises_and_writes_nothing(self):
        ad = ab.memory_adapter(run_id="int-hand")
        agreed = self.hand_built(ad)
        with self.assertRaises(ab.NoCommitment) as cm:
            ab.check({"amount": 25000}, agreed)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn(agreed.id, str(cm.exception))
        self.assertEqual(ad.sink.read(), [])

    def test_check_with_the_adapter_passed_explicitly_raises_too(self):
        ad = ab.memory_adapter(run_id="int-hand")
        agreed, _ = ab.build(ORDER, [payment()], run_id=ad.run_id, ts=ad.clock())  # no .adapter set at all
        with self.assertRaises(ab.NoCommitment):
            ab.check({"amount": 25000}, agreed, adapter=ad)
        self.assertEqual(ad.sink.read(), [])

    def test_check_with_no_adapter_and_no_history_raises(self):
        ad = ab.memory_adapter(run_id="int-hand")
        agreed = self.hand_built(ad)
        with self.assertRaises(ab.NoCommitment):
            ab.check({"amount": 25000}, agreed, history=ab.NO_HISTORY)
        self.assertEqual(ad.sink.read(), [])

    def test_hold_on_a_hand_built_record_raises_and_writes_nothing(self):
        ad = ab.memory_adapter(run_id="int-hand")
        agreed = self.hand_built(ad)
        with self.assertRaises(ab.NoCommitment) as cm:
            ab.hold([], agreed)
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(ad.sink.read(), [])

    def test_hold_on_a_record_with_no_adapter_raises(self):
        agreed, _ = ab.build(ORDER, [payment()], run_id="int-none", ts="2026-11-01T00:00:00Z")
        with self.assertRaises(ab.NoCommitment):
            ab.hold([], agreed)

    def test_check_and_hold_refuse_a_record_whose_commitments_changed_in_memory(self):
        ad, agreed = registered(run_id="int-changed")
        verdicts = ab.check({"amount": 19800}, agreed)
        agreed.commitments[0].limit["amount"]["max"] = 999999
        before = list(ad.sink.read())
        for call in (lambda: ab.check({"amount": 19800}, agreed), lambda: ab.hold(verdicts, agreed)):
            with self.assertRaises(ab.NoCommitment) as cm:
                call()
            self.assertIn(agreed.commitments[0].commitment_id, str(cm.exception))
            self.assertIn("cannot be changed after registration", str(cm.exception))
        self.assertEqual(list(ad.sink.read()), before)

    def test_check_on_a_registered_record_still_returns_its_verdicts(self):
        ad, agreed = registered(run_id="int-ok")
        before = list(ad.sink.read())
        verdicts = ab.check({"amount": 19800}, agreed)
        widened = [v for v in verdicts if v.drift_class == "WIDENED"]
        self.assertEqual([v.field for v in widened], ["limit.amount"])
        self.assertEqual(list(ad.sink.read()), before, "check is a dry run")
        self.assertTrue(ab.check({"amount": 19800}, agreed, history=ab.NO_HISTORY))
        self.assertEqual([v.drift_class for v in ab.check({"amount": 18000}, agreed) if v.drift_class], ["PRESERVED"])

    def test_hold_on_a_registered_record_still_decides(self):
        ad, agreed = registered(run_id="int-ok")
        before = list(ad.sink.read())
        held = ab.hold(ab.check({"amount": 19800}, agreed), agreed)
        self.assertFalse(held.passed)
        self.assertTrue(ab.hold(ab.check({"amount": 18000}, agreed), agreed).passed)
        self.assertEqual(list(ad.sink.read()), before)


if __name__ == "__main__":
    unittest.main()
