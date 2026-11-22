"""The band and the Board page (ASAG-23): the run's flag as labels above the prompt in Claude Code, and one local page
that lists each binding term with what was agreed, what arrived and the result.

The three fixtures are what the plugin's hook writes for three calls on an invented payment of at most 18,400 USD:
kept (18000), held (19800) and unchecked (an amount in words). `test_the_fixtures_are_what_the_hook_writes` keeps them
true to the hook. They are named `<case>.ledger.jsonl` because MANIFEST.in leaves every file named `ledger.jsonl` out
of the source package."""
import contextlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from as_agreed import band  # noqa: E402
from as_agreed.__main__ import main as cli  # noqa: E402
from as_agreed.hold import flag  # noqa: E402
from as_agreed.hook import main as hook  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "band"
# the three label strings of the mock, plans/2026-11-22-product/band-and-board.html, states A, B and C
MOCK = {"kept": ["Kept as agreed 2 of 2", "held 0", "unchecked 0"],
        "held": ["Kept as agreed 1 of 2", "held 1", "unchecked 0"],
        "unchecked": ["Kept as agreed 1 of 2", "held 0", "unchecked 1"]}
WHY = {"kept": "", "held": "pay limit.amount WIDENED: 19800 where at most 18400 USD was agreed",
       "unchecked": "1 term waits for your read"}
LOADS = re.compile(r"src=|url\(|http")


def rows(name):
    return band.read_rows(str(FIX / f"{name}.ledger.jsonl"))


def event(name, session=None):
    ev = json.loads((FIX / "events" / f"{name}.json").read_text(encoding="utf-8"))
    if session:
        ev["session_id"] = session
    return ev


def steady(value):
    """A row without what changes from one run to the next: times and the run's id."""
    if isinstance(value, dict):
        return {k: steady(v) for k, v in value.items() if k not in ("ts", "run_id", "captured_at")}
    if isinstance(value, list):
        return [steady(v) for v in value]
    return value


def loads(html):
    """What `grep -c 'src=\\|url(\\|http'` counts: the lines that name a load."""
    return sum(1 for line in html.splitlines() if LOADS.search(line))


