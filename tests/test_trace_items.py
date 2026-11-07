"""The library items the trace bench needs (SPEC draft 8, 2026-11-05; trace-bench plan S1 to S5): an integer bound
beyond float range compared exactly (S2), a missing carried key read as not addressed (S1, K28 in the suite), the value
reader (S3), the batch form of a tier callable (S4) and a proxy record's provenance through the stage folder and the
importer's runner (S5). Draft 9 (2026-11-05, chunk 1 of the bench): a value in another unit converted or refused as
unit_mismatch (aegis-18 L09, K29 in the suite), and a proposal's proposed_class (t03 L07, K30)."""
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import as_agreed.sdk as ab  # noqa: E402
from as_agreed.adapters.base import rebuild  # noqa: E402
from as_agreed.commitment import MAX_INT_BITS, number  # noqa: E402
from as_agreed.importers.stages import Link, links_to_run, run_folder  # noqa: E402

ORDER = "Pay the October invoice from Northwind Freight, up to 18,400 dollars, only if it matches purchase order 4471.\n"
PAY = "pay the October invoice from Northwind Freight"
PROSE = "Paid Northwind Freight 19,800 dollars for the October invoice, against purchase order 4471."
# t08 of the trace bench (MAST-Data, AG2 olympiad row 0, 2026-11-04): the drafted record's range field, as the drafter
# wrote it; the library raised OverflowError on it and the bench dropped the field
T08_TASK = "Find the number of positive integers at most $2^{2017}$ whose base-two representation has a given property."
T08_FIELD = {"act": "the number of positive integers at most $2^{2017}$", "modality": "MUST",
             "limit": {"integer_min": {"min": 1}, "integer_max": {"max": 2 ** 2017}}}
DRAFTER = {"model": "claude-opus-5-5", "version": "drafter-v1", "date": "2026-11-04", "prompt_id": "a1b2c3d4"}


def pay(**over):
    data = dict(act=PAY, obligor="finance-ops", obligee="northwind-freight", modality="MUST", commitment_id="pay",
                limit={"amount": {"max": 18400, "unit": "USD"}}, carried_by={"limit.amount": "amount"},
                acceptance_criteria=[{"text": "the controller confirmed by phone", "verification": "machine_checkable",
                                      "field": "phone_confirmed", "criterion_id": "phone"}])
    data.update(over)
    return ab.Commitment(**data)


def registered(*cs, **kw):
    ad = ab.memory_adapter()
    return ad, ab.register(ORDER, list(cs) or [pay()], adapter=ad, source="order", **kw)


def labels(agreed, outputs, tier=1, **kw):
    return {v.field: v.label for v in ab.check(outputs, agreed, **kw) if v.unit_kind == "commitment" and v.tier == tier}


def t08():
    ad = ab.memory_adapter()
    c = ab.Commitment.from_mapping(dict(T08_FIELD, obligor="solver", obligee="asker", commitment_id="integer_range",
                                        carried_by={"limit.integer_max": "n"}))
    return ad, ab.register(T08_TASK, [c], adapter=ad, source="t08", proxy=True)


