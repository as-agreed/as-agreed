"""The flag's first line names the hold (0.1.3): a reader of that line alone sees that the run is held.

When the gate holds a run only because of an untyped MUST or MUST_NOT term (named on the line "Terms not restated,
untyped"), the first line used to read "0 held" beside "gate: HOLD". The first line now says the run is held, and by
which untyped terms. It keeps everything else: the typed count (S12: the headline counts typed terms only), the line
shape the data lab's parser reads, and the line as it was whenever no untyped term holds the run or a typed term
already shows the hold.

The chains are the invented ones in tests/fixtures/flag_truth/, two of them changed in a temporary folder.
"""
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
FIX = ROOT / "tests" / "fixtures" / "flag_truth"

from as_agreed.__main__ import main as cli  # noqa: E402

# the data lab's parser of the flag's first line (plans/2026-11-22-data-lab/lab/lab.py), copied: the shape must hold
FLAG = re.compile(r"^Kept as agreed: (\d+) of (\d+) binding terms, checked at ([^;]+); (\d+) held(?: \((.*?)\))?; "
                  r"(?:(\d+) changed and not held(?: \((.*?)\))?; )?(\d+) unchecked$")
NAMED = re.compile(r"(?:^|; )(\S+) (\S+) ([A-Z_]+): ")
NONE_CHECKED = "Kept as agreed: no binding term was checked in this run"


def reconcile(chain):
    """`as-agreed reconcile <chain> --out <temp>`, as the lab runs it: the exit code, the gate line, the flag's first
    line."""
    with tempfile.TemporaryDirectory() as out:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = cli(["reconcile", str(chain), "--out", out])
    lines = buf.getvalue().splitlines()
    gate = [ln for ln in lines if ln.startswith("gate: ")]
    first = [ln for ln in lines if ln.startswith("Kept as agreed:")]
    assert len(gate) == 1 and len(first) == 1, lines
    return code, gate[0], first[0]


def parsed(line):
    """What the lab reads from the first line; None when the line does not load."""
    m = FLAG.match(line)
    if m:
        return {"kept": int(m.group(1)), "terms": int(m.group(2)), "held": int(m.group(4)),
                "held_fields": NAMED.findall(m.group(5) or ""), "unchecked": int(m.group(8))}
    if "no binding term was checked" in line:
        return {"kept": 0, "terms": 0, "held": 0, "held_fields": [], "unchecked": 0}
    return None


class TheFirstLineNamesTheHold(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp))

    def chain(self, name, base="clean-untyped-must"):
        """A copy of a fixture chain to change."""
        path = self.tmp / name
        shutil.copytree(str(FIX / base), str(path))
        return path

    def test_a_chain_held_only_by_a_prose_must_reads_the_hold_on_its_first_line(self):
        code, gate, first = reconcile(FIX / "clean-untyped-must")
        self.assertEqual((code, gate), (1, "gate: HOLD (1 reasons)"))
        self.assertEqual(first, "Kept as agreed: 3 of 3 binding terms, checked at delivery; 0 held (the run is held "
                                "by 1 untyped term: prose_1 MUST); 0 unchecked")

    def test_a_chain_held_only_by_a_prose_must_not_names_its_modality(self):
        code, _, first = reconcile(FIX / "must-not-prose-dropped")
        self.assertEqual(code, 1)
        self.assertIn("0 held (the run is held by 1 untyped term: prose_1 MUST_NOT);", first)

    def test_a_chain_with_no_typed_term_reads_the_hold_on_its_first_line(self):
        code, _, first = reconcile(FIX / "prose-only")
        self.assertEqual(code, 1)
        self.assertEqual(first, NONE_CHECKED + "; the run is held by 1 untyped term: prose_1 MUST")

    def test_two_untyped_terms_are_both_named(self):
        path = self.chain("two-prose")
        rec = json.loads((path / "commitments.json").read_text(encoding="utf-8"))
        second = dict(next(c for c in rec["commitments"] if c["commitment_id"] == "prose_1"),
                      commitment_id="prose_2", act="Keep the vans below five degrees")
        rec["commitments"].append(second)
        (path / "commitments.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        order = path / "01-order.md"
        order.write_text(order.read_text(encoding="utf-8") + "- Keep the vans below five degrees\n", encoding="utf-8")
        code, _, first = reconcile(path)
        self.assertEqual(code, 1)
        self.assertIn("0 held (the run is held by 2 untyped terms: prose_1 MUST, prose_2 MUST);", first)

    def test_a_passed_chain_reads_as_before(self):
        code, gate, first = reconcile(FIX / "should-prose")
        self.assertEqual((code, gate), (0, "gate: pass"))
        self.assertEqual(first, "Kept as agreed: 3 of 3 binding terms, checked at delivery; 0 held; 0 unchecked")

    def test_a_typed_hold_reads_as_before(self):
        code, _, first = reconcile(FIX / "drop-middle")
        self.assertEqual(code, 1)
        self.assertEqual(first, "Kept as agreed: 2 of 3 binding terms, checked at a brief and delivery; 1 held "
                                "(guests limit.guests DROPPED: present upstream, absent downstream); 0 unchecked")

    def test_a_typed_hold_beside_an_untyped_one_names_the_typed_hold_as_before(self):
        """The first line already shows the hold; the untyped term stays on its own line, and the typed names stay
        the only names the lab reads."""
        path = self.chain("drop-plus-prose")
        handover = path / "05-handover.json"
        out = json.loads(handover.read_text(encoding="utf-8"))
        del out["guests"]
        handover.write_text(json.dumps(out, indent=1), encoding="utf-8")
        code, _, first = reconcile(path)
        self.assertEqual(code, 1)
        f = parsed(first)
        self.assertEqual((f["held"], f["held_fields"]), (1, [("guests", "limit.guests", "DROPPED")]), first)
        self.assertNotIn("untyped", first)


class TheShapeTheLabReads(unittest.TestCase):
    """The typed count, the line shape and the names the lab reads do not move."""

    def test_every_fixture_chain_loads_with_its_typed_count(self):
        want = {"clean-untyped-must": (3, 3, 0, 0), "must-not-prose-dropped": (3, 3, 0, 0),
                "prose-only": (0, 0, 0, 0), "should-prose": (3, 3, 0, 0), "drop-middle": (2, 3, 1, 0),
                "must-not-carried-dropped": (3, 4, 1, 0)}
        for chain, counts in want.items():
            _, _, first = reconcile(FIX / chain)
            f = parsed(first)
            self.assertIsNotNone(f, first)
            self.assertEqual((f["kept"], f["terms"], f["held"], f["unchecked"]), counts, chain)

    def test_the_note_adds_no_name_the_lab_reads_as_a_typed_hold(self):
        for chain in ("clean-untyped-must", "must-not-prose-dropped"):
            _, _, first = reconcile(FIX / chain)
            self.assertEqual(parsed(first)["held_fields"], [], first)


if __name__ == "__main__":
    unittest.main()
