"""Capture in one step (0.1.3): `said`, `as-agreed show`, `as-agreed confirm`, and what the hook does after a confirm.

- each term may carry `said`, the person's words for it, verbatim from the record's `order`;
- `show` marks a quote not in the order, a value that does not match its words (numbers, dates, currency, name tokens),
  and a number or date with no words;
- `confirm` reads the person's name from the terminal (`/dev/tty`), never from stdin, and writes `confirmed_by`,
  `confirmed_at`, `confirmed_sha256` and `binding_sha256`; with no terminal it writes nothing;
- the hook refuses a file tool that writes any of those four fields, stops every call while the record or the binding
  no longer matches its hash, and denies an agent's call that runs confirm;
- a record confirmed by hand (0.1.0 to 0.1.2, no hashes) works as before.

The records are invented: `examples/capture/` (one planted wrong date) and `tests/fixtures/capture/twelve.json` (12
terms, two judged by a person, one planted wrong amount)."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from as_agreed import capture  # noqa: E402
from as_agreed.hook import decide, main as hook_main  # noqa: E402

EXAMPLE = ROOT / "examples" / "capture"
PLUGIN_HOLD = ROOT / "examples" / "plugin-hold"
TWELVE = ROOT / "tests" / "fixtures" / "capture" / "twelve.json"
MISMATCH, NO_WORDS, NOT_IN_ORDER = "does not match its words", "no words", "not in the order"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def found(rec):
    return [(m.unit, m.path, m.kind) for m in capture.marks(rec)]


def booking(total=9200, guests=14):
    return {"session_id": "t", "tool_name": "Write",
            "tool_input": {"file_path": "project/outbox/booking.json",
                           "content": json.dumps({"guests": guests, "arrive": "2031-03-12", "leave": "2031-03-14",
                                                  "total": total})}}


def bash(command):
    return {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": command}}


class Project(unittest.TestCase):
    """A copy of examples/capture in a temporary project folder."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.folder = os.path.join(self.dir, ".as-agreed")
        shutil.copytree(str(EXAMPLE / ".as-agreed"), self.folder)
        self.record = os.path.join(self.folder, "record.json")
        self.binding = os.path.join(self.folder, "binding.json")

    def rec(self):
        return load(self.record)

    def edit(self, change, path=None):
        """A change made outside the file tools (a script the agent's shell runs)."""
        path = path or self.record
        data = load(path)
        change(data)
        Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")

    def fix_the_date(self):
        self.edit(lambda r: r["commitments"][2].update(deadline="2031-02-20"))

    def confirmed(self, name="Ada"):
        self.fix_the_date()
        capture.write_confirm(self.folder, name)
        return self.rec()


class TheMarks(unittest.TestCase):
    def test_the_example_marks_only_its_planted_date(self):
        self.assertEqual(found(load(EXAMPLE / ".as-agreed" / "record.json")), [("deposit", "deadline", MISMATCH)])

    def test_the_twelve_term_analogue_marks_only_its_planted_amount(self):
        rec = load(TWELVE)
        self.assertEqual(len(rec["commitments"]), 12)
        judged = [c for c in rec["commitments"] if any(a.get("verification") == "human_judged"
                                                       for a in c.get("acceptance_criteria") or [])]
        self.assertEqual(len(judged), 2)
        self.assertEqual(found(rec), [("seeds", "limit.spend", MISMATCH)])

    def change(self, cid, **kw):
        rec = load(EXAMPLE / ".as-agreed" / "record.json")
        rec["commitments"][2]["deadline"] = "2031-02-20"  # the planted date fixed: only the new plant is marked
        c = next(c for c in rec["commitments"] if c["commitment_id"] == cid)
        for k, v in kw.items():
            if k == "said":
                c["said"].update(v)
            elif v is None:
                c["said"].pop(k, None)
            else:
                c[k] = v
        return found(rec)

    def test_the_fixed_example_has_no_mark(self):
        self.assertEqual(self.change("deposit"), [])

    def test_a_quote_not_in_the_order_is_marked(self):
        self.assertIn(("spend", "limit.spend", NOT_IN_ORDER), self.change("spend", said={"limit.spend": "at most 9,500 EUR"}))

    def test_a_number_that_does_not_match_its_words_is_marked(self):
        self.assertEqual(self.change("spend", limit={"spend": {"max": 9800, "unit": "EUR"}}),
                         [("spend", "limit.spend", MISMATCH)])

    def test_a_date_that_does_not_match_its_words_is_marked(self):
        self.assertEqual(self.change("book-offsite", limit={"guests": {"max": 14}, "arrive": {"from": "2031-03-13"},
                                                            "leave": {"until": "2031-03-14"}}),
                         [("book-offsite", "limit.arrive", MISMATCH)])

    def test_a_currency_that_does_not_match_its_words_is_marked(self):
        self.assertEqual(self.change("spend", limit={"spend": {"max": 9500, "unit": "USD"}}),
                         [("spend", "limit.spend", MISMATCH)])

    def test_a_name_token_that_does_not_match_its_words_is_marked(self):
        self.assertEqual(self.change("book-offsite", obligee="harbor-inn"), [("book-offsite", "obligee", MISMATCH)])
        self.assertEqual(self.change("book-offsite", act="book the team offsite at Harbor Inn"),
                         [("book-offsite", "act", MISMATCH)])

    def test_a_number_or_a_date_with_no_words_is_marked(self):
        self.assertEqual(self.change("book-offsite", **{"limit.guests": None}), [("book-offsite", "limit.guests", NO_WORDS)])
        self.assertEqual(self.change("deposit", deadline=None), [("deposit", "deadline", NO_WORDS)])

    def test_a_name_with_no_words_is_shown_but_not_marked(self):
        self.assertEqual(self.change("book-offsite", obligee=None), [])  # the words for obligee removed

    def test_a_record_with_no_said_marks_each_number_and_date(self):
        rec = load(PLUGIN_HOLD / ".as-agreed" / "record.json")
        self.assertEqual(found(rec), [("pay-invoice", "limit.amount", NO_WORDS)])


