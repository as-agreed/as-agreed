"""The record found from a throwaway folder (0.1.1): an agent started in a Multica task folder, a CI checkout or a
cloud workdir has no `.as-agreed/` of its own. The hook finds the confirmed record through `AS_AGREED_RECORD` or a
`.as-agreed-record` pointer in the start folder or a parent; it denies every call when the named folder is missing; it
keeps the record, and the way to it, frozen against file tools once the person has confirmed it.

The throwaway folder is `tests/fixtures/locate/workspace/task/workdir` (invented). The record and the planted events
are `examples/plugin-hold`'s: the dropped amount is denied and the faithful payment goes through."""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from as_agreed import band  # noqa: E402
from as_agreed.hook import main  # noqa: E402
from as_agreed.locate import ENV, POINTER, locate  # noqa: E402

EXAMPLE = ROOT / "examples" / "plugin-hold"
FIX = ROOT / "tests" / "fixtures" / "locate"
FAITHFUL = "Kept as agreed: 1 of 1 binding terms, checked at delivery; 0 held; 0 unchecked"
SH = shutil.which("sh")
ROOT_USER = hasattr(os, "getuid") and os.getuid() == 0


def event(name):
    return json.loads((EXAMPLE / "events" / f"{name}.json").read_text())


def write(path, text):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": text}}


def denied(answer):
    out = (answer or {}).get("hookSpecificOutput") or {}
    return out.get("permissionDecision") == "deny", out.get("permissionDecisionReason", "")