class S2ExactIntegers(unittest.TestCase):
    def test_number_keeps_an_integer_exact_and_refuses_what_it_cannot_hold(self):
        self.assertEqual(number(2 ** 2017), 2 ** 2017)
        self.assertIsInstance(number(2 ** 2017), int)
        self.assertEqual(number("18,400"), 18400)
        self.assertEqual(number(str(2 ** 2017)), 2 ** 2017)
        for no in (2 ** (MAX_INT_BITS + 1), "1" * 2000, float("inf"), float("nan"), True, "1e400"):
            self.assertIsNone(number(no), no if not isinstance(no, int) else "a huge int")

    def test_the_t08_bound_registers_and_is_decided_exactly(self):
        ad, agreed = t08()
        n = 2 ** 2017
        self.assertEqual(labels(agreed, {"n": n})["limit.integer_max"], "PRESERVED")
        self.assertEqual(labels(agreed, {"n": n - 1})["limit.integer_max"], "PRESERVED")
        widened = next(v for v in ab.check({"n": n + 1}, agreed) if v.field == "limit.integer_max")
        self.assertEqual(widened.drift_class, "WIDENED")
        self.assertIn("(608 digits)", widened.reason)
        self.assertNotEqual(*widened.reason.split(" where at most "), "two neighbours shown as one number")
        self.assertEqual(labels(agreed, {"n": str(n + 1)})["limit.integer_max"], "WIDENED")
        restated = {"commitments": [dict(T08_FIELD, obligor="solver", obligee="asker", parent_id="integer_range",
                                         limit={"integer_min": {"min": 1}, "integer_max": {"max": 2 ** 2018}})]}
        restated["n"] = n  # the payload beside the record is read too, and it is inside the bound
        got = sorted(v.label for v in ab.check(restated, agreed) if v.field == "limit.integer_max")
        self.assertEqual(got, ["PRESERVED", "WIDENED"])

    def test_beyond_the_exact_range_a_bound_is_refused_by_name_and_a_value_escalates(self):
        with self.assertRaises(ValueError) as e:
            pay(limit={"amount": {"max": 2 ** 5000}})
        self.assertIn("5001 bits", str(e.exception))
        ad, agreed = t08()
        got = next(v for v in ab.check({"n": 2 ** 5000}, agreed) if v.field == "limit.integer_max")
        self.assertEqual(got.undecided, "ESCALATED")
        self.assertIn("5001 bits", got.reason)

    def test_a_huge_value_at_a_commit_holds_and_writes_no_traceback(self):
        ad, agreed = t08()

        @ab.step("answer", agreed, commit=True)
        def answer(x):
            return {"n": 2 ** 5000}

        with self.assertRaises(ab.DriftHold) as held:
            answer(T08_TASK)
        self.assertTrue(any("ESCALATED" in ln for ln in held.exception.lines), held.exception.lines)
        json.dumps(ad.records())  # every record still serialises

    def test_set_members_and_criteria_compare_integers_exactly(self):
        ad, agreed = registered(pay(limit={"account": {"in": [2 ** 60]}}, carried_by={"limit.account": "account"}))
        self.assertEqual(labels(agreed, {"account": 2 ** 60})["limit.account"], "PRESERVED")
        self.assertEqual(labels(agreed, {"account": 2 ** 60 + 1})["limit.account"], "WIDENED")
        self.assertEqual(labels(agreed, {"phone_confirmed": 2 ** 2017})["acceptance_criteria.phone"], "ESCALATED")

    def test_a_huge_confidence_is_a_tier_failure_not_a_crash(self):
        ad, agreed = registered()
        tier = ab.Tier2(lambda e: {"drift_class": "RESTATED", "confidence": 2 ** 2017, "reason": "r"})

        @ab.step("brief", agreed, tiers=[tier])
        def brief(x):
            return PROSE

        brief(ORDER)
        failures = [r for r in ad.records() if r.get("kind") == "tier_failure"]
        self.assertEqual(len(failures), 1)
        self.assertIn("confidence", failures[0]["error"])


