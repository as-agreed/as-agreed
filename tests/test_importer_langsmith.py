"""The LangSmith importer (SPEC §8.3, shipped in 0.0.3): `as-agreed import langsmith <export>... <out_folder>`.

Every fixture in tests/fixtures/langsmith/ is our own invented text, written for these tests; no text of LangSmith's
own examples is copied here. The API page, the SDK's nested run and the CSV give the same links; runs are ordered by
`dotted_order`; the root is the run whose id is the trace id; a hand-off is found where the declared agent changes
and nowhere else; an agent comes only from declared metadata, never from a run's name or a graph node; a run's status
and error travel with its link; `delivered` says whether the run ends with an output; a missing field is named, never
written as empty links."""
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
from as_agreed.importers import langsmith as S  # noqa: E402
from as_agreed.importers.langfuse import Scrub  # noqa: E402
from as_agreed.importers.stages import run_folder, stage_files  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "langsmith"
API, SDK, CSV = str(FIX / "handoff.runs.json"), str(FIX / "handoff.sdk.json"), str(FIX / "handoff.csv")
ONE = str(FIX / "one-agent.jsonl")
ORDER = str(FIX / "booking-order" / "order.md")
TID = "desk01"


def read(path):
    traces, _ = S.read_exports([path])
    assert len(traces) == 1, path
    return S.read_trace(next(iter(traces.values())), Scrub(True))


def cli(*args):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["import", "langsmith", *args])
    return code, out.getvalue(), err.getvalue()


def write(tmp, name, rows, lines=False):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        if lines:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        else:
            json.dump(rows, f)
    return path


def lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def run(rid, parent, name, sec, outputs, **extra):
    r = {"id": rid, "trace_id": "t", "parent_run_id": parent, "name": name, "run_type": "chain",
         "start_time": f"2030-01-01T10:00:0{sec}", "end_time": f"2030-01-01T10:00:0{sec}.5", "status": "success",
         "inputs": {"x": 1}, "outputs": outputs}
    r.update(extra)
    return r