class Project:
    """A temporary copy of the fixture project, where the hook runs as Claude Code runs it."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        shutil.copytree(str(FIX / "project" / ".as-agreed"), os.path.join(self.dir, ".as-agreed"))
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.dir)

    def call(self, ev):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": self.dir}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = hook(json.dumps(ev))
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None)

    def folder(self, session):
        return band.run_folder(self.dir, session)


def denied(answer):
    return ((answer or {}).get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


class TheBand(unittest.TestCase):
    def test_each_fixture_reads_the_three_labels_of_the_mock(self):
        for name, want in MOCK.items():
            self.assertEqual(band.labels(rows(name)), want, name)

    def test_the_line_under_the_labels_is_the_mock_s(self):
        for name, want in WHY.items():
            self.assertEqual(band.state(rows(name))["why"], want, name)

    def test_the_labels_count_what_the_flag_line_counts(self):
        for name in MOCK:
            s, line = band.state(rows(name)), flag(rows(name))[0]
            self.assertTrue(line.startswith(f"Kept as agreed: {s['kept']} of {s['terms']} binding terms"), line)
            self.assertIn(f"; {s['held']} held", line)
            self.assertTrue(line.endswith(f"{s['unchecked']} unchecked"), line)

    def test_a_change_no_hold_stopped_adds_a_fourth_label(self):
        through = [{"kind": "hold", "link": "brief#1@1", "passed": False, "policy": {"on_loss": "record"}},
                   {"kind": "verdict", "unit_kind": "commitment", "must": True, "unit": "pay", "field": "limit.amount",
                    "link": "brief#1@1", "drift_class": "WIDENED", "state": "as_briefed", "reason": "r"}]
        self.assertEqual(band.labels(through), ["Kept as agreed 0 of 1", "held 0", "unchecked 0",
                                                "changed and not held 1"])
        self.assertEqual(band.state(through)["why"], "pay limit.amount WIDENED: r")

    def test_no_checked_term_reads_as_such(self):
        self.assertEqual(band.labels([]), ["No binding term checked yet"])

    def test_the_module_test_feeds_the_states_python_writes(self):
        """hooks/band/band.test.tsx feeds the band module the three states; they must stay the ones written here."""
        text = (ROOT / "hooks" / "band" / "band.test.tsx").read_text(encoding="utf-8")
        for name in MOCK:
            found = re.search(rf"^const {name.upper()} = (\{{.*\}})$", text, re.M)
            self.assertIsNotNone(found, name)
            self.assertEqual(json.loads(found.group(1)), band.state(rows(name)), name)


class TheHookWritesTheRun(unittest.TestCase):
    def test_the_fixtures_are_what_the_hook_writes(self):
        for name, want in MOCK.items():
            with Project() as p:
                p.call(event(name))
                folder = p.folder(f"fixture-{name}")
                written = band.read_rows(os.path.join(folder, "ledger.jsonl"))
                self.assertEqual(band.labels(written), want, name)
                self.assertEqual(steady(written), steady(rows(name)), name)
                with open(os.path.join(folder, "band.json"), encoding="utf-8") as f:
                    self.assertEqual(json.load(f), band.state(written))
                with open(os.path.join(folder, "board.html"), encoding="utf-8") as f:
                    self.assertEqual(loads(f.read()), 0)

    def test_a_retry_that_passes_counts_as_kept_by_the_check(self):
        with Project() as p:
            self.assertTrue(denied(p.call(event("held", "s1"))[1]))
            self.assertFalse(denied(p.call(event("kept", "s1"))[1]))
            written = band.read_rows(os.path.join(p.folder("s1"), "ledger.jsonl"))
            self.assertEqual(band.labels(written), MOCK["kept"])
            self.assertEqual(flag(written)[1], "1 of 2 terms kept that would have been lost, 18,400 USD at stake")

    def test_each_session_is_its_own_task(self):
        with Project() as p:
            p.call(event("held", "s1"))
            p.call(event("kept", "s2"))
            self.assertEqual(band.labels(band.read_rows(os.path.join(p.folder("s1"), "ledger.jsonl"))), MOCK["held"])
            self.assertEqual(band.labels(band.read_rows(os.path.join(p.folder("s2"), "ledger.jsonl"))), MOCK["kept"])

    def test_the_run_folder_stays_out_of_git_and_inside_the_project(self):
        with Project() as p:
            p.call(event("held", "../../outside"))
            folder = p.folder("../../outside")
            self.assertTrue(os.path.realpath(folder).startswith(os.path.realpath(p.dir) + os.sep))
            self.assertTrue(os.path.isfile(os.path.join(folder, "ledger.jsonl")))
            with open(os.path.join(p.dir, ".as-agreed", "runs", ".gitignore"), encoding="utf-8") as f:
                self.assertEqual(f.read().split(), ["*"])

    def test_the_hook_still_denies_when_the_run_cannot_be_written(self):
        with Project() as p:
            os.makedirs(os.path.join(p.dir, ".as-agreed"), exist_ok=True)
            Path(p.dir, ".as-agreed", "runs").write_text("a file where the folder goes")
            code, answer = p.call(event("held"))
            self.assertEqual(code, 0)
            self.assertTrue(denied(answer))

    def test_the_hook_still_denies_when_the_band_code_fails(self):
        with Project() as p, mock.patch.object(band, "write_run", side_effect=RuntimeError("boom")):
            code, answer = p.call(event("held"))
            self.assertEqual(code, 0)
            self.assertTrue(denied(answer))

    def test_a_call_no_step_names_writes_no_run(self):
        with Project() as p:
            p.call({"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "ls"}})
            self.assertFalse(os.path.exists(os.path.join(p.dir, ".as-agreed", "runs")))


POSIX = os.name == "posix"


@unittest.skipUnless(POSIX, "links, modes and FIFOs as a POSIX disk has them")
class TheRunFolderIsTheHooksOwn(unittest.TestCase):
    """The agent can write in the project; the hook runs as the person. A link the agent plants in the run folder
    must not carry the hook's writes out of it (security review of ASAG-23, findings 1 and 2)."""

    def setUp(self):
        self.outside = tempfile.mkdtemp()
        self.target = os.path.join(self.outside, "profile")
        self.reset()

    def tearDown(self):
        shutil.rmtree(self.outside)

    def reset(self):
        """Each case starts from an untouched outside folder: one file, a line the person wrote."""
        for name in os.listdir(self.outside):
            path = os.path.join(self.outside, name)
            shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.remove(path)
        Path(self.target).write_text("untouched\n")

    def held_call(self, p, session="fixture-held"):
        code, answer = p.call(event("held", session))
        self.assertEqual(code, 0)
        self.assertTrue(denied(answer))

    def assert_outside_untouched(self):
        self.assertEqual(Path(self.target).read_text(), "untouched\n")
        self.assertEqual(sorted(os.listdir(self.outside)), ["profile"])

    def plant(self, p, name, to=None):
        folder = p.folder("fixture-held")
        os.makedirs(folder, exist_ok=True)
        os.symlink(to or self.target, os.path.join(folder, name))
        return folder

    def test_a_link_at_any_file_of_the_run_is_not_written_through(self):
        for name in ("board.html.tmp", "band.json.tmp", "board.html", "band.json", "ledger.jsonl"):
            self.reset()
            with self.subTest(name=name), Project() as p:
                self.plant(p, name)
                self.held_call(p)
                self.assert_outside_untouched()

    def test_a_link_at_the_gitignore_is_not_written_through(self):
        for to in (self.target, os.path.join(self.outside, "made-by-the-hook")):  # an existing file, a dangling link
            self.reset()
            with self.subTest(to=to), Project() as p:
                runs = os.path.join(p.dir, ".as-agreed", "runs")
                os.makedirs(runs)
                os.symlink(to, os.path.join(runs, ".gitignore"))
                self.held_call(p)
                self.assert_outside_untouched()

    def test_a_linked_runs_folder_or_run_folder_gets_nothing(self):
        for linked in ("runs", "run"):
            self.reset()
            with self.subTest(linked=linked), Project() as p:
                runs = os.path.join(p.dir, ".as-agreed", "runs")
                if linked == "runs":
                    os.symlink(self.outside, runs)
                else:
                    os.makedirs(runs)
                    os.symlink(self.outside, p.folder("fixture-held"))
                self.held_call(p)
                self.assert_outside_untouched()

    def test_a_linked_as_agreed_folder_is_checked_but_gets_no_run(self):
        with Project() as p:
            real = os.path.join(self.outside, "real")
            shutil.move(os.path.join(p.dir, ".as-agreed"), real)
            os.symlink(real, os.path.join(p.dir, ".as-agreed"))
            self.held_call(p)
            self.assertFalse(os.path.exists(os.path.join(real, "runs")))

    def test_a_fifo_at_the_ledger_does_not_stall_the_check(self):
        import signal
        with Project() as p:
            folder = p.folder("fixture-held")
            os.makedirs(folder)
            os.mkfifo(os.path.join(folder, "ledger.jsonl"))
            signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TimeoutError("the hook stalled")))
            signal.alarm(5)
            try:
                self.held_call(p)
            finally:
                signal.alarm(0)

    def test_the_run_is_readable_by_the_person_alone(self):
        with Project() as p:
            self.held_call(p)
            folder = p.folder("fixture-held")
            mode = lambda path: stat.S_IMODE(os.lstat(path).st_mode)  # noqa: E731
            self.assertEqual(mode(os.path.dirname(folder)), 0o700)
            self.assertEqual(mode(folder), 0o700)
            for name in ("ledger.jsonl", "band.json", "board.html"):
                self.assertEqual(mode(os.path.join(folder, name)), 0o600, name)
            self.assertEqual(mode(os.path.join(os.path.dirname(folder), ".gitignore")), 0o600)
            self.assertEqual(sorted(os.listdir(folder)), ["band.json", "board.html", "ledger.jsonl"])  # no temp left

    def test_the_count_of_calls_reads_no_link(self):
        with Project() as p:
            self.held_call(p, "s1")
            ledger = os.path.join(p.folder("s1"), "ledger.jsonl")
            self.assertEqual(band.calls(p.dir, "s1"), 1)
            os.makedirs(p.folder("s2"))
            os.symlink(ledger, os.path.join(p.folder("s2"), "ledger.jsonl"))
            self.assertEqual(band.calls(p.dir, "s2"), 0)