class S1MissingKey(unittest.TestCase):
    def test_the_default_is_unchanged_a_missing_key_is_dropped(self):
        ad, agreed = registered()
        got = labels(agreed, {"po": "4471"})
        self.assertEqual((got["limit.amount"], got["acceptance_criteria.phone"]), ("DROPPED", "DROPPED"))

    def test_under_undeclared_a_missing_key_is_not_addressed_and_a_null_is_dropped(self):
        ad, agreed = registered()
        vs = [v for v in ab.check({"po": "4471"}, agreed, on_missing_key="undeclared") if v.unit_kind == "commitment"]
        amount = next(v for v in vs if v.field == "limit.amount")
        self.assertEqual((amount.undecided, amount.missing_key), ("UNDECLARED", "amount"))
        self.assertEqual(next(v for v in vs if v.field == "acceptance_criteria.phone").undecided, "UNDECLARED")
        for empty in (None, "", [], {}):
            self.assertEqual(labels(agreed, {"amount": empty}, on_missing_key="undeclared")["limit.amount"], "DROPPED")
        self.assertEqual(labels(agreed, {"amount": 19800}, on_missing_key="undeclared")["limit.amount"], "WIDENED")
        self.assertEqual(set(labels(agreed, PROSE, on_missing_key="undeclared").values()), {"UNDECLARED"})

    def test_a_record_still_drops_what_it_leaves_out(self):
        ad, agreed = registered()
        rec = {"parent_id": "pay", "act": PAY, "obligor": "finance-ops", "modality": "MUST",
               "limit": {"amount": {"max": 18400, "unit": "USD"}}}
        got = labels(agreed, {"commitments": [rec], "amount": 18400}, on_missing_key="undeclared")
        self.assertEqual(got["obligee"], "DROPPED")

    def test_at_a_commit_the_missing_key_of_a_binding_commitment_holds_unless_the_policy_records(self):
        ad, agreed = registered()
        calls = []

        @ab.step("commit", agreed, commit=lambda o, *, idempotency_key: calls.append(1), on_missing_key="undeclared")
        def commit(x):
            return {"po": "4471", "phone_confirmed": True}

        with self.assertRaises(ab.DriftHold) as held:
            commit(ORDER)
        self.assertEqual(calls, [])
        self.assertTrue(any("limit.amount UNDECLARED" in ln and "amount is not in this link" in ln
                            for ln in held.exception.lines), held.exception.lines)
        ad2, agreed2 = registered()

        @ab.step("commit", agreed2, commit=True, on_missing_key="undeclared", on_undecided="record")
        def lenient(x):
            return {"po": "4471", "phone_confirmed": True}

        lenient(ORDER)
        self.assertTrue([h for h in ad2.records() if h.get("kind") == "hold" and h["passed"]])

    def test_a_faithful_record_does_not_cover_a_missing_payload_key(self):
        ad, agreed = registered()
        copied = [r for r in ad.records() if r.get("kind") == "commitment"]

        @ab.step("commit", agreed, commit=True, on_missing_key="undeclared")
        def commit(x):
            return {"commitments": copied, "total": 19800.0, "phone_confirmed": True}

        with self.assertRaises(ab.DriftHold):
            commit(ORDER)

    def test_an_unknown_mode_is_refused(self):
        ad, agreed = registered()
        with self.assertRaises(ValueError):
            ab.step("x", agreed, on_missing_key="ignore")
        with self.assertRaises(ValueError):
            ab.check({}, agreed, on_missing_key="ignore")


def reader(answers, calls=None):
    """A value reader that answers from a table: field -> answer."""
    def fn(e):
        if calls is not None:
            calls.append(e.field)
        return answers.get(e.field)
    return fn


READ = {"limit.amount": {"value": 19800, "confidence": 0.9, "quote": "19,800 dollars"}}


