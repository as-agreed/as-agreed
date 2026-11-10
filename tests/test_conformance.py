"""The conformance suite of SPEC §10 against the plain-Python adapter, under a guard that refuses every socket;
the demo under sixty seconds; the two examples through the SDK's folder run."""
import contextlib
import io
import socket
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from as_agreed.adapters.plain import memory_adapter  # noqa: E402
from as_agreed.conformance import SENTENCES, run_suite  # noqa: E402
from as_agreed.demo import run_demo  # noqa: E402
from as_agreed.importers.stages import run_folder  # noqa: E402

EXPECTED_SKIPS = {"A4", "A5", "A6", "A8"}


@contextlib.contextmanager
def no_sockets():
    """K13's guard: any socket constructed or connection attempted while the suite runs fails the suite.

    The constructor is patched, not the class: a standard-library import chain (xml.sax.saxutils, urllib, ssl)
    subclasses socket.socket at import time, and that must stay possible; opening one must not."""
    real_init, real_create, real_getaddrinfo = socket.socket.__init__, socket.create_connection, socket.getaddrinfo

    def refuse(*args, **kwargs):
        raise AssertionError("a socket was opened while the deterministic suite ran")

    socket.socket.__init__ = refuse
    socket.create_connection = refuse
    socket.getaddrinfo = refuse
    try:
        yield
    finally:
        socket.socket.__init__ = real_init
        socket.create_connection = real_create
        socket.getaddrinfo = real_getaddrinfo


class Conformance(unittest.TestCase):
    def test_every_shared_sentence_passes_on_the_plain_adapter(self):
        results = run_suite(memory_adapter, guard=no_sockets())
        failed = [r for r in results if r.status == "fail"]
        self.assertEqual(failed, [], "\n" + "\n".join(f"{r.id}: {r.message}" for r in failed))
        self.assertEqual({r.id for r in results if r.status == "skip"}, EXPECTED_SKIPS)
        self.assertEqual(sum(1 for r in results if r.status == "pass"), len(SENTENCES) - len(EXPECTED_SKIPS))
        k13 = next(r for r in results if r.id == "K13")
        self.assertIn("socket guard", k13.message)

    def test_the_demo_runs_both_ways_on_both_fixtures_under_sixty_seconds(self):
        started = time.time()
        for fixture, planted in (("widened", "WIDENED"), ("dropped", "DROPPED")):
            with redirect_stdout(io.StringIO()) as out:
                code_retry = run_demo(fixture, retry=True, out_dir=tempfile.mkdtemp())
                code_hold = run_demo(fixture, retry=False, out_dir=tempfile.mkdtemp())
            self.assertEqual((code_retry, code_hold), (0, 1), fixture)
            text = out.getvalue()
            self.assertIn("schema: PASS", text)
            self.assertIn(planted, text)
            self.assertIn("DANGER", text)
            self.assertIn("retry output scripted", text)
            self.assertIn("never called", text)
        self.assertLess(time.time() - started, 60)

    def test_every_claim_line_of_the_demo_names_what_it_compares(self):
        """A claim line and a commitment line can report on one sentence: the order's limit WIDENED while the
        sentence that states it is PRESERVED. Each claim line names what it compared, so the two do not read as one
        verdict on one thing."""
        for fixture in ("widened", "dropped"):
            with redirect_stdout(io.StringIO()) as out:
                run_demo(fixture, retry=False, out_dir=tempfile.mkdtemp())
            text = out.getvalue()
            self.assertIn("claim lines compare a sentence's status words, never its numbers", text)
            claims = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("claim [")]
            self.assertTrue(claims, fixture)
            for ln in claims:
                rest = ln.split("]", 1)[1].strip()
                self.assertTrue(rest.startswith(("status: ", "words only, no status")), ln)

    def test_the_examples_hold_through_the_folder_run(self):
        with redirect_stdout(io.StringIO()):
            widened = run_folder(str(ROOT / "examples" / "planted-widened"), out_dir=tempfile.mkdtemp())
            dropped = run_folder(str(ROOT / "examples" / "planted-failure"), out_dir=tempfile.mkdtemp())
            hello = run_folder(str(ROOT / "examples" / "hello"), out_dir=tempfile.mkdtemp())
        self.assertEqual(widened, 1)   # 19,800 where at most 18,400 was agreed
        self.assertEqual(dropped, 1)   # the phone check never reaches the payment note
        self.assertEqual(hello, 1)     # two hedges hardened into facts in the email


if __name__ == "__main__":
    unittest.main()
