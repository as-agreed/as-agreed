"""The text an agent or a new user follows (0.1.3): the skill, the packaged examples, and the install line.

A `carried_by` key on a word of the order (`act`, `obligor`, `obligee`, `modality`) checks nothing: the agent copies the
order's words into the output, the copy matches, and the term counts as kept. On a one-term order the band then reads
"3 of 3" where one term was checked. So the skill's example and rule, and every record the package ships, give a key only
to a value the call uses, and the example's events carry no words copied from the order.

`pip install as-agreed` is "command not found" on a Mac that has only `pip3`. And on that Mac's `/usr/bin/python3`, pip
installs for the user, into a folder that is not on PATH, so `as-agreed` itself is not found either. So each doc installs
with `python3 -m pip install as-agreed`, and names `python3 -m as_agreed` right after it.

Slice S2 edits SKILL.md after this slice; these tests keep the fix through that edit."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
SKILL = ROOT / "skills" / "as-agreed" / "SKILL.md"
README = ROOT / "README.md"
FIRST_RUN = ROOT / "docs" / "FIRST-RUN.md"
TREES = (ROOT / "examples", SRC / "as_agreed" / "examples")
COPIED = ("act", "obligor", "obligee", "modality")
P1 = ("In `carried_by`, give a key only to a value that the call uses, for example the amount that it pays. Do not copy "
      "the words of the order into the output: a copied value counts as kept and checks nothing.")
INSTALL = "python3 -m pip install as-agreed"
FALLBACK = re.compile(r"does not find `as-agreed`.*`python3 -m as_agreed`")


def commitments(data):
    """The commitments of a record (`{"commitments": [...]}`) or of a commitments file (a list)."""
    return data.get("commitments", []) if isinstance(data, dict) else data


def copied_keys(commitment):
    """The `carried_by` paths that start at a word of the order, not at a value the call uses."""
    return sorted(p for p in (commitment.get("carried_by") or {}) if p.split(".")[0] in COPIED)


def records():
    """Every record the repository and the package ship as an example: `record.json` and `commitments.json`."""
    found = []
    for tree in TREES:
        for d, dirs, files in os.walk(str(tree)):
            found += [Path(d, f) for f in files if f in ("record.json", "commitments.json")]
    return sorted(found)


def words(text):
    return set(re.findall(r"[a-z]+", str(text).lower()))


def strings(value):
    """Every text value inside a JSON value, keys aside."""
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return [value] if isinstance(value, str) else []


def skill_example():
    """The record in section 1 of the skill: the first `json` block after its heading."""
    text = SKILL.read_text(encoding="utf-8")
    block = re.search(r"^## 1\..*?^```json\n(.*?)^```", text, re.M | re.S)
    return json.loads(block.group(1))


class TheSkill(unittest.TestCase):
    def test_the_example_gives_no_key_to_a_word_of_the_order(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertEqual(text.count('"act": "act"'), 0)
        example = commitments(skill_example())
        self.assertTrue(example)
        for c in example:
            self.assertEqual(copied_keys(c), [], c.get("commitment_id"))
            self.assertTrue(c.get("carried_by"), "the example shows a key that checks a value")

    def test_each_key_of_the_example_names_a_value_the_commitment_holds(self):
        for c in commitments(skill_example()):
            for path in c["carried_by"]:
                value = c
                for part in path.split("."):
                    self.assertIsInstance(value, dict, path)
                    self.assertIn(part, value, path)
                    value = value[part]

    def test_the_rule_reads_the_replacement_text(self):
        lines = [ln[2:] for ln in SKILL.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
        self.assertIn(P1, lines)
        self.assertFalse([ln for ln in lines if "give the key of the output that has each field" in ln])


class ThePackagedExamples(unittest.TestCase):
    def test_every_record_is_read(self):
        """plugin-hold, planted-failure, planted-widened and capture (0.1.3), in the repository and in the package:
        eight records."""
        names = sorted(str(p.relative_to(ROOT)) for p in records())
        self.assertEqual(len(names), 8, names)

    def test_no_record_gives_a_key_to_a_word_of_the_order(self):
        for path in records():
            for c in commitments(json.loads(path.read_text(encoding="utf-8"))):
                self.assertEqual(copied_keys(c), [], f"{path.relative_to(ROOT)} {c.get('commitment_id')}")

    def test_the_events_carry_no_words_copied_from_the_order(self):
        for tree in TREES:
            folder = tree / "plugin-hold"
            record = json.loads((folder / ".as-agreed" / "record.json").read_text(encoding="utf-8"))
            order = words(record["order"])
            for c in record["commitments"]:
                order |= words(" ".join(str(c.get(k, "")) for k in COPIED))
            events = sorted((folder / "events").glob("*.json"))
            self.assertEqual([e.name for e in events], ["dropped.json", "faithful.json", "widened.json"])
            for e in events:
                content = json.loads(json.loads(e.read_text(encoding="utf-8"))["tool_input"]["content"])
                where = f"{e.relative_to(ROOT)}"
                self.assertEqual([k for k in content if k in COPIED], [], where)
                for s in strings(content):
                    self.assertEqual(sorted(words(s) & order), [], f"{where}: {s!r}")

    def test_the_dropped_event_has_no_amount(self):
        for tree in TREES:
            folder = tree / "plugin-hold"
            record = json.loads((folder / ".as-agreed" / "record.json").read_text(encoding="utf-8"))
            key = record["commitments"][0]["carried_by"]["limit.amount"]
            dropped = json.loads((folder / "events" / "dropped.json").read_text(encoding="utf-8"))
            self.assertNotIn(key, json.loads(dropped["tool_input"]["content"]))


class TheInstallLine(unittest.TestCase):
    def test_each_doc_installs_with_python3_m_pip(self):
        for doc in (README, FIRST_RUN):
            text = doc.read_text(encoding="utf-8")
            self.assertGreaterEqual(text.count(INSTALL), 1, doc.name)
            self.assertEqual(re.findall(r"^pip install as-agreed", text, re.M), [], doc.name)

    def test_each_install_block_is_followed_by_the_python3_m_as_agreed_line(self):
        for doc in (README, FIRST_RUN):
            lines = doc.read_text(encoding="utf-8").splitlines()
            sites = [i for i, ln in enumerate(lines) if ln.strip() == INSTALL]
            self.assertTrue(sites, doc.name)
            for i in sites:
                end = next(j for j in range(i + 1, len(lines)) if lines[j].startswith("```"))
                after = next(ln for ln in lines[end + 1:] if ln.strip())
                self.assertRegex(after, FALLBACK, f"{doc.name} line {i + 1}")


class TheFirstRunPage(unittest.TestCase):
    """Steps 2 and 3 of docs/FIRST-RUN.md, run as the page says, with `python -m as_agreed` for `as-agreed`."""

    def test_the_page_shows_what_the_example_prints(self):
        shown = [ln for ln in FIRST_RUN.read_text(encoding="utf-8").splitlines() if ln.startswith('{"labels":')]
        self.assertEqual(len(shown), 1, shown)
        prefix = shown[0].rsplit(" ...", 1)[0]
        tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        env = {k: v for k, v in os.environ.items() if k not in ("AS_AGREED_RECORD", "CLAUDE_PROJECT_DIR")}
        env["PYTHONPATH"] = str(SRC)

        def cli(*args, cwd, stdin=None, extra=None):
            p = subprocess.run([sys.executable, "-m", "as_agreed", *args], cwd=cwd, env=dict(env, **(extra or {})),
                               input=stdin, capture_output=True, text=True, timeout=60)
            return p.returncode, p.stdout

        self.assertEqual(cli("example", "plugin-hold", "run", cwd=tmp)[0], 0)
        run = os.path.join(tmp, "run")
        code, out = cli("hook", cwd=run, stdin=Path(run, "events", "dropped.json").read_text(),
                        extra={"CLAUDE_PROJECT_DIR": run})
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        code, out = cli("band", os.path.join(".as-agreed", "runs", "example", "ledger.jsonl"), cwd=run)
        self.assertTrue(out.startswith(prefix), f"the page shows {prefix!r}; the example prints {out[:120]!r}")


class TheSkillForms(unittest.TestCase):
    """0.1.3 stage 3 (T1): the limit forms an agent wrote wrong, the confirm line for a shell that does not find
    `as-agreed`, and every kind of mark that `show` gives named where the skill says what it marks."""

    def section_3(self):
        return re.search(r"^## 3\..*?(?=^## )", SKILL.read_text(encoding="utf-8"), re.M | re.S).group(0)

    def test_in_and_not_in_take_a_list_and_a_date_limit_is_until_or_from(self):
        rules = [ln for ln in SKILL.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
        self.assertTrue([ln for ln in rules if "`in` and `not_in` take a list" in ln], rules)
        self.assertTrue([ln for ln in rules if "date limit" in ln and "`until`" in ln and "`from`" in ln], rules)

    def test_the_confirm_step_names_python3_m_as_agreed_confirm(self):
        self.assertRegex(self.section_3(), r"does not find `as-agreed`[^\n]*`python3 -m as_agreed confirm`")

    def test_the_skill_names_every_kind_of_mark_that_show_gives(self):
        from as_agreed import capture
        said = {capture.NOT_IN_ORDER: "not in the order", capture.MISMATCH: "differs from its words",
                capture.NO_WORDS: "no words", capture.HIDDEN: "hidden character", capture.BINDING: "binding with no step"}
        self.assertEqual(sorted(said), sorted(capture.KINDS), "a kind of mark the skill does not name")
        section = self.section_3()
        for kind, words in said.items():
            self.assertIn(words, section, kind)


if __name__ == "__main__":
    unittest.main()
