"""The OpenTelemetry importer (SPEC §8.4, shipped in 0.0.3): `as-agreed import otlp <export>... <out_folder>`.

Every fixture in tests/fixtures/otlp/ is our own invented text, written for these tests; no text of OpenTelemetry's
own examples is copied here. Structured attributes and attributes written as JSON strings give the same links, and so
do lines in any order; the step type is `gen_ai.operation.name`; an agent comes from `gen_ai.agent.name` or
`gen_ai.agent.id` only; a hand-off is found where the declared agent changes and nowhere else; a step without content
attributes is a link with empty text, counted apart, with the opt-in setting named; a trace with no order text is
refused with the `--commitment` hint; a missing field is named."""
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
from as_agreed.importers import otlp as O  # noqa: E402
from as_agreed.importers.langfuse import Scrub  # noqa: E402
from as_agreed.importers.stages import run_folder, stage_files  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "otlp"
JSON, JSONL = str(FIX / "handoff.otlp.json"), str(FIX / "handoff.otlp.jsonl")
ONE, NO_CONTENT = str(FIX / "one-agent.otlp.jsonl"), str(FIX / "no-content.otlp.jsonl")
ORDER = str(FIX / "trip-order" / "order.md")
SETTING = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT"


def read(path):
    traces = O.read_exports([path])
    assert len(traces) == 1, path
    return O.read_trace(next(iter(traces.values())), Scrub(True))


def cli(*args):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["import", "otlp", *args])
    return code, out.getvalue(), err.getvalue()


def span(sid, parent, name, sec, **attrs):
    return {"traceId": "ab" * 16, "spanId": sid, "parentSpanId": parent or "", "name": name,
            "startTimeUnixNano": str(1_900_000_000_000_000_000 + sec * 10 ** 9),
            "endTimeUnixNano": str(1_900_000_000_500_000_000 + sec * 10 ** 9),
            "attributes": [{"key": k, "value": {"stringValue": v if isinstance(v, str) else json.dumps(v)}}
                           for k, v in attrs.items()]}


def write(tmp, name, spans):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"resourceSpans": [{"resource": {}, "scopeSpans": [{"scope": {}, "spans": spans}]}]}, f)
    return path