class TheShowCommand(unittest.TestCase):
    def cli(self, *args, cwd=None):
        env = {k: v for k, v in os.environ.items() if k not in ("AS_AGREED_RECORD", "CLAUDE_PROJECT_DIR")}
        env["PYTHONPATH"] = str(ROOT / "src")
        p = subprocess.run([sys.executable, "-m", "as_agreed", *args], cwd=cwd or str(ROOT), env=env,
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout + p.stderr

    def test_show_prints_each_term_with_its_words_and_its_mark(self):
        code, out = self.cli("show", str(EXAMPLE))
        self.assertEqual(code, 0, out)
        for line in ("deadline", "2031-02-28", '"by 20 February 2031"', "limit.spend", '"at most 9,500 euros"',
                     "obligor", "(no words)", "judged by a person: every room is on a quiet floor"):
            self.assertIn(line, out)
        self.assertIn("MARK " + MISMATCH, out)
        self.assertIn("4 commitments", out)
        self.assertIn("1 mark", out)
        self.assertIn("as-agreed confirm", out)

    def test_show_finds_the_record_in_the_start_folder(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(str(EXAMPLE / ".as-agreed"), os.path.join(d, ".as-agreed"))
            code, out = self.cli("show", cwd=d)
        self.assertEqual(code, 0, out)
        self.assertIn("1 mark", out)

    def test_show_with_no_record_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = self.cli("show", cwd=d)
        self.assertEqual(code, 2, out)


class TheConfirm(Project):
    def terminal(self, typed):
        """A pseudo-terminal the person types `typed` into; its output is drained so confirm never blocks."""
        master, slave = os.openpty()
        path = os.ttyname(slave)
        os.write(master, typed.encode("utf-8"))
        shown = []

        def drain():
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                shown.append(chunk)

        t = threading.Thread(target=drain, daemon=True)
        t.start()
        self.addCleanup(lambda: (os.close(slave), os.close(master)))
        return path, shown

    def test_with_no_terminal_confirm_refuses_and_writes_nothing(self):
        before = Path(self.record).read_bytes()
        code, text = capture.confirm(self.folder, tty=os.path.join(self.dir, "no-such-terminal"))
        self.assertEqual(code, 2)
        self.assertIn("no terminal", text)
        self.assertEqual(Path(self.record).read_bytes(), before)

    def test_the_command_run_with_no_terminal_never_reads_a_name_piped_on_stdin(self):
        before = Path(self.record).read_bytes()
        env = {k: v for k, v in os.environ.items() if k not in ("AS_AGREED_RECORD", "CLAUDE_PROJECT_DIR")}
        env["PYTHONPATH"] = str(ROOT / "src")
        p = subprocess.run([sys.executable, "-m", "as_agreed", "confirm", self.folder], input="Mallory\n",
                           capture_output=True, text=True, env=env, timeout=60, start_new_session=True)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("no terminal", p.stdout + p.stderr)
        self.assertEqual(Path(self.record).read_bytes(), before)

    @unittest.skipUnless(hasattr(os, "openpty"), "needs a pseudo-terminal")
    def test_confirm_on_a_terminal_writes_the_four_fields(self):
        self.fix_the_date()
        path, shown = self.terminal("Ada Lovelace\n")
        with mock.patch.object(sys, "stdin", io.StringIO("Mallory\n")):
            code, text = capture.confirm(self.folder, tty=path)
        self.assertEqual(code, 0, text)
        rec = self.rec()
        self.assertEqual(rec["confirmed_by"], "Ada Lovelace")
        self.assertRegex(rec["confirmed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
        self.assertEqual(rec["confirmed_sha256"], capture.record_sha256(rec))
        self.assertEqual(rec["binding_sha256"], capture.binding_sha256(load(self.binding)))
        self.assertNotIn("Mallory", json.dumps(rec))

    @unittest.skipUnless(hasattr(os, "openpty"), "needs a pseudo-terminal")
    def test_confirm_shows_the_marks_before_it_asks(self):
        path, shown = self.terminal("Ada\n")
        capture.confirm(self.folder, tty=path)
        # the planted date is still in: the person saw its mark on the terminal before typing the name
        text = b"".join(shown).decode("utf-8", "replace")
        self.assertIn("MARK " + MISMATCH, text)
        self.assertLess(text.index("MARK"), text.index("Type your name"))

    @unittest.skipUnless(hasattr(os, "openpty"), "needs a pseudo-terminal")
    def test_an_empty_name_writes_nothing(self):
        before = Path(self.record).read_bytes()
        path, _ = self.terminal("\n")
        code, text = capture.confirm(self.folder, tty=path)
        self.assertEqual(code, 2, text)
        self.assertEqual(Path(self.record).read_bytes(), before)

    @unittest.skipUnless(hasattr(os, "openpty"), "needs a pseudo-terminal")
    def test_a_record_changed_while_the_person_reads_it_is_not_confirmed(self):
        path, _ = self.terminal("Ada\n")
        real = capture._read_line

        def read_and_change(fd):
            name = real(fd)
            self.edit(lambda r: r["commitments"][1]["limit"]["spend"].update(max=95000))
            return name

        with mock.patch.object(capture, "_read_line", read_and_change):
            code, text = capture.confirm(self.folder, tty=path)
        self.assertEqual(code, 2, text)
        self.assertIn("changed while", text)
        self.assertNotIn("confirmed_by", self.rec())


class TheHookAfterCapture(Project):
    def call(self, event):
        return decide(event, self.dir)

    def test_a_record_with_said_is_checked_as_before(self):
        self.confirmed()
        decision, lines, _ = self.call(booking())
        self.assertEqual(decision, "pass", lines)
        self.assertTrue(lines[0].startswith("Kept as agreed: 4 of 4 binding terms"), lines[0])
        decision, lines, held = self.call(booking(total=9800))
        self.assertEqual(decision, "hold", lines)
        self.assertEqual(held, ["spend limit.spend WIDENED"])

    def test_an_unconfirmed_record_names_the_confirm_step(self):
        decision, lines, _ = self.call(booking())
        self.assertEqual(decision, "refused")
        self.assertIn("not confirmed by a person", lines[0])
        self.assertIn("as-agreed confirm", lines[0])

    def test_a_file_tool_that_writes_a_confirm_field_is_refused_before_and_after_a_confirm(self):
        for stage in ("before", "after"):
            if stage == "after":
                self.confirmed()
            for name in capture.CONFIRM_FIELDS:
                rec = dict(self.rec(), **{name: "x"})
                for ev in ({"tool_name": "Write", "tool_input": {"file_path": self.record, "content": json.dumps(rec)}},
                           {"tool_name": "Edit", "tool_input": {"file_path": self.record, "old_string": '"order"',
                                                                "new_string": f'"{name}": "x", "order"'}}):
                    decision, lines, _ = self.call(ev)
                    self.assertEqual(decision, "refused", (stage, name, ev["tool_name"]))
                    self.assertIn("only the person writes confirmed_by", lines[0])

    def test_a_draft_may_still_hold_the_empty_fields(self):
        rec = dict(self.rec(), confirmed_by="", confirmed_at="")
        decision, _, _ = self.call({"tool_name": "Write", "tool_input": {"file_path": self.record,
                                                                        "content": json.dumps(rec)}})
        self.assertEqual(decision, "unbound")

    def test_a_record_changed_after_the_confirm_stops_the_next_call(self):
        self.confirmed()
        self.edit(lambda r: r["commitments"][1]["limit"]["spend"].update(max=95000))
        for ev in (booking(), bash("ls"), {"tool_name": "Read", "tool_input": {"file_path": self.record}}):
            decision, lines, _ = self.call(ev)
            self.assertEqual(decision, "refused", ev["tool_name"])
            self.assertIn("record.json changed after the person confirmed it", lines[0])

    def test_a_binding_changed_after_the_confirm_stops_the_next_call(self):
        """Dropping the step would otherwise let the booking through unchecked."""
        self.confirmed()
        self.edit(lambda b: b.update(steps=[]), path=self.binding)
        decision, lines, _ = self.call(booking(total=99000))
        self.assertEqual(decision, "refused")
        self.assertIn("binding.json changed after the person confirmed it", lines[0])

    def test_a_hash_removed_after_the_confirm_stops_the_next_call(self):
        self.confirmed()
        self.edit(lambda r: r.pop("binding_sha256"))
        self.assertEqual(self.call(booking())[0], "refused")

    def test_a_new_confirm_lifts_the_stop(self):
        self.confirmed()
        self.edit(lambda r: r["commitments"][1]["limit"]["spend"].update(max=9600))
        self.assertEqual(self.call(booking())[0], "refused")
        capture.write_confirm(self.folder, "Ada")
        self.assertEqual(self.call(booking())[0], "pass")

    def test_an_agent_call_that_runs_confirm_is_denied(self):
        commands = ("as-agreed confirm", "python3 -m as_agreed confirm .as-agreed", "cd x && as-agreed confirm",
                    "/usr/local/bin/as-agreed confirm", "python -m as_agreed confirm", "script -q /dev/null as-agreed confirm",
                    "python3 -c 'from as_agreed.capture import confirm; confirm()'")
        for stage in ("unconfirmed", "confirmed"):
            if stage == "confirmed":
                self.confirmed()
            for c in commands:
                decision, lines, _ = self.call(bash(c))
                self.assertEqual(decision, "refused", (stage, c))
                self.assertIn("only the person runs as-agreed confirm", lines[0])
        for c in ("as-agreed show", "grep confirmed_by .as-agreed/record.json", "echo confirm"):
            self.assertNotEqual(self.call(bash(c))[0], "refused", c)

    def test_an_agent_call_that_runs_confirm_is_denied_with_no_record(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": d}), contextlib.redirect_stdout(out):
                os.environ.pop("AS_AGREED_RECORD", None)  # patch.dict puts it back
                self.assertEqual(hook_main(json.dumps(bash("as-agreed confirm"))), 0)
        answer = json.loads(out.getvalue())
        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "deny")


class TheOldRecords(unittest.TestCase):
    def test_a_record_confirmed_by_hand_works_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(str(PLUGIN_HOLD / ".as-agreed"), os.path.join(d, ".as-agreed"))
            ev = lambda name: load(PLUGIN_HOLD / "events" / f"{name}.json")  # noqa: E731
            self.assertEqual(decide(ev("dropped"), d)[0], "hold")
            decision, lines, _ = decide(ev("faithful"), d)
            self.assertEqual(decision, "pass", lines)
            self.assertIsNone(capture.stale(os.path.join(d, ".as-agreed"), load(PLUGIN_HOLD / ".as-agreed" / "record.json")))


class TheDemo(unittest.TestCase):
    def test_the_package_carries_the_example_byte_equal(self):
        packaged = ROOT / "src" / "as_agreed" / "examples" / "capture" / ".as-agreed"
        for name in ("record.json", "binding.json"):
            self.assertEqual((EXAMPLE / ".as-agreed" / name).read_bytes(), (packaged / name).read_bytes(), name)

    def test_the_capture_demo_reads_in_a_short_page(self):
        env = {k: v for k, v in os.environ.items() if k not in ("AS_AGREED_RECORD", "CLAUDE_PROJECT_DIR")}
        env["PYTHONPATH"] = str(ROOT / "src")
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run([sys.executable, "-m", "as_agreed", "demo", "--capture"], cwd=d, env=env,
                               capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        out = p.stdout
        self.assertIn("MARK " + MISMATCH, out)
        self.assertIn("0 marks", out)
        self.assertIn("record.json changed after the person confirmed it", out)
        self.assertLessEqual(len(out.splitlines()), 45, out)
        self.assertLessEqual(len(out.split()), 450, "a page a person reads in under a minute")


if __name__ == "__main__":
    unittest.main()