class TheRunFolderByPath(TheRunFolderIsTheHooksOwn):
    """The same cases where the system has no folder handles (Windows): each folder is checked with lstat."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(band, "_AT", False)
        patcher.start()
        self.addCleanup(patcher.stop)


class TheBoard(unittest.TestCase):
    def test_the_page_loads_nothing(self):
        for name in MOCK:
            self.assertEqual(loads(band.board_html(rows(name))), 0, name)

    def test_the_held_page_lists_the_lost_term_with_agreed_and_arrived(self):
        html = band.board_html(rows("held"))
        lost = re.findall(r'<tr class="lost">(.*?)</tr>', html, re.S)
        self.assertEqual(len(lost), 1)
        self.assertIn("pay limit.amount", lost[0])
        self.assertIn("at most 18,400 USD", lost[0])  # agreed
        self.assertIn("19800", lost[0])               # arrived
        self.assertIn("Lost, held", lost[0])
        self.assertIn("1 term lost in this task", html)

    def test_the_kept_page_marks_nothing_lost(self):
        html = band.board_html(rows("kept"))
        self.assertNotIn('class="lost"', html)
        self.assertIn("18000", html)
        self.assertIn("Nothing was lost in this task", html)

    def test_the_unchecked_page_says_the_term_waits(self):
        html = band.board_html(rows("unchecked"))
        self.assertNotIn('class="lost"', html)
        self.assertIn("about eighteen thousand", html)
        self.assertIn("Unchecked: code could not decide; waits for your read", html)

    def test_text_from_the_ledger_is_escaped(self):
        rs = rows("held")
        for r in rs:
            if r.get("kind") == "verdict" and r.get("field") == "limit.amount":
                r["arrived"] = "<script>alert(1)</script>"
        html = band.board_html(rs)
        self.assertNotIn("<script", html)
        self.assertIn("&lt;script&gt;", html)

    def test_the_command_writes_the_page_and_prints_the_state(self):
        with tempfile.TemporaryDirectory() as d:
            page = os.path.join(d, "board.html")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli(["band", str(FIX / "held.ledger.jsonl"), "--board", page]), 0)
            self.assertEqual(json.loads(out.getvalue())["labels"], MOCK["held"])
            with open(page, encoding="utf-8") as f:
                self.assertEqual(loads(f.read()), 0)

    def test_the_button_s_page_is_built_fresh_in_a_private_file(self):
        """The Board button opens the page this command builds at click time, never the run folder's board.html,
        which the agent can rewrite (security review of ASAG-23, finding 4)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli(["band", str(FIX / "held.ledger.jsonl"), "--board-temp"]), 0)
        page = json.loads(out.getvalue())["board"]
        try:
            self.assertTrue(os.path.isabs(page) and page.endswith(".html"))
            self.assertNotEqual(os.path.dirname(page), str(FIX))
            if POSIX:
                self.assertEqual(stat.S_IMODE(os.lstat(page).st_mode), 0o600)
            with open(page, encoding="utf-8") as f:
                html = f.read()
            self.assertEqual(loads(html), 0)
            self.assertIn("at most 18,400 USD", html)
        finally:
            os.remove(page)

    @unittest.skipUnless(POSIX, "links as a POSIX disk has them")
    def test_the_button_s_page_is_not_built_from_a_linked_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            link = os.path.join(d, "ledger.jsonl")
            os.symlink(str(FIX / "held.ledger.jsonl"), link)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(cli(["band", link, "--board-temp"]), 2)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("not a regular file", err.getvalue())


if __name__ == "__main__":
    unittest.main()
