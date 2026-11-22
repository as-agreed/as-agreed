"""`as-agreed hook` on the invented example in examples/plugin-hold: what the plugin's hook denies and lets through."""
import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from as_agreed.hook import main  # noqa: E402

EXAMPLE = ROOT / "examples" / "plugin-hold"


def event(name):
    return json.loads((EXAMPLE / "events" / f"{name}.json").read_text())


class TheHook(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.folder = os.path.join(self.dir, ".as-agreed")
        shutil.copytree(EXAMPLE / ".as-agreed", self.folder)
        self.record = os.path.join(self.folder, "record.json")

    def tearDown(self):
        os.chmod(self.folder, stat.S_IRWXU)
        shutil.rmtree(self.dir)

    def call(self, ev, code=0):
        ev = event(ev) if isinstance(ev, str) else ev
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": self.dir}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            self.assertEqual(main(json.dumps(ev)), code)
        self.err = err.getvalue()
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def log(self):
        with open(os.path.join(self.folder, "hook.jsonl")) as f:
            return [json.loads(ln) for ln in f]

    def denied(self, answer):
        out = (answer or {}).get("hookSpecificOutput") or {}
        return out.get("permissionDecision") == "deny", out.get("permissionDecisionReason", "")

    def set_record(self, **kw):
        rec = json.loads(Path(self.record).read_text())
        rec.update(kw)
        Path(self.record).write_text(json.dumps(rec))

    def test_the_faithful_payment_goes_through(self):
        answer = self.call("faithful")
        self.assertFalse(self.denied(answer)[0])
        self.assertEqual(answer["systemMessage"], "as-agreed: Kept as agreed: 1 of 1 binding terms, checked at "
                                                  "delivery; 0 held; 0 unchecked")

    def test_a_dropped_amount_is_denied(self):
        deny, why = self.denied(self.call("dropped"))
        self.assertTrue(deny)
        self.assertIn("limit.amount DROPPED", why)

    def test_an_amount_over_the_limit_is_denied(self):
        deny, why = self.denied(self.call("widened"))
        self.assertTrue(deny)
        self.assertIn("19800 where at most 18400 USD was agreed", why)
        self.assertIn("1 held (pay-invoice limit.amount WIDENED", why)

    def test_a_log_that_cannot_be_written_still_denies(self):
        os.chmod(self.folder, stat.S_IRUSR | stat.S_IXUSR)
        self.assertTrue(self.denied(self.call("widened"))[0])

    def test_an_unconfirmed_record_is_refused(self):
        self.set_record(confirmed_by="")
        deny, why = self.denied(self.call("faithful"))
        self.assertTrue(deny)
        self.assertIn("not confirmed by a person", why)
        self.assertEqual(self.log()[-1]["decision"], "refused")

    def test_the_agent_cannot_write_the_confirmation(self):
        self.set_record(confirmed_by="")
        rec = json.loads(Path(self.record).read_text())
        rec["confirmed_by"] = "the person"
        for ev in ({"tool_name": "Write", "tool_input": {"file_path": self.record, "content": json.dumps(rec)}},
                   {"tool_name": "Edit", "tool_input": {"file_path": self.record, "old_string": '"confirmed_by": ""',
                                                        "new_string": '"confirmed_by": "the person"'}}):
            deny, why = self.denied(self.call(ev))
            self.assertTrue(deny)
            self.assertIn("only the person writes confirmed_by", why)
        rec["confirmed_by"] = ""
        self.assertIsNone(self.call({"tool_name": "Write", "tool_input": {"file_path": self.record,
                                                                         "content": json.dumps(rec)}}))

    def test_the_confirmed_record_is_frozen_against_file_tools(self):
        for target in (self.record, os.path.join(self.folder, "binding.json"),
                       os.path.join(self.dir, ".AS-AGREED", "Record.json"), ".as-agreed/./record.json"):
            edit = {"tool_name": "Edit", "tool_input": {"file_path": target, "old_string": "18400",
                                                         "new_string": "19800"}}
            if target.endswith("Record.json") and not os.path.exists(target):
                continue  # a case-sensitive disk: that path is another file
            deny, why = self.denied(self.call(edit))
            self.assertTrue(deny, target)
            self.assertIn("frozen", why)

    def test_a_broken_record_blocks_only_the_bound_step(self):
        Path(self.record).write_text('{"order": "x",}')
        self.assertIsNone(self.call({"tool_name": "Read", "tool_input": {"file_path": self.record}}))
        self.assertIsNone(self.call({"tool_name": "Write", "tool_input": {"file_path": self.record, "content": "{}"}}))
        self.assertTrue(self.denied(self.call("faithful"))[0])

    def test_a_call_no_step_names_is_not_checked_or_logged(self):
        self.assertIsNone(self.call({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
        other = event("dropped")
        other["tool_input"]["file_path"] = "project/notes.md"
        self.assertIsNone(self.call(other))
        self.assertFalse(os.path.exists(os.path.join(self.folder, "hook.jsonl")))

    def test_a_bound_path_matches_after_normalising(self):
        ev = event("dropped")
        ev["tool_input"]["file_path"] = "project//outbox/./payment.json"
        self.assertTrue(self.denied(self.call(ev))[0])

    def test_a_step_that_cannot_be_checked_does_not_run(self):
        for content in ("not json", json.dumps([{"amount": 19800}]), "19800", "null"):
            ev = event("faithful")
            ev["tool_input"]["content"] = content
            self.assertIsNone(self.call(ev, code=2))
            self.assertIn("could not check this call", self.err)

    def test_a_project_without_the_folder_is_left_alone(self):
        shutil.rmtree(self.folder)
        os.makedirs(self.folder)  # tearDown expects it; without binding.json the hook does nothing
        self.assertIsNone(self.call("dropped"))
        self.assertEqual(os.listdir(self.folder), [])

    def test_the_log_holds_no_value_of_the_call(self):
        self.call("widened")
        text = json.dumps(self.log())
        self.assertNotIn("19800", text)
        self.assertNotIn("Northwind", text)
        self.assertEqual(self.log()[0]["held"], ["pay-invoice limit.amount WIDENED"])


if __name__ == "__main__":
    unittest.main()