class Reading(unittest.TestCase):
    def test_structured_and_json_string_attributes_give_the_same_links(self):
        ma, la = read(JSON)
        mb, lb = read(JSONL)  # two lines, the later batch first, every message a JSON string
        self.assertEqual(la, lb)
        self.assertEqual(ma["handoffs"], mb["handoffs"])
        self.assertEqual(O.any_value({"kvlistValue": {"values": [{"key": "n", "value": {"intValue": "7"}}]}}), {"n": 7})
        self.assertEqual(O.any_value({"arrayValue": {"values": [{"boolValue": True}, {"doubleValue": 0.5}]}}), [True, 0.5])

    def test_the_handoff_is_found_where_the_declared_agent_changes(self):
        m, links = read(JSON)
        self.assertEqual(m["agents"], ["booker", "planner"])
        self.assertEqual(m["handoffs"], [{"at_link": 4, "from_agent": "planner", "to_agent": "booker",
                                          "via": "execute_tool transfer_to_booker", "to_observation_id": "0000000000000a05"}])
        self.assertEqual(links[3]["to"], "booker")
        self.assertEqual([l["agent"] for l in links[1:]], ["planner", "planner", "planner", "booker", "booker", None])
        self.assertEqual(links[4]["agent_declared_by"], "gen_ai.agent.name")

    def test_links_carry_text_parents_and_the_step_type(self):
        m, links = read(JSON)
        self.assertEqual(links[0]["text"], "Book two train seats to Lyon for 14 May 2030, at most 120 euros in total.")
        self.assertEqual(m["task_from"], "root input")
        self.assertEqual([l["kind"] for l in links[1:]],
                         ["invoke_agent", "chat", "execute_tool", "execute_tool", "chat", "final"])
        self.assertEqual([l["parent"] for l in links[1:]], [0, 1, 1, 0, 0, 5])
        self.assertEqual(links[1]["text"], "Send this to the booking agent.")
        self.assertEqual(links[2]["outputs"], {"seats": 2, "city": "Lyon", "date": "2030-05-14", "max_total": 120})
        self.assertEqual(links[2]["text"], "seats: 2\ncity: Lyon\ndate: 2030-05-14\nmax_total: 120")
        self.assertEqual(links[4]["outputs"], {"seats": 2, "total": 140, "currency": "EUR"})  # a structured tool result
        self.assertNotIn("outputs", links[5])
        self.assertEqual(links[6]["text"], "Booked two seats to Lyon for 140 euros.")
        self.assertEqual((m["not_links"], m["content_not_captured"]), (1, 0))  # the booker span has input, no output
        self.assertEqual(m["siblings"], "ordered by start time (startTimeUnixNano)")
        self.assertTrue(m["delivered"])

    def test_one_agent_no_handoff_and_no_agent_from_a_name(self):
        m, links = read(ONE)
        self.assertEqual(m["agents"], ["sorter-1"])
        self.assertEqual(m["handoffs"], [])
        self.assertEqual([l["agent"] for l in links[1:]], ["sorter-1", "sorter-1", "sorter-1", None])
        self.assertEqual(links[1]["agent_declared_by"], "gen_ai.agent.id")
        self.assertEqual(links[4]["phase"], "invoke_agent helper")  # its name reads like an agent; it declares none
        self.assertEqual(m["task_from"], "first user message of 'invoke_agent sorter' (root input empty)")
        self.assertEqual(links[0]["text"], "Sort the five parcels by weight, heaviest first.")
        self.assertEqual(m["spans_not_steps"], 1)  # the HTTP root span is structure only

    def test_a_tool_call_with_no_text_part_keeps_its_lines(self):
        _, links = read(ONE)
        self.assertEqual(links[1]["text"], "messages.0.role: assistant\nmessages.0.parts.0.type: tool_call\n"
                                           "messages.0.parts.0.id: call-1\nmessages.0.parts.0.name: weigh\n"
                                           "messages.0.parts.0.arguments.parcels: 5")
        self.assertEqual(links[2]["text"], "weights_kg: 4; 9; 1; 7; 3")

    def test_a_span_without_content_is_an_empty_link_counted_apart(self):
        m, links = read(NO_CONTENT)
        self.assertEqual([(l["phase"], l["text"]) for l in links[1:]],
                         [("chat m-3", "The totals are 120, 80 and 45 euros."), ("execute_tool add", ""),
                          ("chat m-3", "The sum is 245 euros.")])
        self.assertEqual(links[2]["content"], "not captured")
        self.assertEqual((m["content_not_captured"], m["content_not_captured_at_links"]), (1, [2]))
        self.assertEqual(m["content_setting"], SETTING)
        self.assertTrue(m["delivered"])
        self.assertEqual(m["agents"], ["summer"])