class Reading(unittest.TestCase):
    def test_the_api_page_the_sdk_run_and_the_csv_give_the_same_links(self):
        ma, la = read(API)
        strip = lambda ls: [{k: v for k, v in l.items() if k != "line"} for l in ls]  # the time spellings differ
        for other in (SDK, CSV):
            mb, lb = read(other)
            self.assertEqual(strip(la), strip(lb), other)
            self.assertEqual(ma["handoffs"], mb["handoffs"], other)
        self.assertEqual(S.load_file(API)[1], "API page")
        self.assertEqual(S.load_file(SDK)[1], "JSON, child runs nested")
        self.assertEqual(S.load_file(CSV)[1], "CSV")
        self.assertEqual(S.load_file(ONE)[1], "JSON Lines")

    def test_the_handoff_is_found_where_the_declared_agent_changes(self):
        m, links = read(API)
        self.assertEqual(m["agents"], ["concierge", "reservations"])
        self.assertEqual(m["handoffs"], [{"at_link": 3, "from_agent": "concierge", "to_agent": "reservations",
                                          "via": "transfer_to_reservations",
                                          "to_observation_id": "desk05"}])
        self.assertEqual(links[2]["to"], "reservations")
        self.assertEqual([l["agent"] for l in links[1:]],
                         ["concierge", "concierge", "reservations", "reservations", "reservations", None])

    def test_links_carry_text_parents_status_and_error(self):
        m, links = read(API)
        self.assertEqual(links[0]["text"], "request: Book a table for four at Lumen on 12 March 2030, at most 200 euros "
                                           "in total.")
        self.assertEqual((m["task_from"], m["root_from"]), ("root input", "id equals trace_id"))
        self.assertEqual([l["parent"] for l in links[1:]], [0, 1, 0, 0, 0, 5])  # the root's outputs read the last link
        self.assertEqual(links[2]["text"], "messages.0.role: assistant\nmessages.0.content: Party of four, Lumen, "
                                           "12 March 2030.\nmessages.0.content: At most 200 euros.")
        self.assertEqual(links[4]["outputs"], {"party": 4, "total": 230, "currency": "EUR"})
        failed = links[3]
        self.assertEqual((failed["status"], failed["text"]),
                         ("error", "error: TimeoutError: the table service did not answer in 5 seconds"))
        self.assertTrue(failed["error"].startswith("TimeoutError"))
        self.assertNotIn("outputs", failed)
        self.assertEqual({l["status"] for l in links[1:] if l is not failed}, {"success"})
        self.assertEqual(m["errors"], [{"at_link": 3, "record": "reserve_table", "status": "error",
                                        "error": failed["error"]}])
        self.assertEqual(m["status"], {"error": 1, "success": 7})
        self.assertEqual(links[-1]["kind"], "final")
        self.assertTrue(m["delivered"])

    def test_runs_are_ordered_by_dotted_order_not_by_start_time(self):
        m, links = read(ONE)  # "logger agent" starts before scan_shelf, but it is the auditor's later sibling
        self.assertEqual([l["phase"] for l in links[1:]], ["plan", "scan_shelf", "ChatModel", "logger agent", "shelf-audit"])
        self.assertEqual(m["siblings"], "ordered by dotted_order")
        rows = lines(ONE)
        for r in rows:
            del r["dotted_order"]
        with tempfile.TemporaryDirectory() as t:
            traces, _ = S.read_exports([write(t, "no-dotted.jsonl", rows, lines=True)])
            m2, links2 = S.read_trace(next(iter(traces.values())), Scrub(True))
        self.assertEqual([l["phase"] for l in links2[1:]], ["plan", "logger agent", "scan_shelf", "ChatModel", "shelf-audit"])
        self.assertEqual(m2["siblings"], "ordered by start time (a run has no dotted_order)")

    def test_no_handoff_with_one_agent_and_no_agent_from_a_name_or_a_graph_node(self):
        m, links = read(ONE)
        self.assertEqual(m["agents"], ["auditor-7"])
        self.assertEqual(m["agents_declared_in_scopes"], ["auditor-7"])  # not "planner" (langgraph_node), not "logger"
        self.assertEqual(m["handoffs"], [])
        self.assertEqual([l["agent"] for l in links[1:]], ["auditor-7", "auditor-7", "auditor-7", None, None])
        self.assertEqual(links[1]["agent_declared_by"], "gen_ai.agent.id")
        self.assertTrue(all("to" not in l for l in links))

    def test_the_root_is_the_run_whose_id_is_the_trace_id(self):
        rows = [run("stray", "gone", "stray", 1, {"a": 1}),  # its parent is not in the export, and it starts first
                run("t", None, "pipeline", 2, {"done": True}, inputs={"task": "Ship the three crates to Oslo."}),
                run("c", "t", "pack", 3, {"crates": 3})]
        with tempfile.TemporaryDirectory() as t:
            traces, _ = S.read_exports([write(t, "runs.json", rows)])
            m, links = S.read_trace(traces["t"], Scrub(True))
        self.assertEqual((m["root_from"], m["roots"], m["parents_not_in_export"]), ("id equals trace_id", 2, 1))
        self.assertEqual(links[0]["text"], "task: Ship the three crates to Oslo.")
        self.assertEqual([(l["phase"], l["parent"]) for l in links[1:]], [("stray", 0), ("pack", 0), ("pipeline", 2)])

    def test_delivered_is_false_when_the_root_failed_with_no_outputs(self):
        rows = [run("t", None, "pipeline", 1, None, status="error", error="ValueError: no crates left\ntrace"),
                run("c", "t", "pack", 2, {"crates": 0})]
        with tempfile.TemporaryDirectory() as t:
            traces, _ = S.read_exports([write(t, "runs.json", rows)])
            m, links = S.read_trace(traces["t"], Scrub(True))
        self.assertFalse(m["delivered"])
        self.assertEqual((links[-1]["kind"], links[-1]["text"]), ("final", "error: ValueError: no crates left"))

    def test_the_first_user_message_of_a_chat_input(self):
        self.assertEqual(S.first_user({"messages": [{"role": "system", "content": "Be brief."},
                                                    {"role": "user", "content": "Ship the crates."}]}), "Ship the crates.")
        batch = {"messages": [[{"kwargs": {"type": "system", "content": "Be brief."}},
                               {"kwargs": {"type": "human", "content": "Pack three crates."}}]]}
        self.assertEqual(S.first_user(batch), "Pack three crates.")  # a batch of lists, fields under kwargs
        self.assertEqual(S.first_user({"task": "x"}), "")

    def test_a_trace_id_is_read_from_dotted_order_when_missing(self):
        rows = lines(ONE)
        for r in rows:
            del r["trace_id"]
        with tempfile.TemporaryDirectory() as t:
            traces, _ = S.read_exports([write(t, "runs.jsonl", rows, lines=True)])
        self.assertEqual(list(traces), ["shelf01"])


