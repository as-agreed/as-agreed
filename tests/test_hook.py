"""`as-agreed hook` on the invented example in examples/plugin-hold: what the plugin's hook denies and lets through."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from as_agreed.hook import main  # noqa: E402

EXAMPLE = ROOT / "examples" / "plugin-hold"


class TheHook(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        shutil.copytree(EXAMPLE / ".as-agreed", os.path.join(self.dir, ".as-agreed"))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def call(self, event):
        if isinstance(event, str):
            event = json.loads((EXAMPLE / "events" / f"{event}.json").read_text())
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": self.dir}), contextlib.redirect_stdout(out):
            self.assertEqual(main(json.dumps(event)), 0)
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def log(self):
        with open(os.path.join(self.dir, ".as-agreed", "hook.jsonl")) as f:
            return [json.loads(ln) for ln in f]

    def denied(self, answer):
        out = (answer or {}).get("hookSpecificOutput") or {}
        return out.get("permissionDecision") == "deny", out.get("permissionDecisionReason", "")

    def test_the_faithful_payment_goes_through(self):
        answer = self.call("faithful")
        self.assertFalse(self.denied(answer)[0])
        self.assertEqual(answer["systemMessage"], "as-agreed: Kept as agreed: 3 of 3 binding terms, checked at "
                                                  "delivery; 0 held; 0 unchecked")

    def test_a_dropped_amount_is_denied(self):
        deny, why = self.denied(self.call("dropped"))
        self.assertTrue(deny)
        self.assertIn("limit.amount DROPPED", why)

    def test_an_amount_over_the_limit_is_denied(self):
        deny, why = self.denied(self.call("widened"))
        self.assertTrue(deny)
        self.assertIn("19800 where at most 18400 USD was agreed", why)

    def test_an_unconfirmed_record_is_refused(self):
        path = os.path.join(self.dir, ".as-agreed", "record.json")
        rec = json.loads(Path(path).read_text())
        rec["confirmed_by"] = ""
        Path(path).write_text(json.dumps(rec))
        deny, why = self.denied(self.call("faithful"))
        self.assertTrue(deny)
        self.assertIn("not confirmed by a person", why)
        self.assertEqual(self.log()[-1]["decision"], "refused")

    def test_a_call_no_step_names_is_not_checked(self):
        self.assertIsNone(self.call({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
        other = json.loads((EXAMPLE / "events" / "dropped.json").read_text())
        other["tool_input"]["file_path"] = "project/notes.md"
        self.assertIsNone(self.call(other))
        self.assertEqual([r["decision"] for r in self.log()], ["unbound", "unbound"])

    def test_a_declared_step_that_cannot_be_checked_is_refused(self):
        event = json.loads((EXAMPLE / "events" / "faithful.json").read_text())
        event["tool_input"]["content"] = "not json"
        deny, why = self.denied(self.call(event))
        self.assertTrue(deny)
        self.assertIn("could not check this step", why)

    def test_the_confirmed_record_is_frozen_against_file_tools(self):
        for name in ("record.json", "binding.json"):
            edit = {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(self.dir, ".as-agreed", name),
                                                         "old_string": "18400", "new_string": "19800"}}
            deny, why = self.denied(self.call(edit))
            self.assertTrue(deny)
            self.assertIn("frozen", why)

    def test_an_unconfirmed_record_can_still_be_drafted(self):
        path = os.path.join(self.dir, ".as-agreed", "record.json")
        rec = json.loads(Path(path).read_text())
        rec["confirmed_by"] = None
        Path(path).write_text(json.dumps(rec))
        self.assertIsNone(self.call({"tool_name": "Write", "tool_input": {"file_path": path, "content": "{}"}}))

    def test_a_project_without_the_folder_is_left_alone(self):
        shutil.rmtree(os.path.join(self.dir, ".as-agreed"))
        self.assertIsNone(self.call("dropped"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".as-agreed")))

    def test_the_log_holds_no_content(self):
        self.call("widened")
        self.assertNotIn("northwind", json.dumps(self.log()).replace("northwind-freight", ""))
        self.assertNotIn("content", self.log()[0])


if __name__ == "__main__":
    unittest.main()