class S3ValueReader(unittest.TestCase):
    def test_a_confident_read_is_classed_by_tier_1s_procedure_and_holds_the_commit(self):
        ad, agreed = registered()

        @ab.step("commit", agreed, commit=True, tiers=[ab.ValueReader(reader(READ))], on_undecided="record")
        def commit(x):
            return PROSE

        with self.assertRaises(ab.DriftHold) as held:
            commit(ORDER)
        v = next(r for r in ad.records() if r.get("kind") == "verdict" and r.get("tier") == 2)
        self.assertEqual((v["drift_class"], v["field"], v["confidence"]), ("WIDENED", "limit.amount", 0.9))
        self.assertEqual(v["read"], {"value": 19800, "quote": "19,800 dollars"})
        self.assertIn("19800 where at most 18400 USD was agreed", v["reason"])
        self.assertTrue(any("WIDENED (tier 2)" in ln for ln in held.exception.lines), held.exception.lines)
        tier1 = next(r for r in ad.records() if r.get("kind") == "verdict" and r.get("tier") == 1
                     and r["field"] == "limit.amount")
        self.assertEqual(tier1["undecided"], "UNDECLARED", "the reader erased tier 1's verdict")

    def test_the_class_follows_the_value_and_a_null_value_is_dropped(self):
        ad, agreed = registered()
        for value, quote, want in ((18400, "18,400 dollars", "PRESERVED"), (17000, "Paid", "PRESERVED"),
                                   (None, "Paid", "DROPPED")):
            fn = reader({"limit.amount": {"value": value, "confidence": 0.95, "quote": quote}})
            got = labels(agreed, "Paid 18,400 dollars.", tier=2, tiers=[ab.ValueReader(fn)])
            self.assertEqual(got["limit.amount"], want, value)

    def test_below_the_threshold_or_with_a_quote_not_in_the_link_a_read_is_a_proposal_for_tier_3(self):
        ad, agreed = registered()
        person = []
        t3 = ab.Tier3(lambda e: person.append(e.field))
        for answer, why in (({"value": 19800, "confidence": 0.6, "quote": "19,800 dollars"}, "below the threshold"),
                            ({"value": 19800, "confidence": 0.99, "quote": "20,000 dollars"}, "quote is not in")):
            vs = ab.check(PROSE, agreed, tiers=[ab.ValueReader(reader({"limit.amount": answer})), t3])
            v = next(x for x in vs if x.tier == 2)
            self.assertEqual((v.drift_class, v.undecided), (None, "ESCALATED"))
            self.assertIn("proposed WIDENED", v.reason)
            self.assertIn(why, v.reason)
        self.assertEqual(person.count("limit.amount"), 2, "a proposal did not go on to tier 3")

    def test_the_reader_never_asserts_a_class(self):
        ad, agreed = registered()
        for bad in ({"value": 1, "confidence": 0.9, "quote": "Paid", "drift_class": "PRESERVED"},
                    {"value": 1, "confidence": 0.9}, {"value": 1, "quote": "Paid"},
                    {"value": 1, "confidence": 1.5, "quote": "Paid"}, {"value": 1, "confidence": 0.9, "quote": ""}):
            ad, agreed = registered()

            @ab.step("brief", agreed, tiers=[ab.ValueReader(reader({"limit.amount": bad}))])
            def brief(x):
                return PROSE

            brief(ORDER)
            self.assertEqual(len([r for r in ad.records() if r.get("kind") == "tier_failure"]), 1, bad)
            self.assertFalse([r for r in ad.records() if r.get("kind") == "verdict" and r.get("tier") == 2], bad)

    def test_no_answer_leaves_the_field_and_purpose_is_never_read(self):
        ad, agreed = registered(pay(purpose="settle the account"))
        asked = []
        vs = ab.check({"commitments": [{"parent_id": "pay", "act": PAY, "obligor": "finance-ops",
                                        "obligee": "northwind-freight", "modality": "MUST", "purpose": "other",
                                        "limit": {"amount": {"max": 18400, "unit": "USD"}}}]},
                      agreed, tiers=[ab.ValueReader(reader({}, asked))])
        self.assertNotIn("purpose", asked)
        self.assertFalse([v for v in vs if v.tier == 2])

    def test_a_read_after_a_loss_carries_restored_equivalent_not_verbatim(self):
        ad, agreed = registered()
        fn = reader({"limit.amount": {"value": 18400, "confidence": 0.9, "quote": "18,400 dollars"}})
        v = next(x for x in ab.check("Paid 18,400 dollars.", agreed, tiers=[ab.ValueReader(fn)],
                                     history={"pay/limit.amount": "DROPPED"}) if x.tier == 2)
        self.assertEqual((v.drift_class, v.trajectory), ("PRESERVED", "restored_equivalent"))

    def test_a_threshold_off_the_unit_interval_is_refused(self):
        for bad in (1.5, -0.1, True, "0.8"):
            with self.assertRaises(ValueError):
                ab.ValueReader(lambda e: None, threshold=bad)
            with self.assertRaises(ValueError):
                ab.Tier2(lambda e: None, threshold=bad)


def three():
    return [pay(commitment_id=f"pay{i}", act=f"{PAY}, part {i}") for i in range(3)]


