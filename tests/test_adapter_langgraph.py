"""The conformance suite of SPEC §10 against the LangGraph adapter (§5.2), under the socket guard; A5 and A8 run a
compiled graph on an in-memory checkpointer. Skipped where langgraph is not installed; the import test always runs."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_conformance import no_sockets  # noqa: E402

try:
    import langgraph  # noqa: F401
    HAVE_LANGGRAPH = True
except ImportError:
    HAVE_LANGGRAPH = False

EXPECTED_SKIPS = {"A6", "I3", "I4", "I5"}


class LangGraphImport(unittest.TestCase):
    def test_the_module_imports_without_langgraph(self):
        from as_agreed.adapters import langgraph as lgmod
        self.assertEqual(lgmod.merge({"records": [1], "held": True}, {"records": [2], "held": False}),
                         {"records": [1, 2], "held": False})
        if not HAVE_LANGGRAPH:
            with self.assertRaises(ImportError) as cm:
                lgmod.LangGraphAdapter()
            self.assertIn("pip install langgraph", str(cm.exception))


@unittest.skipUnless(HAVE_LANGGRAPH, "langgraph is not installed: pip install langgraph")
class LangGraphConformance(unittest.TestCase):
    def test_every_shared_sentence_and_the_langgraph_sentences_pass(self):
        from as_agreed.adapters.langgraph import langgraph_adapter
        from as_agreed.conformance import SENTENCES, run_suite
        results = run_suite(langgraph_adapter, guard=no_sockets())
        failed = [r for r in results if r.status == "fail"]
        self.assertEqual(failed, [], "\n" + "\n".join(f"{r.id}: {r.message}" for r in failed))
        self.assertEqual({r.id for r in results if r.status == "skip"}, EXPECTED_SKIPS)
        self.assertEqual(sum(1 for r in results if r.status == "pass"), len(SENTENCES) - len(EXPECTED_SKIPS))
        by_id = {r.id: r for r in results}
        self.assertIn("socket guard", by_id["K13"].message)
        self.assertIn("Command", by_id["T5"].message)

    def test_the_command_line_finds_the_adapter_by_name(self):
        from as_agreed.conformance import load_factory
        ad = load_factory("langgraph")()
        self.assertEqual(ad.binding.name, "LangGraph")

    def test_a_reopened_checkpoint_sink_reads_the_checkpointer(self):
        from as_agreed.adapters.langgraph import CheckpointSink
        sink = CheckpointSink()
        sink.append({"kind": "link", "id": "a#1@1", "run_id": "r"})
        sink.append({"kind": "link", "id": "b#1@1", "run_id": "r"})
        again = sink.reopen()
        self.assertIsNot(again, sink)
        self.assertEqual([r["id"] for r in again.read()], ["a#1@1", "b#1@1"])


if __name__ == "__main__":
    unittest.main()
