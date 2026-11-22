"""The plugin example ships in the wheel (0.1.2), so a first run starts with `pip install as-agreed`.

`examples/plugin-hold` stays at the repository root, where the release gate reads it; its copy in
`src/as_agreed/examples/plugin-hold` is the one the wheel carries, and the two are byte-equal. `as-agreed example
plugin-hold <dir>` copies the packaged example into a new or empty folder; the hook then denies its dropped event and
passes its faithful one. A folder that holds anything else is refused, so a confirmed record is never written over a
person's own `.as-agreed/`. With it, `quantity.figures()` reads a run of thousands of digits without an error."""
import glob
import json
import os
import re
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

import as_agreed  # noqa: E402
from as_agreed import example  # noqa: E402
from as_agreed.quantity import figures  # noqa: E402

PACKAGE = Path(as_agreed.__file__).resolve().parent
PACKAGED = PACKAGE / "examples" / "plugin-hold"
AT_ROOT = ROOT / "examples" / "plugin-hold"
FILES = [".as-agreed/binding.json", ".as-agreed/record.json", "events/dropped.json", "events/faithful.json",
         "events/widened.json"]
FAITHFUL = "Kept as agreed: 3 of 3 binding terms, checked at delivery; 0 held; 0 unchecked"
ROOT_USER = hasattr(os, "getuid") and os.getuid() == 0


def example_files(folder):
    """The example's files under `folder`: its record and binding, and its events (what a run of the hook adds there,
    `hook.jsonl` and `runs/`, is not the example)."""
    return sorted(os.path.relpath(p, folder).replace(os.sep, "/")
                  for pattern in (".as-agreed/*.json", "events/*.json") for p in glob.glob(os.path.join(folder, pattern)))


def package_data():
    """The patterns of `[tool.setuptools.package-data]` for as_agreed (read by hand: 3.9 has no tomllib)."""
    text = (ROOT / "pyproject.toml").read_text()
    block = re.search(r"^\[tool\.setuptools\.package-data\]\s*\nas_agreed\s*=\s*\[(.*?)\]", text, re.M | re.S)
    return re.findall(r'"([^"]+)"', block.group(1))


def tree(folder):
    """Every file and folder under `folder`, relative, links included as names (os.walk does not follow them)."""
    out = []
    for d, dirs, files in os.walk(folder):
        out += [os.path.relpath(os.path.join(d, n), folder).replace(os.sep, "/") for n in dirs + files]
    return sorted(out)


class ThePackagedExample(unittest.TestCase):
    def test_the_package_holds_the_example(self):
        self.assertEqual(example_files(PACKAGED), FILES)
        self.assertEqual([p for p in tree(PACKAGED) if p not in (".as-agreed", "events")], FILES)

    def test_the_two_copies_are_byte_equal(self):
        self.assertEqual(example_files(AT_ROOT), FILES)
        for f in FILES:
            self.assertEqual((AT_ROOT / f).read_bytes(), (PACKAGED / f).read_bytes(), f)

    def test_the_package_data_takes_every_file_of_every_packaged_example(self):
        """setuptools expands package-data with glob, where `*` skips a dot folder: `.as-agreed` needs its own."""
        taken = {os.path.relpath(p, PACKAGE) for pattern in package_data()
                 for p in glob.glob(os.path.join(str(PACKAGE), pattern)) if os.path.isfile(p)}
        every = {os.path.relpath(os.path.join(d, f), PACKAGE) for d, dirs, files in os.walk(PACKAGE / "examples")
                 for f in files if "__pycache__" not in d and f != ".DS_Store"}
        self.assertEqual(sorted(every - taken), [])
        self.assertTrue({os.path.join("examples", "plugin-hold", *f.split("/")) for f in FILES} <= taken)

    def test_the_command_reads_the_package_not_the_clone(self):
        self.assertEqual(Path(example.source("plugin-hold")).resolve(), PACKAGED)