class Command(unittest.TestCase):
    def test_import_writes_the_stage_folder_and_names_the_content_setting(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(NO_CONTENT, t)
            self.assertEqual(code, 0, err)
            d = os.path.join(t, "no-content-7a1d0003")
            self.assertEqual(out, f"{d}\t3 links\tagents 1\thand-offs none\tdelivered true\tcontent not captured on 1 of 3 "
                                  f"links (set {SETTING}=SPAN_ONLY; the default NO_CONTENT records none)\n")
            self.assertEqual(sorted(os.listdir(d)), ["01-order.md", "02-summer-chat-m-3.md", "03-summer-execute_tool-add.md",
                                                     "04-summer-chat-m-3.md", "commitments.json", "trace-meta.json"])
            with open(os.path.join(d, "03-summer-execute_tool-add.md")) as f:
                self.assertEqual(f.read(), "\n")
            with open(os.path.join(d, "commitments.json")) as f:
                self.assertEqual(json.load(f), {"commitments": [], "proxy": True})
            self.assertEqual(len(stage_files(d)), 4)

    def test_commitment_passes_the_order_through_and_reconcile_holds(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(JSONL, t, "--commitment", ORDER, "--prefix", "trip")
            self.assertEqual(code, 0, err)
            d = os.path.join(t, "trip-7a1d0001")
            with open(ORDER) as a, open(os.path.join(d, "01-order.md")) as b:
                self.assertEqual(a.read(), b.read())
            with open(os.path.join(d, "trace-meta.json")) as f:
                self.assertFalse(json.load(f)["proxy"])
            rep = StringIO()
            with redirect_stdout(rep), redirect_stderr(StringIO()):
                code = run_folder(d)
            self.assertEqual(code, 1)
            self.assertRegex(rep.getvalue(), r"WIDENED\s+commitment seats-lyon limit\.total\s+140 where at most 120 EUR")
            self.assertIn("gate: HOLD", rep.getvalue())

    def test_a_trace_with_no_content_needs_the_order_file(self):
        spans = [span("01", None, "invoke_agent a", 0, **{"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "a"}),
                 span("02", "01", "chat m", 1, **{"gen_ai.operation.name": "chat"}),
                 span("03", "01", "execute_tool t", 2, **{"gen_ai.operation.name": "execute_tool"})]
        with tempfile.TemporaryDirectory() as t:
            path = write(t, "none.json", spans)
            code, out, err = cli(path, os.path.join(t, "out"))
            self.assertEqual((code, out), (2, ""))
            self.assertIn("--commitment", err)
            self.assertIn(SETTING, err)
            self.assertEqual(err.count("\n"), 1)
            self.assertFalse(os.path.exists(os.path.join(t, "out")))
            code, out, err = cli(path, os.path.join(t, "out"), "--commitment", ORDER)
            self.assertEqual(code, 0, err)
            self.assertIn("\tdelivered unknown\tcontent not captured on 2 of 2 links", out)

    def test_a_missing_field_is_named(self):
        with tempfile.TemporaryDirectory() as t:
            s = span("01", None, "root", 0, **{"gen_ai.input.messages": []})
            for field in ("spanId", "traceId"):
                code, out, err = cli(write(t, "x.json", [{k: v for k, v in s.items() if k != field}]), os.path.join(t, "out"))
                self.assertEqual((code, out), (2, ""))
                self.assertIn(f"has no `{field}`", err)
            bad = os.path.join(t, "bad.jsonl")
            with open(bad, "w") as f:
                f.write('{"resourceSpans": []}\n{"spans": []}\n')
            code, _, err = cli(bad, t)
            self.assertEqual(code, 2)
            self.assertIn("`resourceSpans`", err)
            with open(bad, "w") as f:
                f.write('{"resourceSpans": []}\nnot json\n')
            self.assertEqual(cli(bad, t)[0], 2)
            code, _, err = cli(write(t, "empty.json", []), t)
            self.assertEqual(code, 2)
            self.assertIn("no record", err)
            self.assertFalse(os.path.exists(os.path.join(t, "out")))

    def test_several_files_give_several_folders(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(JSON, ONE, NO_CONTENT, os.path.join(t, "out"), "--prefix", "all")
            self.assertEqual(code, 0, err)
            self.assertEqual(sorted(os.listdir(os.path.join(t, "out"))), ["all-7a1d0001", "all-7a1d0002", "all-7a1d0003"])
            self.assertEqual(len(out.splitlines()), 3)

    def test_two_traces_with_one_id_head_keep_their_whole_ids(self):
        a = span("01", None, "chat", 0, **{"gen_ai.operation.name": "chat",
                                            "gen_ai.input.messages": [{"role": "user", "parts": [{"type": "text",
                                                                       "content": "Name the three primary colours."}]}]})
        b = dict(a, traceId="ab" * 15 + "cd")
        with tempfile.TemporaryDirectory() as t:
            code, out, err = cli(write(t, "two.json", [a, b]), os.path.join(t, "out"), "--prefix", "p")
            self.assertEqual(code, 0, err)
            self.assertEqual(sorted(os.listdir(os.path.join(t, "out"))), ["p-" + "ab" * 16, "p-" + "ab" * 15 + "cd"])


if __name__ == "__main__":
    unittest.main()
