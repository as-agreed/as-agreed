"""SPEC §2.6: the library is the client of the As-Agreed service, and the destination is the only thing that moves.

D1 the bytes the core hands a mocked remote destination over one run are the bytes the local file holds;
D2 a remote destination is handed the envelope (workspace, run id, the record's bytes, its verdict) and nothing else;
D3 the remote destination is an interface: it cannot be built without `send`, and the package ships no sink beyond four;
D4 the default destination is the local file store.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import as_agreed.sdk as ab  # noqa: E402
from as_agreed.adapters.base import encode  # noqa: E402
from as_agreed.destination import Envelope, LocalDestination, RemoteDestination  # noqa: E402


class MockRemote(RemoteDestination):
    """The one mock: keeps what it was handed, and reads it back so the core can see the run's history."""

    def __init__(self, workspace, received=None):
        super().__init__(workspace)
        self.received = received if received is not None else []

    def send(self, envelope):
        self.received.append(envelope)

    def read(self):
        import json
        return [json.loads(e.record.decode("utf-8")) for e in self.received]

    def reopen(self):
        return MockRemote(self.workspace, self.received)


class FixedClock(ab.PlainAdapter):
    def clock(self):
        return "2026-11-04T00:00:00Z"


def one_run(destination):
    """Register, one step that passes, one step that widens the agreed limit and is held."""
    ad = FixedClock(run_id="run-1", pipeline="p", destination=destination)
    agreed = ab.register("Pay the October invoice, up to 18,400 USD.",
                         [ab.Commitment(act="pay the invoice", obligor="finance", obligee="vendor", modality="MUST",
                                        commitment_id="c1", limit={"amount": {"max": 18400, "unit": "USD"}},
                                        carried_by={"limit.amount": "amount"})],
                         adapter=ad, source="order.md")

    @ab.step("ok", agreed)
    def ok(x, reason=None):
        return {"amount": 18000.0}

    @ab.step("commit", agreed, commit=lambda o, *, idempotency_key: None)
    def commit(x, reason=None):
        return {"amount": 19800.0}

    ok("in")
    with unittest.TestCase().assertRaises(ab.DriftHold):
        commit("in")
    return ad


class TheDestination(unittest.TestCase):
    def test_d1_local_and_mocked_remote_give_byte_identical_records(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.jsonl"
            one_run(LocalDestination(str(path)))
            local = path.read_bytes()
        remote = MockRemote("w-1")
        one_run(remote)
        self.assertTrue(local, "the local run wrote nothing")
        self.assertEqual(b"".join(e.record for e in remote.received), local)

    def test_d2_the_remote_is_handed_the_envelope_and_nothing_else(self):
        remote = MockRemote("w-1")
        ad = one_run(remote)
        self.assertTrue(remote.received)
        for e in remote.received:
            self.assertIs(type(e), Envelope)
            self.assertEqual(set(vars(e)), {"workspace", "run_id", "record", "verdict"})
            self.assertEqual(e.workspace, "w-1")
            self.assertEqual(e.run_id, "run-1")
        self.assertEqual([e.record for e in remote.received], [encode(r) for r in ad.records()])
        self.assertIn("hold", [e.verdict for e in remote.received])

    def test_d3_the_remote_is_an_interface_and_none_ships(self):
        with self.assertRaises(TypeError):
            RemoteDestination("w-1")  # send, read and reopen are abstract
        with self.assertRaises(ValueError):
            MockRemote("")
        import importlib
        from as_agreed.adapters.base import JsonlSink, MemorySink, Sink
        for path in sorted((ROOT / "src" / "as_agreed").rglob("*.py")):
            rel = path.relative_to(ROOT / "src").with_suffix("")
            parts = [p for p in rel.parts if p != "__init__"]
            importlib.import_module(".".join(parts))
        seen, todo = set(), [Sink]
        while todo:
            for sub in todo.pop().__subclasses__():
                if sub not in seen and sub.__module__.startswith("as_agreed"):
                    seen.add(sub)
                    todo.append(sub)
        self.assertEqual(seen, {JsonlSink, MemorySink, LocalDestination, RemoteDestination},
                         "a destination implementation beyond the four shipped sinks is in the package")

    def test_d4_the_default_is_the_local_file_store(self):
        with tempfile.TemporaryDirectory() as d:
            ad = ab.PlainAdapter(d)
            self.assertIsInstance(ad.sink, LocalDestination)
            self.assertEqual(Path(ad.sink.path), Path(d) / "ledger.jsonl")
        with self.assertRaises(ValueError):
            ab.PlainAdapter(sink=ab.memory_adapter().sink, destination=MockRemote("w"))


if __name__ == "__main__":
    unittest.main()