class Tree(unittest.TestCase):
    """`<tmp>/workspace/task/workdir`, the throwaway folder the agent starts in, and `<tmp>/orders/pay-invoice`, the
    confirmed record kept elsewhere."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        shutil.copytree(str(FIX / "workspace"), os.path.join(self.dir, "workspace"))
        self.workspace = os.path.join(self.dir, "workspace")
        self.work = os.path.join(self.workspace, "task", "workdir")
        self.record = os.path.join(self.dir, "orders", "pay-invoice")
        shutil.copytree(str(EXAMPLE / ".as-agreed"), self.record)

    def tearDown(self):
        os.chmod(self.record, stat.S_IRWXU)
        shutil.rmtree(self.dir)

    def call(self, ev, env=None, code=0):
        ev = event(ev) if isinstance(ev, str) else ev
        environ = {k: v for k, v in os.environ.items() if k != ENV}
        environ.update({"CLAUDE_PROJECT_DIR": self.work}, **(env or {}))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            self.assertEqual(main(json.dumps(ev)), code)
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def point(self, folder, text):
        path = os.path.join(folder, POINTER)
        Path(path).write_text(text)
        return path

    def set_record(self, **kw):
        path = os.path.join(self.record, "record.json")
        rec = json.loads(Path(path).read_text())
        rec.update(kw)
        Path(path).write_text(json.dumps(rec))

    def held_and_kept(self, env=None):
        """The planted drop is denied for its dropped amount; the faithful payment goes through."""
        deny, why = denied(self.call("dropped", env))
        self.assertTrue(deny, why)
        self.assertIn("limit.amount DROPPED", why)
        answer = self.call("faithful", env)
        self.assertFalse(denied(answer)[0])
        self.assertEqual(answer["systemMessage"], "as-agreed: " + FAITHFUL)

    def refused(self, ev, words, env=None):
        deny, why = denied(self.call(ev, env))
        self.assertTrue(deny, ev)
        self.assertIn(words, why)
        return why


class ThroughTheVariable(Tree):
    def test_the_throwaway_folder_holds_no_record(self):
        self.assertFalse(os.path.exists(os.path.join(self.work, ".as-agreed")))
        self.assertEqual(locate(self.work, {}).how, "none")

    def test_the_planted_drop_is_denied_and_the_faithful_payment_passes(self):
        self.held_and_kept({ENV: self.record})

    def test_the_run_is_kept_beside_the_record_not_in_the_throwaway_folder(self):
        self.held_and_kept({ENV: self.record})
        with open(os.path.join(self.record, "hook.jsonl")) as f:
            self.assertEqual([json.loads(ln)["decision"] for ln in f], ["hold", "pass"])
        run = band.run_folder(self.work, "example", self.record)
        self.assertEqual(run, os.path.join(self.record, "runs", "example"))
        self.assertEqual(band.calls(self.work, "example", record=self.record), 2)
        rows = band.read_rows(os.path.join(run, "ledger.jsonl"))
        self.assertEqual([r["drift_class"] for r in rows if r.get("kind") == "verdict" and r.get("field") == "limit.amount"],
                         ["DROPPED", "PRESERVED"])
        self.assertTrue(os.path.isfile(os.path.join(run, "band.json")))
        self.assertEqual(Path(self.record, "runs", ".gitignore").read_text(), "*\n")
        self.assertFalse(os.path.exists(os.path.join(self.work, ".as-agreed")))

    def test_a_relative_path_is_read_from_the_start_folder(self):
        self.held_and_kept({ENV: os.path.relpath(self.record, self.work)})


class ThroughAPointer(Tree):
    def test_a_pointer_in_a_parent_folder_holds_the_drop(self):
        pointer = self.point(self.workspace, self.record + "\n")
        found = locate(self.work, {})
        self.assertEqual((found.how, found.folder, found.pointer, found.error),
                         ("pointer", self.record, pointer, None))
        self.held_and_kept()
        self.assertEqual(band.calls(self.work, "example", record=self.record), 2)

    def test_a_relative_pointer_is_read_from_its_own_folder(self):
        self.point(self.workspace, os.path.join("..", "orders", "pay-invoice"))
        self.held_and_kept()

    def test_the_nearest_pointer_counts(self):
        self.point(self.workspace, self.record)
        self.point(self.work, os.path.join(self.dir, "nowhere"))
        self.refused("faithful", "missing")

    def test_the_variable_comes_before_a_pointer(self):
        self.point(self.workspace, os.path.join(self.dir, "nowhere"))
        self.held_and_kept({ENV: self.record})


class FailClosed(Tree):
    def test_a_missing_folder_named_by_the_variable_denies_every_call(self):
        missing = os.path.join(self.dir, "orders", "gone")
        for ev in ("faithful", "dropped", {"tool_name": "Read", "tool_input": {"file_path": "x"}},
                   {"tool_name": "Bash", "tool_input": {"command": "ls"}}):
            why = self.refused(ev, "missing", {ENV: missing})
            self.assertIn(ENV, why)
            self.assertIn(missing, why)

    def test_a_missing_folder_named_by_a_pointer_denies_every_call(self):
        pointer = self.point(self.workspace, os.path.join(self.dir, "orders", "gone"))
        for ev in ("faithful", {"tool_name": "Bash", "tool_input": {"command": "ls"}}):
            self.assertIn(pointer, self.refused(ev, "missing"))

    def test_a_pointer_that_does_not_name_one_folder_denies(self):
        for text in ("", "  \n", self.record + "\n" + self.record + "\n"):
            self.point(self.workspace, text)
            self.refused("faithful", "must hold one line")
        Path(self.workspace, POINTER).write_bytes(b"\xff\xfe")
        self.refused("faithful", "not UTF-8")
        os.remove(os.path.join(self.workspace, POINTER))
        os.mkdir(os.path.join(self.workspace, POINTER))
        self.refused("faithful", "as-agreed: the pointer")

    @unittest.skipUnless(hasattr(os, "symlink") and hasattr(os, "mkfifo"), "needs links and FIFOs")
    def test_a_broken_link_or_a_fifo_at_the_pointer_denies_without_waiting(self):
        os.symlink(os.path.join(self.dir, "gone"), os.path.join(self.workspace, POINTER))
        self.refused("faithful", "cannot be read")
        os.remove(os.path.join(self.workspace, POINTER))
        os.mkfifo(os.path.join(self.workspace, POINTER))
        self.refused("faithful", "not a regular file")

    def test_a_file_named_as_the_folder_denies(self):
        self.refused("faithful", "not a folder", {ENV: os.path.join(self.record, "record.json")})

    @unittest.skipIf(ROOT_USER or not hasattr(os, "getuid"), "root reads every folder; Windows has no modes")
    def test_a_folder_that_cannot_be_read_denies(self):
        os.chmod(self.record, 0)
        self.refused("faithful", "cannot be read", {ENV: self.record})

    @unittest.skipIf(ROOT_USER or not hasattr(os, "getuid"), "root owns every file in the check; no user ids")
    def test_another_user_s_folder_is_denied_and_their_pointer_skipped(self):
        with mock.patch("os.getuid", return_value=os.getuid() + 4242):
            self.refused("faithful", "not yours", {ENV: self.record})
            self.point(self.workspace, os.path.join(self.dir, "orders", "gone"))
            self.assertEqual(locate(self.work, {}).how, "none")  # in a shared parent, not the person's word

    @unittest.skipUnless(hasattr(os, "getuid"), "no modes where the system has no user ids")
    def test_a_folder_others_can_write_is_denied(self):
        os.chmod(self.record, 0o777)
        self.refused("faithful", "others can write", {ENV: self.record})
        if os.path.isdir("/tmp") and os.stat("/tmp").st_mode & stat.S_IWOTH:  # root's, and anyone's to write
            self.refused("faithful", "others can write", {ENV: "/tmp"})

    @unittest.skipUnless(hasattr(os, "symlink"), "needs links")
    def test_the_log_is_never_written_through_a_link(self):
        victim = os.path.join(self.dir, "victim.txt")
        Path(victim).write_text("untouched\n")
        os.symlink(victim, os.path.join(self.record, "hook.jsonl"))
        self.assertTrue(denied(self.call("dropped", {ENV: self.record}))[0])
        self.assertEqual(Path(victim).read_text(), "untouched\n")

    def test_a_lookup_that_fails_stops_the_call(self):
        with mock.patch("as_agreed.hook.locate", side_effect=RuntimeError("boom")):
            self.call("faithful", {ENV: self.record}, code=2)

    def test_a_folder_without_its_binding_refuses_the_calls(self):
        os.remove(os.path.join(self.record, "binding.json"))
        self.refused("faithful", "binding.json cannot be read", {ENV: self.record})

    def test_an_unconfirmed_record_is_refused(self):
        self.set_record(confirmed_by="")
        self.refused("faithful", "not confirmed by a person", {ENV: self.record})


class TheFreeze(Tree):
    def setUp(self):
        super().setUp()
        self.pointer = self.point(self.workspace, self.record)

    def test_a_write_to_the_pointer_after_the_confirm_is_refused(self):
        other = os.path.join(self.dir, "orders", "other")
        for ev in (write(self.pointer, other), write(os.path.join(self.workspace, POINTER.upper()), other),
                   {"tool_name": "Edit", "tool_input": {"file_path": self.pointer, "old_string": "pay-invoice",
                                                        "new_string": "other"}}):
            self.refused(ev, "frozen")
        self.assertEqual(Path(self.pointer).read_text(), self.record)

    def test_a_nearer_pointer_or_a_start_folder_record_cannot_go_ahead_of_it(self):
        self.refused(write(POINTER, os.path.join(self.dir, "orders", "other")), "the lookup reads first")
        self.refused(write(os.path.join(self.work, "task", "..", POINTER), "x"), "frozen")
        binding = os.path.join(".as-agreed", "binding.json")
        self.refused(write(binding, '{"steps": []}'), "the lookup reads first")
        self.refused(write(binding, '{"steps": []}'), "the lookup reads first", {ENV: self.record})

    @unittest.skipUnless(hasattr(os, "symlink"), "needs links")
    def test_a_write_through_a_linked_folder_is_refused(self):
        os.symlink(self.workspace, os.path.join(self.work, "up"))
        self.refused(write(os.path.join("up", POINTER), "x"), "frozen")
        os.symlink(self.record, os.path.join(self.work, "rec"))
        self.refused(write(os.path.join("rec", "binding.json"), '{"steps": []}'), "frozen")

    def test_another_unicode_form_of_a_parent_is_the_same_folder(self):
        composed, decomposed = os.path.join(self.dir, "Caf\u00e9"), os.path.join(self.dir, "Cafe\u0301")
        shutil.copytree(str(FIX / "workspace"), composed)
        if not os.path.isdir(decomposed):
            self.skipTest("this disk keeps the two forms apart: they are two folders, not one")
        self.workspace, self.work = composed, os.path.join(composed, "task", "workdir")
        self.point(composed, self.record)
        other = os.path.join(decomposed, "task", "workdir")
        for path in (os.path.join(other, POINTER), os.path.join(other, ".as-agreed", "binding.json"),
                     os.path.join(decomposed, POINTER), os.path.join(decomposed.upper(), "TASK", POINTER)):
            self.refused(write(path, '{"steps": []}'), "the lookup reads first")
        self.held_and_kept()

    def test_the_located_record_and_binding_are_frozen(self):
        for name in ("record.json", "binding.json"):
            edit = {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(self.record, name),
                                                         "old_string": "18400", "new_string": "19800"}}
            self.refused(edit, "frozen")
            self.refused(edit, "frozen", {ENV: self.record})

    def test_the_agent_cannot_write_the_confirmation(self):
        self.set_record(confirmed_by="")
        rec = json.loads(Path(self.record, "record.json").read_text())
        rec["confirmed_by"] = "the agent"
        self.refused(write(os.path.join(self.record, "record.json"), json.dumps(rec)), "only the person writes")

    def test_before_the_confirm_the_pointer_may_be_repaired(self):
        self.set_record(confirmed_by="")
        self.assertIsNone(self.call(write(self.pointer, self.record)))

    def test_other_files_are_not_frozen(self):
        self.assertIsNone(self.call(write("notes.md", "a note")))
        self.assertIsNone(self.call(write(os.path.join(self.workspace, "record-notes.md"), "a note")))


class TheProjectLayout(Tree):
    """0.1.0's `.as-agreed/` in the start folder, as before: it comes first, and nothing new is frozen."""

    def setUp(self):
        super().setUp()
        shutil.copytree(str(EXAMPLE / ".as-agreed"), os.path.join(self.work, ".as-agreed"))

    def test_the_project_folder_comes_first(self):
        gone = os.path.join(self.dir, "gone")
        self.point(self.workspace, gone)
        self.held_and_kept({ENV: gone})
        self.assertTrue(os.path.isfile(os.path.join(self.work, ".as-agreed", "hook.jsonl")))
        self.assertEqual(band.calls(self.work, "example"), 2)
        self.assertFalse(os.path.exists(os.path.join(self.record, "hook.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.record, "runs")))

    def test_a_pointer_is_not_frozen_in_a_project_with_its_own_folder(self):
        self.assertIsNone(self.call(write(POINTER, self.record)))

    def test_nothing_named_leaves_the_folder_alone(self):
        shutil.rmtree(os.path.join(self.work, ".as-agreed"))
        for ev in ("faithful", "dropped"):
            self.assertIsNone(self.call(ev))
        self.assertEqual(sorted(os.listdir(self.work)), sorted(os.listdir(FIX / "workspace" / "task" / "workdir")))


@unittest.skipUnless(SH, "the plugin's command runs under sh")
class ThePluginCommand(Tree):
    """hooks/hooks.json as Claude Code runs it: the shell gate, then `python3 -m as_agreed hook` from the plugin."""

    def run_plugin(self, ev, env=None, python=sys.executable, code=0):
        bin_dir = os.path.join(self.dir, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        Path(bin_dir, "python3").write_text(f'#!/bin/sh\nexec "{python}" "$@"\n')
        os.chmod(os.path.join(bin_dir, "python3"), stat.S_IRWXU)
        command = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
        environ = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""), "CLAUDE_PROJECT_DIR": self.work,
                   "CLAUDE_PLUGIN_ROOT": str(ROOT), **(env or {})}
        p = subprocess.run([SH, "-c", command["command"]], input=json.dumps(event(ev)), env=environ, cwd=self.work,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, code, p.stderr)
        self.stderr = p.stderr
        return json.loads(p.stdout) if p.stdout.strip() else None

    def test_a_check_that_cannot_start_stops_the_call(self):
        plugin = os.path.join(self.dir, "plugin")  # the plugin's hooks, without its src folder
        shutil.copytree(str(ROOT / "hooks"), os.path.join(plugin, "hooks"))
        self.assertIsNone(self.run_plugin("faithful", {ENV: self.record, "CLAUDE_PLUGIN_ROOT": plugin}, code=2))
        self.assertIn("src folder cannot be reached", self.stderr)
        only_sh = os.path.join(self.dir, "only-sh")  # a PATH with no python3 on it
        os.makedirs(only_sh)
        os.symlink(SH, os.path.join(only_sh, "sh"))
        self.assertIsNone(self.run_plugin("faithful", {ENV: self.record, "PATH": only_sh}, code=2))
        self.assertIn("python3 exit 127", self.stderr)
        broken = os.path.join(self.dir, "broken-python")  # a python3 that fails before it decides
        Path(broken).write_text("#!/bin/sh\nexit 1\n")
        os.chmod(broken, stat.S_IRWXU)
        self.assertIsNone(self.run_plugin("faithful", {ENV: self.record}, python=broken, code=2))
        self.assertIn("python3 exit 1", self.stderr)

    @unittest.skipUnless(hasattr(os, "symlink"), "needs links")
    def test_a_dotted_start_folder_through_a_link_is_checked(self):
        shutil.copytree(str(EXAMPLE / ".as-agreed"), os.path.join(self.work, ".as-agreed"))
        os.symlink(self.record, os.path.join(self.dir, "link"))  # the shell reads link/.. as orders/, Python as <tmp>
        dotted = os.path.join(self.dir, "link", "..", "workspace", "task", "workdir")
        self.assertTrue(denied(self.run_plugin("dropped", {"CLAUDE_PROJECT_DIR": dotted}))[0])
        self.assertFalse(denied(self.run_plugin("faithful", {"CLAUDE_PROJECT_DIR": dotted}))[0])

    def test_the_plugin_checks_a_throwaway_folder_through_the_variable(self):
        self.assertTrue(denied(self.run_plugin("dropped", {ENV: self.record}))[0])
        self.assertEqual(self.run_plugin("faithful", {ENV: self.record})["systemMessage"], "as-agreed: " + FAITHFUL)

    def test_the_plugin_checks_a_throwaway_folder_through_a_pointer(self):
        self.point(self.workspace, self.record)
        self.assertTrue(denied(self.run_plugin("dropped"))[0])
        self.assertEqual(self.run_plugin("faithful")["systemMessage"], "as-agreed: " + FAITHFUL)

    def test_the_plugin_denies_a_missing_named_folder(self):
        self.assertIn("missing", denied(self.run_plugin("faithful", {ENV: os.path.join(self.dir, "gone")}))[1])

    def test_the_plugin_starts_no_python_where_nothing_is_named(self):
        marker = os.path.join(self.dir, "started")
        fake = os.path.join(self.dir, "fake-python")
        Path(fake).write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 3\n')
        os.chmod(fake, stat.S_IRWXU)
        self.assertIsNone(self.run_plugin("dropped", python=fake))
        self.assertFalse(os.path.exists(marker))


if __name__ == "__main__":
    unittest.main()
