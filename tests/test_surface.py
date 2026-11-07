"""`import as_agreed.sdk as ab` is the line every example teaches, so it is the surface and it does not move.

The package root is not the surface: `check`, `hold`, `retry` and `report` are module names in this package, so at
the root they resolve to modules. A half-working root surface, where `register` is the verb but `check` is a module,
would be worse than none, which is why this test pins both halves.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import as_agreed  # noqa: E402
import as_agreed.sdk as sdk  # noqa: E402

TAUGHT = ("register", "step", "run", "check", "hold", "retry", "report", "Commitment", "Criterion", "AsAgreed",
          "Tier2", "Tier3", "RecallRow", "fidelity_score", "DriftHold", "NoCommitment", "current_reason",
          "memory_adapter", "PlainAdapter", "LocalDestination", "RemoteDestination")


class TheSurface(unittest.TestCase):
    def test_every_name_the_documents_teach_is_exported(self):
        missing = [n for n in TAUGHT if n not in sdk.__all__ or not hasattr(sdk, n)]
        self.assertEqual(missing, [])

    def test_the_root_is_not_the_surface(self):
        # if someone adds re-exports to __init__.py, these four would silently shadow the modules or be shadowed
        for name in ("check", "hold", "retry", "report"):
            self.assertTrue(hasattr(getattr(as_agreed, name), "__file__"),
                            f"as_agreed.{name} is no longer the module; the surface is as_agreed.sdk")

    def test_a_whole_run_through_the_taught_import(self):
        import as_agreed.sdk as ab
        ad = ab.memory_adapter()
        agreed = ab.register("Pay the invoice as agreed.",
                             [ab.Commitment(act="pay the invoice", obligor="f", obligee="n", modality="MUST",
                                            commitment_id="c1", limit={"amount": {"max": 18400, "unit": "USD"}},
                                            carried_by={"limit.amount": "amount"})],
                             adapter=ad, source="o.md")
        calls = []

        @ab.step("commit", agreed, commit=lambda o, *, idempotency_key: calls.append(idempotency_key))
        def body(x, reason=None):
            return {"amount": 19800.0}

        with self.assertRaises(ab.DriftHold):
            body("in")
        self.assertEqual(calls, [], "a widened limit reached the commit callable")


if __name__ == "__main__":
    unittest.main()