class S4Batch(unittest.TestCase):
    def test_one_call_per_link_answers_every_escalation_in_order(self):
        calls = []

        def batch(escs):
            calls.append(len(escs))
            return [{"value": 19800, "confidence": 0.9, "quote": "19,800 dollars"} if e.field == "limit.amount"
                    else None for e in escs]

        ad, agreed = registered(*three())
        one_by_one = []
        ab.check(PROSE, agreed, tiers=[ab.ValueReader(reader(READ, one_by_one))])
        vs = ab.check(PROSE, agreed, tiers=[ab.ValueReader(batch, batch=True)])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], len(one_by_one))
        self.assertGreater(len(one_by_one), 3)
        widened = sorted(v.unit for v in vs if v.tier == 2 and v.drift_class == "WIDENED")
        self.assertEqual(widened, ["pay0", "pay1", "pay2"])

    def test_tier2_and_tier3_take_the_batch_form_too(self):
        ad, agreed = registered()
        t2 = ab.Tier2(lambda escs: [{"drift_class": "RESTATED", "confidence": 0.9, "reason": "same"}
                                    if e.field == "act" else None for e in escs], batch=True)
        t3 = ab.Tier3(lambda escs: [None for _ in escs], batch=True)
        vs = ab.check({"commitments": [{"parent_id": "pay", "act": "settle the October bill from Northwind",
                                        "obligor": "finance-ops", "obligee": "northwind-freight", "modality": "MUST",
                                        "limit": {"amount": {"max": 18400, "unit": "USD"}}}], "amount": 18400,
                       "phone_confirmed": True}, agreed, tiers=[t2, t3])
        self.assertEqual([(v.field, v.drift_class) for v in vs if v.tier == 2], [("act", "RESTATED")])

    def test_a_batch_answer_of_the_wrong_shape_is_a_tier_failure(self):
        for bad in (lambda escs: escs[:-1] and [None], lambda escs: {"a": 1}, lambda escs: None):
            ad, agreed = registered()

            @ab.step("brief", agreed, tiers=[ab.ValueReader(bad, batch=True)])
            def brief(x):
                return PROSE

            brief(ORDER)
            failures = [r for r in ad.records() if r.get("kind") == "tier_failure"]
            self.assertEqual(len(failures), 1)

    def test_a_link_with_nothing_undecided_makes_no_call(self):
        ad, agreed = registered(ab.Commitment(PAY, "finance-ops", "northwind-freight", "MUST", commitment_id="p",
                                              carried_by={"obligor": "who", "obligee": "to", "act": "act",
                                                          "modality": "m"}))
        calls = []
        ab.check({"who": "finance-ops", "to": "northwind-freight", "act": PAY, "m": "MUST"}, agreed,
                 tiers=[ab.ValueReader(lambda escs: calls.append(escs) or [], batch=True)])
        self.assertEqual(calls, [])


def folder_with(folder, files):
    for name, content in files.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))


PAY_RECORD = {"act": PAY, "obligor": "finance-ops", "obligee": "northwind-freight", "modality": "MUST",
              "commitment_id": "pay", "limit": {"amount": {"max": 18400, "unit": "USD"}},
              "carried_by": {"limit.amount": "amount"}}