class Command(unittest.TestCase):
    def test_import_writes_the_stage_folder_with_a_proxy_record(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(API, t)
            self.assertEqual(code, 0, err)
            d = os.path.join(t, "handoff-desk01")
            self.assertEqual(out, f"{d}\t6 links\tagents 2\thand-offs concierge -> reservations at link 3\t"
                                  f"delivered true\terrors at link 3\n")
            self.assertEqual(sorted(os.listdir(d)), [
                "01-order.md", "02-concierge-concierge.json", "02-concierge-concierge.md",
                "03-concierge-ChatModel.json", "03-concierge-ChatModel.md", "04-reservations-reserve_table.md",
                "05-reservations-reserve_table.json", "05-reservations-reserve_table.md",
                "06-reservations-ChatModel.json", "06-reservations-ChatModel.md",
                "07-no-agent-booking-desk.json", "07-no-agent-booking-desk.md", "commitments.json", "trace-meta.json"])
            with open(os.path.join(d, "commitments.json")) as f:
                self.assertEqual(json.load(f), {"commitments": [], "proxy": True})
            with open(os.path.join(d, "trace-meta.json")) as f:
                meta = json.load(f)
            self.assertTrue(meta["proxy"])
            self.assertEqual(meta["errors"][0]["at_link"], 3)
            self.assertEqual(len(stage_files(d)), 7)
            out2, err2 = StringIO(), StringIO()
            with redirect_stdout(out2), redirect_stderr(err2):
                code = run_folder(d)
            self.assertEqual(code, 2)  # nothing was compared against a commitment, and reconcile says so
            self.assertIn("as-agreed: proxy (task as stated; no order was recorded)", out2.getvalue())

    def test_commitment_passes_the_order_through_and_reconcile_holds(self):
        with tempfile.TemporaryDirectory() as t:
            code, _, err = cli(CSV, t, "--commitment", ORDER, "--prefix", "refund")
            self.assertEqual(code, 0, err)
            d = os.path.join(t, "refund-desk01")
            with open(ORDER) as a, open(os.path.join(d, "01-order.md")) as b:
                self.assertEqual(a.read(), b.read())
            with open(FIX / "booking-order" / "commitments.json") as a, open(os.path.join(d, "commitments.json")) as b:
                self.assertEqual(json.load(a), json.load(b))
            with open(os.path.join(d, "trace-meta.json")) as f:
                self.assertFalse(json.load(f)["proxy"])
            rep = StringIO()
            with redirect_stdout(rep), redirect_stderr(StringIO()):
                code = run_folder(d)
            self.assertEqual(code, 1)  # 230 euros where at most 200 was agreed: held before the commit
            self.assertRegex(rep.getvalue(), r"WIDENED\s+commitment book-lumen limit\.total\s+230 where at most 200 EUR")
            self.assertIn("gate: HOLD", rep.getvalue())

    def test_a_missing_field_is_named(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(write(t, "no-id.json", [{"trace_id": "t", "name": "root", "outputs": {"a": 1}}]),
                                 os.path.join(t, "out"))
            self.assertEqual((code, out), (2, ""))
            self.assertIn("has no `id`", err)
            self.assertEqual(err.count("\n"), 1)
            code, _, err = cli(write(t, "no-trace.json", [{"id": "r", "name": "root", "outputs": {"a": 1}}]),
                               os.path.join(t, "out"))
            self.assertEqual(code, 2)
            self.assertIn("`trace_id`", err)
            rows = [{k: v for k, v in run("t", None, "root", 1, None).items() if k not in ("inputs", "outputs")}]
            code, _, err = cli(write(t, "no-io.json", rows), os.path.join(t, "out"))
            self.assertEqual(code, 2)
            self.assertIn("`inputs` or an `outputs`", err)
            self.assertFalse(os.path.exists(os.path.join(t, "out")))

    def test_bad_input_is_code_2_in_one_line(self):
        with tempfile.TemporaryDirectory() as t:
            bad = os.path.join(t, "bad.jsonl")
            with open(bad, "w") as f:
                f.write('{"id": "a", "trace_id": "a"}\nnot json\n')
            self.assertEqual(cli(bad, t)[0], 2)
            empty = os.path.join(t, "empty.jsonl")
            open(empty, "w").close()
            code, _, err = cli(empty, t)
            self.assertEqual(code, 2)
            self.assertIn("no record", err)
            no_task = write(t, "no-task.json", [run("t", None, "root", 1, {"a": 1}, inputs=None)])
            code, _, err = cli(no_task, os.path.join(t, "out"))
            self.assertEqual(code, 2)
            self.assertIn("--commitment", err)
            code, _, err = cli(API, t, "--commitment", os.path.join(t, "missing.md"))
            self.assertEqual(code, 2)

    def test_several_files_give_several_folders(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(API, ONE, os.path.join(t, "out"), "--prefix", "all")
            self.assertEqual(code, 0, err)
            self.assertEqual(sorted(os.listdir(os.path.join(t, "out"))), ["all-desk01", "all-shelf01"])
            self.assertEqual(len(out.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
