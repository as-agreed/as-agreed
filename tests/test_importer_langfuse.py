"""The Langfuse importer (SPEC §8.2, shipped in 0.0.2): `as-agreed import langfuse <export>... <out_folder>`.

Every fixture in tests/fixtures/langfuse/ is our own invented text, written for these tests; no text of Langfuse's own
fixtures is copied here. Both key spellings give the same links; a hand-off is found where the declared agent changes
and nowhere else; an agent comes only from a declared attribute, never from a name; `delivered` says whether the run
ends with an output; the stage folder is what `reconcile` reads, with the order as agreed (`--commitment`) or the task
as stated marked `proxy: true`; a missing field is named, never written as empty links."""
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

from as_agreed.__main__ import main  # noqa: E402
from as_agreed.importers import langfuse as L  # noqa: E402
from as_agreed.importers.stages import run_folder, stage_files  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "langfuse"
API, BLOB = str(FIX / "handoff.api.json"), str(FIX / "handoff.blob.jsonl")
ONE, NAMES = str(FIX / "one-agent.blob.jsonl"), str(FIX / "names-only.blob.jsonl")
ORDER = str(FIX / "refund-order" / "order.md")


def read(path):
    traces, _ = L.read_exports([path])
    assert len(traces) == 1, path
    return L.read_trace(next(iter(traces.values())), L.Scrub(True))


def cli(*args):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["import", "langfuse", *args])
    return code, out.getvalue(), err.getvalue()


def page(tmp, name, rows):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"data": rows, "meta": {}}, f)
    return path


class Reading(unittest.TestCase):
    def test_both_spellings_give_the_same_links(self):
        ma, la = read(API)
        mb, lb = read(BLOB)
        strip = lambda ls: [{k: v for k, v in l.items() if k != "line"} for l in ls]  # the two time spellings differ
        self.assertEqual(strip(la), strip(lb))
        self.assertEqual(ma["handoffs"], mb["handoffs"])
        self.assertEqual(L.load_file(API)[1], "camelCase")
        self.assertEqual(L.load_file(BLOB)[1], "snake_case")

    def test_the_handoff_is_found_where_the_declared_agent_changes(self):
        m, links = read(API)
        self.assertEqual(m["agents"], ["refunds", "triage"])
        self.assertEqual(m["handoffs"], [{"at_link": 3, "from_agent": "triage", "to_agent": "refunds",
                                          "via": "handoff_to_refunds", "to_observation_id": "refunds"}])
        self.assertEqual(links[2]["to"], "refunds")
        self.assertEqual([l["agent"] for l in links[1:]], ["triage", "triage", "refunds", "refunds", None])

    def test_links_carry_text_and_parents(self):
        m, links = read(API)
        self.assertEqual(links[0]["text"], "Refund order 5512 to the original card, at most 240 euros, before 1 March 2030.")
        self.assertEqual(m["task_from"], "root input")
        self.assertEqual([l["parent"] for l in links[1:]], [0, 1, 0, 0, 4])  # the root's output reads the last link
        self.assertEqual(links[2]["text"], "order: 5512\namount: 240\ncurrency: EUR\nnote: Refund to the original card.\n"
                                           "note: Before 1 March 2030.")
        self.assertEqual(links[3]["outputs"], {"amount": 260, "currency": "EUR", "card": "original"})
        self.assertNotIn("outputs", links[4])  # a string output declares nothing
        self.assertEqual(links[-1]["kind"], "final")

    def test_no_handoff_where_nested_runs_share_one_agent(self):
        m, links = read(ONE)
        self.assertEqual(m["agents"], ["k9"])
        self.assertEqual(m["handoffs"], [])
        self.assertTrue(all("to" not in l for l in links))

    def test_an_agent_comes_only_from_a_declared_attribute(self):
        m, links = read(NAMES)  # names that read like agents, an unknown key, graph.node.id on a span that is not an agent
        self.assertEqual(m["agents"], [])
        self.assertEqual(m["agents_declared_in_scopes"], [])
        self.assertEqual(m["handoffs"], [])
        self.assertEqual([l["agent"] for l in links[1:]], [None, None])
        m, links = read(ONE)  # graph.node.id "planner" on a plain span is not an agent: the link keeps k9
        self.assertEqual(links[1]["agent"], "k9")
        self.assertEqual(links[1]["agent_declared_by"], "gen_ai.agent.id")
        self.assertNotIn("planner", m["agents_declared_in_scopes"])

    def test_delivered(self):
        self.assertTrue(read(API)[0]["delivered"])  # the root has an output
        self.assertTrue(read(NAMES)[0]["delivered"])  # the last leaf has an output
        m, _ = read(ONE)  # the last call ends with no output: the run stops before a delivery
        self.assertFalse(m["delivered"])
        self.assertEqual(m["final_observation"], "llm.call")

    def test_an_empty_root_input_gives_the_first_user_message(self):
        m, links = read(ONE)
        self.assertEqual(links[0]["text"], "Count the red cups on shelf 3.")
        self.assertEqual(m["task_from"], "first user message of observation 'llm.call' (root input empty)")

    def test_a_dotted_key_is_kept_as_json_and_declares_nothing(self):
        m, links = read(ONE)
        tool = next(l for l in links if l["phase"] == "scan_shelf")
        self.assertNotIn("outputs", tool)
        self.assertEqual(json.loads(tool["text"]), {"cups": ["red", "green"], "cam.raw": "x"})
        self.assertEqual(m["dotted_keys_kept_as_json"], 1)

    def test_times_with_any_fraction_sort_the_same_on_every_python(self):
        self.assertEqual(L.when("2030-02-01T10:00:07.1234567Z"), "2030-02-01T10:00:07.123456")
        self.assertEqual(L.when("2030-02-01 10:00:07.5"), "2030-02-01T10:00:07.500000")

    def test_scrub(self):
        s = L.Scrub(True)
        home = os.path.join(os.sep, "Users", "alice", "x")  # built here, so the file itself holds no home path
        self.assertEqual(s(f"see {home} and bob@example.org, key sk-lf-0123456789abcdef"),
                         f"see {os.path.join(os.sep, 'Users', '<user>', 'x')} and <email>, key <secret>")
        self.assertEqual(s.counts, {"home_path": 1, "email": 1, "secret": 1})