class S5Provenance(unittest.TestCase):
    def run_folder(self, declared, **kw):
        with tempfile.TemporaryDirectory() as folder:
            folder_with(folder, {"01-order.md": ORDER, "02-research.md": "Looked it up.\n",
                                 "03-commit.md": "Paying.\n", "03-commit.json": {"amount": 18400},
                                 "commitments.json": declared})
            out = tempfile.mkdtemp()
            with redirect_stdout(StringIO()) as printed, redirect_stderr(StringIO()):
                code = run_folder(folder, out_dir=out, **kw)
            with open(os.path.join(out, "ledger.jsonl"), encoding="utf-8") as f:
                rows = [json.loads(ln) for ln in f]
            with open(os.path.join(out, "counts.csv"), encoding="utf-8") as f:
                csv = f.read()
            return code, rows, printed.getvalue(), csv

    def test_the_stage_folder_carries_proxy_and_drafter_into_the_record_and_the_report(self):
        code, rows, text, csv = self.run_folder({"proxy": True, "drafter": DRAFTER, "commitments": [PAY_RECORD]})
        head = next(r for r in rows if r["kind"] == "as_agreed")
        self.assertEqual((code, head["proxy"], head["drafter"]), (0, True, DRAFTER))
        self.assertIn("as-agreed: proxy (task as stated; no order was recorded); commitments drafted by "
                      "claude-opus-5-5 drafter-v1 on 2026-11-04, prompt a1b2c3d4", text)
        self.assertIn(",yes,", csv.splitlines()[1])

    def test_the_bare_list_still_reads_and_carries_no_drafter(self):
        code, rows, text, csv = self.run_folder([PAY_RECORD])
        head = next(r for r in rows if r["kind"] == "as_agreed")
        self.assertEqual((code, head["proxy"], "drafter" in head), (0, False, False))
        self.assertNotIn("proxy", text)

    def test_a_malformed_provenance_is_refused_by_name(self):
        for declared, word in (({"proxy": False, "drafter": DRAFTER, "commitments": [PAY_RECORD]}, "proxy"),
                               ({"proxy": True, "drafter": dict(DRAFTER, date="soon"), "commitments": []}, "date"),
                               ({"proxy": True, "drafter": {"model": "m"}, "commitments": []}, "missing"),
                               ({"proxy": "yes", "commitments": []}, "proxy"),
                               ({"proxy": True, "tag": "[C]", "commitments": []}, "tag")):
            with self.assertRaises(ValueError) as e:
                self.run_folder(declared)
            self.assertIn(word, str(e.exception))

    def test_the_importers_runner_takes_per_link_outputs_a_drafter_and_the_step_options(self):
        out = tempfile.mkdtemp()
        ad = ab.memory_adapter()
        rep = links_to_run([Link("research", "Looked it up."),
                            Link("summary", "amount 19,800", outputs={"amount": 19800}),
                            Link("commit", "Done.", outputs={"po": "4471"})],
                           root_input=ORDER, commitments=[PAY_RECORD], adapter=ad, out_dir=out, drafter=DRAFTER,
                           on_missing_key="undeclared", on_undecided="record")
        head = next(r for r in ad.records() if r["kind"] == "as_agreed")
        self.assertEqual((head["proxy"], head["drafter"]), (True, DRAFTER))
        got = {r["link"]: r.get("drift_class") or r.get("undecided") for r in ad.records()
               if r.get("kind") == "verdict" and r.get("field") == "limit.amount" and r.get("tier") == 1}
        self.assertEqual(got, {"research#1@1": "UNDECLARED", "summary#1@1": "WIDENED", "commit#1@1": "UNDECLARED"})
        self.assertEqual(rep.drafter, DRAFTER)
        self.assertEqual(rebuild(ad.records(), ad.run_id).drafter, DRAFTER)

    def test_a_drafter_on_a_record_that_is_no_proxy_is_refused_at_register(self):
        with self.assertRaises(ValueError):
            ab.register(ORDER, [PAY_RECORD], adapter=ab.memory_adapter(), drafter=DRAFTER)


# aegis-18 of the trace bench (chunk 1, 2026-11-05): the drafted limits in kg, and the L09 reads in grams and pounds
AEGIS18 = {"first_shipment": {"in": [1000], "unit": "kg"}, "sold_saturday": {"in": [300], "unit": "kg"},
           "rotted_sunday": {"in": [200], "unit": "kg"}}
L09 = "The first shipment was 1000 grams, not 1200 grams. Marta sold **300 pounds** of tomatoes. 200 kilos rotted."


def tomatoes():
    return ab.Commitment(act="report the tomatoes", obligor="solver", obligee="asker", modality="MUST",
                         commitment_id="tomatoes", limit=dict(AEGIS18))


