"""The site of a change (D-25, SPEC §1.4 and §1.5): tier 1 takes it from the type of the link where the field first
changed; an untyped link leaves it null and escalates it. K27 in the suite states the sentence; these are the edges."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import as_agreed.sdk as ab  # noqa: E402
from as_agreed.drift import LINK_TYPES, SITE_OF_LINK  # noqa: E402

ORDER = "Pay the October invoice from Northwind Freight, up to 18,400 dollars, only if it matches purchase order 4471.\n"
PAY = "pay the October invoice from Northwind Freight"


def registered():
    ad = ab.memory_adapter()
    agreed = ab.register(ORDER, [ab.Commitment(PAY, "finance-ops", "northwind-freight", "MUST", commitment_id="pay",
                                               limit={"amount": {"max": 18400, "unit": "USD"}},
                                               carried_by={"limit.amount": "amount"})], adapter=ad)
    return ad, agreed


def verdict(ad, link, field="limit.amount", unit="pay"):
    return next(r for r in ad.records() if r.get("kind") == "verdict" and r["link"] == link
                and r["unit"] == unit and r["field"] == field)


class Site(unittest.TestCase):
    def test_the_four_types_name_the_four_sites(self):
        self.assertEqual(LINK_TYPES, ("schema-write", "step", "compression", "delivery"))
        self.assertEqual([SITE_OF_LINK[t] for t in LINK_TYPES], ["design", "source", "seam", "sink"])

    def test_each_type_gives_its_site_where_the_change_is_first_seen(self):
        for lt, expected in SITE_OF_LINK.items():
            ad, agreed = registered()

            @ab.step("draft", agreed, link_type=lt)
            def draft(x):
                return {"amount": 19800.0}
            draft("go")
            v = verdict(ad, "draft#1@1")
            self.assertEqual((v["drift_class"], v["site"], v.get("site_undecided")), ("WIDENED", expected, None), lt)

    def test_an_untyped_first_change_stays_escalated_at_a_typed_copy(self):
        ad, agreed = registered()

        @ab.step("draft", agreed)
        def draft(x):
            return {"amount": 19800.0}

        @ab.step("send", agreed, link_type="delivery")
        def send(x):
            return {"amount": 19800.0}
        draft("go")
        send("go")
        for link in ("draft#1@1", "send#1@1"):
            v = verdict(ad, link)
            self.assertEqual((v["site"], v.get("site_undecided")), (None, "ESCALATED"), link)

    def test_a_field_restored_after_a_change_carries_no_site(self):
        ad, agreed = registered()

        @ab.step("draft", agreed, link_type="compression")
        def draft(x):
            return {"amount": 19800.0}

        @ab.step("fix", agreed, link_type="step")
        def fix(x):
            return {"amount": 18400.0}
        draft("go")
        fix("go")
        v = verdict(ad, "fix#1@1")
        self.assertEqual((v["drift_class"], v["site"], v.get("site_undecided")), ("PRESERVED", None, None))

    def test_the_dry_run_check_takes_a_link_type_and_refuses_an_unknown_one(self):
        ad, agreed = registered()
        got = [v for v in ab.check({"amount": 19800.0}, agreed, link_type="schema-write") if v.field == "limit.amount"]
        self.assertEqual((got[0].drift_class, got[0].site), ("WIDENED", "design"))
        with self.assertRaises(ValueError):
            ab.check({"amount": 19800.0}, agreed, link_type="handoff")
        with self.assertRaises(ValueError):
            ab.step("x", agreed, link_type="seam")  # a site is not a link type

    def test_an_unparented_record_takes_the_site_of_its_link(self):
        ad, agreed = registered()

        @ab.step("plan", agreed, link_type="step")
        def plan(x):
            return {"commitments": [{"commitment_id": "extra", "act": "pay the November invoice too",
                                     "obligor": "finance-ops", "obligee": "northwind-freight", "modality": "MUST"}]}
        plan("go")
        v = next(r for r in ad.records() if r.get("kind") == "verdict" and r["drift_class"] == "ADDED_UNPARENTED")
        self.assertEqual(v["site"], "source")

    def test_a_tier_above_tier_1_still_sets_its_own_site(self):
        cs = [ab.Commitment(PAY, "finance-ops", "northwind-freight", "MUST", commitment_id="pay",
                            condition="only if it matches purchase order 4471")]
        ad2 = ab.memory_adapter()
        agreed2 = ab.register(ORDER, cs, adapter=ad2)
        tier2 = ab.Tier2(lambda esc: {"drift_class": "RECONDITIONED", "confidence": 0.9, "reason": "r", "site": "seam"})

        @ab.step("draft", agreed2, tiers=[tier2])
        def draft(x):
            return {"commitments": [dict(agreed2.commitments[0].record(agreed2.id), parent_id="pay",
                                         condition="if it roughly matches an order")]}
        draft("go")
        v = next(r for r in ad2.records() if r.get("kind") == "verdict" and r["field"] == "condition" and r["tier"] == 2)
        self.assertEqual((v["drift_class"], v["site"]), ("RECONDITIONED", "seam"))


if __name__ == "__main__":
    unittest.main()