class TheCopyCommand(unittest.TestCase):
    """`python -m as_agreed example plugin-hold <dir>`, run as a person runs it, from a folder of its own."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.env = {k: v for k, v in os.environ.items() if k not in ("AS_AGREED_RECORD", "CLAUDE_PROJECT_DIR")}
        self.env["PYTHONPATH"] = str(PACKAGE.parent)

    def tearDown(self):
        for d, dirs, files in os.walk(self.dir):
            for n in dirs:
                if not os.path.islink(os.path.join(d, n)):
                    os.chmod(os.path.join(d, n), stat.S_IRWXU)
        shutil.rmtree(self.dir)

    def cli(self, *args, code=0, cwd=None):
        p = subprocess.run([sys.executable, "-m", "as_agreed", *args], cwd=cwd or self.dir, env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, code, p.stdout + p.stderr)
        return p.stdout + p.stderr

    def hook(self, folder, name):
        env = dict(self.env, CLAUDE_PROJECT_DIR=folder)
        p = subprocess.run([sys.executable, "-m", "as_agreed", "hook"], cwd=folder, env=env, capture_output=True,
                           text=True, timeout=60, input=Path(folder, "events", f"{name}.json").read_text())
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def mine(self, name="mine"):
        """A folder of the person's own, with their own confirmed record."""
        folder = os.path.join(self.dir, name)
        os.makedirs(os.path.join(folder, ".as-agreed"))
        Path(folder, ".as-agreed", "record.json").write_text('{"order": "their own", "confirmed_by": "them"}')
        return folder

    def test_the_copy_holds_the_drop_and_passes_the_faithful_payment(self):
        out = self.cli("example", "plugin-hold", "ex")
        ex = os.path.join(self.dir, "ex")
        self.assertIn("copied the plugin-hold example", out)
        self.assertEqual(tree(ex), [".as-agreed"] + FILES[:2] + ["events"] + FILES[2:])
        for f in FILES:
            self.assertEqual(Path(ex, f).read_bytes(), (PACKAGED / f).read_bytes(), f)
        dropped = self.hook(ex, "dropped")["hookSpecificOutput"]
        self.assertEqual(dropped["permissionDecision"], "deny")
        self.assertIn("limit.amount DROPPED", dropped["permissionDecisionReason"])
        faithful = self.hook(ex, "faithful")
        self.assertNotIn("hookSpecificOutput", faithful)
        self.assertEqual(faithful["systemMessage"], "as-agreed: " + FAITHFUL)

    def test_an_absolute_path_and_an_empty_folder_take_the_copy(self):
        empty, elsewhere = os.path.join(self.dir, "empty"), os.path.join(self.dir, "elsewhere")
        os.mkdir(empty)
        os.mkdir(elsewhere)
        self.cli("example", "plugin-hold", empty, cwd=elsewhere)
        self.assertEqual(example_files(empty), FILES)

    def test_a_folder_that_is_not_empty_is_refused_and_left_as_it_was(self):
        for name, plant in (("record", None), ("file", "notes.txt"), ("hidden", ".env"), ("folder", "sub/")):
            folder = self.mine(name) if plant is None else os.path.join(self.dir, name)
            if plant is not None:
                os.makedirs(folder)
                (os.makedirs(os.path.join(folder, plant)) if plant.endswith("/")
                 else Path(folder, plant).write_text("theirs"))
            before = {p: (Path(folder, p).read_bytes() if os.path.isfile(os.path.join(folder, p)) else None)
                      for p in tree(folder)}
            out = self.cli("example", "plugin-hold", folder, code=2)
            self.assertIn("is not empty", out, name)
            after = {p: (Path(folder, p).read_bytes() if os.path.isfile(os.path.join(folder, p)) else None)
                     for p in tree(folder)}
            self.assertEqual(after, before, name)

    def test_a_second_copy_into_the_same_folder_writes_nothing(self):
        """The gate runs the command once per interpreter in one folder: the second finds the example and stops."""
        self.cli("example", "plugin-hold", "ex")
        ex = os.path.join(self.dir, "ex")
        stamps = {f: os.stat(os.path.join(ex, f)).st_mtime_ns for f in FILES}
        out = self.cli("example", "plugin-hold", "ex")
        self.assertIn("already holds the plugin-hold example; nothing written", out)
        self.assertEqual({f: os.stat(os.path.join(ex, f)).st_mtime_ns for f in FILES}, stamps)

    def test_a_copy_that_differs_by_one_byte_or_one_file_is_refused(self):
        self.cli("example", "plugin-hold", "ex")
        ex = os.path.join(self.dir, "ex")
        self.hook(ex, "dropped")  # the hook's log and run now sit beside the record
        self.assertIn("is not empty", self.cli("example", "plugin-hold", "ex", code=2))
        self.cli("example", "plugin-hold", "ex2")
        Path(self.dir, "ex2", ".as-agreed", "record.json").write_text('{"order": "changed"}')
        self.assertIn("is not empty", self.cli("example", "plugin-hold", "ex2", code=2))
        self.assertEqual(Path(self.dir, "ex2", ".as-agreed", "record.json").read_text(), '{"order": "changed"}')

    @unittest.skipUnless(hasattr(os, "symlink"), "needs links")
    def test_a_link_at_the_name_is_refused_and_nothing_is_written_through_it(self):
        target = os.path.join(self.dir, "target")
        os.mkdir(target)
        os.symlink(target, os.path.join(self.dir, "ex"))
        self.assertIn("is a link", self.cli("example", "plugin-hold", "ex", code=2))
        self.assertEqual(os.listdir(target), [])
        mine = self.mine()
        os.symlink(os.path.join(mine, ".as-agreed"), os.path.join(self.dir, "empty-but-a-link"))
        self.cli("example", "plugin-hold", "empty-but-a-link", code=2)
        self.assertEqual(sorted(os.listdir(os.path.join(mine, ".as-agreed"))), ["record.json"])

    @unittest.skipUnless(hasattr(os, "symlink"), "needs links")
    def test_a_link_planted_after_the_check_is_never_written_through(self):
        """A folder found empty, then a `.as-agreed` link to the person's own record planted before the copy writes:
        the copy makes each folder and file new, so it stops at the link and their record is unchanged."""
        mine = self.mine()
        theirs = Path(mine, ".as-agreed", "record.json").read_bytes()
        ex = os.path.join(self.dir, "ex")
        os.mkdir(ex)
        os.symlink(os.path.join(mine, ".as-agreed"), os.path.join(ex, ".as-agreed"))
        with mock.patch.object(example, "_entries", return_value=[]):
            code, text = example.copy("plugin-hold", ex)
        self.assertEqual(code, 2, text)
        self.assertEqual(sorted(os.listdir(os.path.join(mine, ".as-agreed"))), ["record.json"])
        self.assertEqual(Path(mine, ".as-agreed", "record.json").read_bytes(), theirs)
        os.unlink(os.path.join(ex, ".as-agreed"))
        Path(ex, "events").mkdir()
        os.symlink(os.path.join(mine, ".as-agreed", "binding.json"), os.path.join(ex, "events", "dropped.json"))
        with mock.patch.object(example, "_entries", return_value=[]):
            self.assertEqual(example.copy("plugin-hold", ex)[0], 2)
        self.assertFalse(os.path.lexists(os.path.join(mine, ".as-agreed", "binding.json")))

    def test_a_file_at_the_name_is_refused(self):
        Path(self.dir, "ex").write_text("theirs")
        self.assertIn("is not a folder", self.cli("example", "plugin-hold", "ex", code=2))
        self.assertEqual(Path(self.dir, "ex").read_text(), "theirs")

    def test_a_missing_folder_above_is_refused(self):
        self.assertIn("is missing", self.cli("example", "plugin-hold", os.path.join("no", "such", "ex"), code=2))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "no")))

    @unittest.skipIf(not hasattr(os, "getuid") or ROOT_USER, "needs user ids, and not root")
    def test_an_empty_folder_others_can_write_is_refused(self):
        shared = os.path.join(self.dir, "shared")
        os.mkdir(shared)
        os.chmod(shared, 0o777)
        self.assertIn("others can write", self.cli("example", "plugin-hold", shared, code=2))
        self.assertEqual(os.listdir(shared), [])

    @unittest.skipUnless(hasattr(os, "getuid"), "needs user ids")
    def test_under_umask_000_no_folder_of_the_copy_is_writable_by_the_group_or_others(self):
        """Security review, finding 1: the lookup reads `<start>/.as-agreed/` with no check of who can write it."""
        ex = os.path.join(self.dir, "ex")
        old = os.umask(0)
        try:
            self.assertEqual(example.copy("plugin-hold", ex)[0], 0)
        finally:
            os.umask(old)
        for p in [""] + tree(ex):
            mode = stat.S_IMODE(os.lstat(os.path.join(ex, p)).st_mode)
            self.assertFalse(mode & (stat.S_IWGRP | stat.S_IWOTH), f"{p or 'ex'} {oct(mode)}")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_a_fifo_at_an_example_file_s_name_is_refused_at_once(self):
        """Security review, finding 2: reading a FIFO would wait for a writer that never comes."""
        self.cli("example", "plugin-hold", "ex")
        dropped = os.path.join(self.dir, "ex", "events", "dropped.json")
        os.unlink(dropped)
        os.mkfifo(dropped)
        self.assertIn("is not empty", self.cli("example", "plugin-hold", "ex", code=2))

    def test_an_unknown_example_is_a_usage_error(self):
        self.cli("example", "no-such-example", "ex", code=2)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "ex")))


class FiguresOnLongDigitRuns(unittest.TestCase):
    """The Reviewer's finding on numbers in words: 3.14 refuses int() on more than 4300 digits, so figures() raised
    ValueError there while 3.9 returned the integer. A run beyond the exact range tier 1 compares
    (`commitment.MAX_INT_BITS`) is kept as its text, as an identifier is, on both interpreters."""

    def test_a_run_of_5000_digits_returns_without_an_error(self):
        run = "7" * 5000
        self.assertEqual(figures(run), [run])
        self.assertEqual(figures(f"well over {run} commitments, three of them"), [run, 3])

    def test_a_decimal_beyond_float_range_is_text_never_infinity(self):
        run = "9" * 400 + ".5"
        self.assertEqual(figures(run), [run])

    def test_a_long_run_inside_the_exact_range_is_still_its_number(self):
        self.assertEqual(figures("9" * 300), [int("9" * 300)])
        self.assertEqual(figures("2" * 1300 + " and 12"), ["2" * 1300, 12])


if __name__ == "__main__":
    unittest.main()