class Draft9Units(unittest.TestCase):
    def test_the_aegis_18_reads_are_converted_and_classed(self):
        ad, agreed = registered(tomatoes())
        fn = reader({"limit.first_shipment": {"value": {"in": ["1000"], "unit": "grams"}, "confidence": 0.9,
                                              "quote": "The first shipment was 1000 grams"},
                     "limit.sold_saturday": {"value": {"in": ["300"], "unit": "pounds"}, "confidence": 0.9,
                                             "quote": "Marta sold **300 pounds** of tomatoes"},
                     "limit.rotted_sunday": {"value": {"in": [200], "unit": "kilos"}, "confidence": 0.9,
                                             "quote": "200 kilos rotted"}})
        got = {v.field: v for v in ab.check(L09, agreed, tiers=[ab.ValueReader(fn)]) if v.tier == 2}
        first, sold, rotted = got["limit.first_shipment"], got["limit.sold_saturday"], got["limit.rotted_sunday"]
        self.assertEqual((first.drift_class, first.unit_agreed, first.unit_read), ("WIDENED", "kg", "grams"))
        self.assertIn("one of 1000 grams converted to one of 1 kg", first.reason)
        self.assertEqual(sold.drift_class, "WIDENED")
        self.assertIn("136.077711", sold.reason, "300 lb is exactly 136.077711 kg")
        self.assertEqual((rotted.drift_class, rotted.unit_converted), ("PRESERVED", True))
        self.assertTrue(all(v.record()["unit_converted"] is True for v in got.values()))

    def test_the_table_is_exact_and_refuses_what_it_does_not_hold(self):
        from as_agreed.drift import UNITS, limit_entry
        self.assertEqual(UNITS["lb"][2] * 100000000, 45359237)
        self.assertNotIn("ton", UNITS)
        self.assertNotIn("month", UNITS)
        self.assertNotIn("usd", UNITS)
        same = limit_entry("w", {"max": 0.3, "unit": "kg"}, {"max": 300, "unit": "g"})
        self.assertEqual(same.drift_class, "PRESERVED", "300 g is 0.3 kg exactly")
        self.assertEqual(limit_entry("w", {"max": 1, "unit": "mi"}, {"max": 1609.344, "unit": "m"}).drift_class,
                         "PRESERVED")
        self.assertEqual(limit_entry("w", {"max": 2, "unit": "h"}, {"max": 7201, "unit": "s"}).drift_class, "WIDENED")
        for value in ({"in": ["SKU-1"], "unit": "g"}, {"max": 5, "unit": "furlongs"}, {"max": 5, "unit": "ton"}):
            agreed = {"in": [1], "unit": "kg"} if "in" in value else {"max": 5, "unit": "kg"}
            f = limit_entry("w", agreed, value)
            self.assertEqual((f.drift_class, f.undecided, f.refusal), (None, "ESCALATED", "unit_mismatch"), value)

    def test_a_refused_unit_holds_a_binding_commit_under_on_undecided_hold(self):
        ad, agreed = registered(pay(carried_by={"limit.amount": "amount"}))

        @ab.step("commit", agreed, commit=True, schema=None)
        def commit(x):
            return {"amount": {"max": 18400, "unit": "EUR"}, "phone_confirmed": True}

        with self.assertRaises(ab.DriftHold) as held:
            commit(ORDER)
        self.assertTrue(any("limit.amount ESCALATED" in ln and "unit_mismatch" in ln for ln in held.exception.lines),
                        held.exception.lines)


class Draft9Proposals(unittest.TestCase):
    def test_the_batch_form_carries_proposed_class_per_field(self):
        ad, agreed = registered(*three())
        batch = lambda escs: [{"value": 19800, "confidence": 0.5, "quote": "19,800 dollars"}
                              if e.field == "limit.amount" else None for e in escs]
        vs = [v for v in ab.check(PROSE, agreed, tiers=[ab.ValueReader(batch, batch=True)]) if v.tier == 2]
        self.assertEqual(sorted((v.unit, v.proposed_class) for v in vs),
                         [("pay0", "WIDENED"), ("pay1", "WIDENED"), ("pay2", "WIDENED")])

    def test_two_readers_compare_by_proposed_class_without_a_threshold_0_run(self):
        ad, agreed = registered()
        classes = []
        for value in (19800, 18400):
            fn = reader({"limit.amount": {"value": value, "confidence": 0.6, "quote": "19,800 dollars"}})
            v = next(x for x in ab.check(PROSE, agreed, tiers=[ab.ValueReader(fn)]) if x.tier == 2)
            zero = next(x for x in ab.check(PROSE, agreed, tiers=[ab.ValueReader(fn, threshold=0)]) if x.tier == 2)
            self.assertEqual(v.proposed_class, zero.drift_class, "the proposal is not what threshold 0 decides")
            classes.append(v.proposed_class)
        self.assertEqual(classes, ["WIDENED", "PRESERVED"])

    def test_a_report_without_proposals_prints_no_proposal_line(self):
        ad, agreed = registered()

        @ab.step("brief", agreed, tiers=[ab.ValueReader(reader(READ))])
        def brief(x):
            return PROSE

        brief(ORDER)
        rep = ab.report(ad, out_dir=tempfile.mkdtemp(), quiet=True)
        self.assertFalse([ln for ln in rep.count_lines if "proposed" in ln], rep.count_lines)
        self.assertFalse([ln for ln in rep.unit_lines if "proposed_class" in ln], rep.unit_lines)


if __name__ == "__main__":
    unittest.main()