class Command(unittest.TestCase):
    def test_import_writes_the_stage_folder_with_a_proxy_record(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(API, t)
            self.assertEqual(code, 0, err)
            d = os.path.join(t, "handoff-c0ffee00")
            self.assertEqual(out, f"{d}\t5 links\tagents 2\thand-offs triage -> refunds at link 3\tdelivered true\n")
            self.assertEqual(sorted(os.listdir(d)), [
                "01-order.md", "02-triage-triage-agent.json", "02-triage-triage-agent.md",
                "03-triage-classify-request.json", "03-triage-classify-request.md",
                "04-refunds-issue_refund.json", "04-refunds-issue_refund.md", "05-refunds-confirm.md",
                "06-no-agent-refund-desk.json", "06-no-agent-refund-desk.md", "commitments.json", "trace-meta.json"])
            with open(os.path.join(d, "commitments.json")) as f:
                self.assertEqual(json.load(f), {"commitments": [], "proxy": True})
            with open(os.path.join(d, "trace-meta.json")) as f:
                meta = json.load(f)
            self.assertTrue(meta["proxy"])
            self.assertEqual(meta["order_from"], "root input")
            self.assertEqual(len(stage_files(d)), 6)
            out2, err2 = StringIO(), StringIO()
            with redirect_stdout(out2), redirect_stderr(err2):
                code = run_folder(d)
            self.assertEqual(code, 2)  # nothing was compared against a commitment, and reconcile says so
            self.assertIn("as-agreed: proxy (task as stated; no order was recorded)", out2.getvalue())
            self.assertIn("no commitment was declared", err2.getvalue())

    def test_commitment_passes_the_order_and_its_commitments_through(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(BLOB, t, "--commitment", ORDER, "--prefix", "refund")
            self.assertEqual(code, 0, err)
            d = os.path.join(t, "refund-c0ffee00")
            with open(ORDER) as a, open(os.path.join(d, "01-order.md")) as b:
                self.assertEqual(a.read(), b.read())
            with open(FIX / "refund-order" / "commitments.json") as a, open(os.path.join(d, "commitments.json")) as b:
                self.assertEqual(json.load(a), json.load(b))
            with open(os.path.join(d, "trace-meta.json")) as f:
                meta = json.load(f)
            self.assertFalse(meta["proxy"])
            self.assertEqual(meta["order_from"], "--commitment order.md")
            rep = StringIO()
            with redirect_stdout(rep), redirect_stderr(StringIO()):
                code = run_folder(d)
            self.assertEqual(code, 1)  # 260 euros where at most 240 was agreed: held before the commit
            self.assertRegex(rep.getvalue(), r"commitment refund-5512\s+limit\.amount\s+WIDENED")
            self.assertIn("gate: HOLD", rep.getvalue())

    def test_a_missing_id_is_named(self):
        with tempfile.TemporaryDirectory() as t:
            p = page(t, "no-id.json", [{"traceId": "t1", "parentObservationId": None, "name": "root", "input": "x", "output": "y"}])
            code, out, err = cli(p, os.path.join(t, "out"))
            self.assertEqual((code, out), (2, ""))
            self.assertIn("has no `id`", err)
            self.assertEqual(err.count("\n"), 1)
            self.assertFalse(os.path.exists(os.path.join(t, "out")))

    def test_an_api_page_without_input_and_output_is_refused_not_written_empty(self):
        rows = [{"id": "r", "traceId": "t1", "parentObservationId": None, "type": "SPAN", "name": "root"},
                {"id": "g", "traceId": "t1", "parentObservationId": "r", "type": "GENERATION", "name": "call"}]
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(page(t, "core-only.json", rows), os.path.join(t, "out"))
            self.assertEqual(code, 2)
            self.assertIn("fields=core,basic,io", err)
            self.assertFalse(os.path.exists(os.path.join(t, "out")))

    def test_bad_input_is_code_2_in_one_line(self):
        with tempfile.TemporaryDirectory() as t:
            bad = os.path.join(t, "bad.jsonl")
            with open(bad, "w") as f:
                f.write('{"id": "a"}\nnot json\n')
            self.assertEqual(cli(bad, t)[0], 2)
            empty = os.path.join(t, "empty.jsonl")
            open(empty, "w").close()
            code, _, err = cli(empty, t)
            self.assertEqual(code, 2)
            self.assertIn("no observation", err)
            code, _, err = cli(API, t, "--commitment", os.path.join(t, "missing.md"))
            self.assertEqual(code, 2)
            self.assertIn("--commitment", err)

    def test_several_traces_give_several_folders(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(API, ONE, NAMES, os.path.join(t, "out"), "--prefix", "all")
            self.assertEqual(code, 0, err)
            self.assertEqual(sorted(os.listdir(os.path.join(t, "out"))), ["all-5eed5eed", "all-abcdef01", "all-c0ffee00"])
            self.assertEqual(len(out.splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
