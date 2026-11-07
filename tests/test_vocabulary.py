"""The words of the commitment record stay the words: a banned identifier or an old verdict word in a shipped source
file fails here, as a network import fails in test_no_network.py."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

BANNED = re.compile(r"meaning|semantic|understanding|true_intent|real_intent", re.I)
# "absent downstream" is the terminology's own definition of DROPPED, a sentence and not a verdict
OLD_VERDICTS = re.compile(r"\b(?:intact|absent(?! downstream)|substituted|restored)\b", re.I)
PUBLIC_TEXTS = ("README.md", "SEND-A-MAP.md", "readings/README.md", "CITATION.cff", "pyproject.toml",
                ".github/ISSUE_TEMPLATE/send-a-map.md")


def shipped_texts():
    """Every text that travels with the package, not only the ones listed by hand: the fixtures and the spec are
    shipped too, so a banned word reintroduced in one of them would otherwise be published green."""
    for name in PUBLIC_TEXTS:
        if (ROOT / name).exists():
            yield ROOT / name
    for pattern in ("examples/*/*.md", "examples/*/*.json", "docs/*.md"):
        for path in sorted(ROOT.glob(pattern)):
            yield path
# the one place the old words may stand: the input aliases of the command line
ALIASES = re.compile(r"^OLD_FAIL_ON = ")


def sources():
    return sorted((ROOT / "src").rglob("*.py")) + [ROOT / "ledger.py", ROOT / "draw_flow.py"]


# the one place the banned words may stand in a text: the `Banned:` lines of the word list that bans them
BANNED_LINES = re.compile(r"^Banned: ")


def hits(path, pattern):
    word_list = path.name == "WORDLIST.md"
    return [(n, ln.strip()) for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if pattern.search(ln) and not ALIASES.search(ln) and not (word_list and BANNED_LINES.search(ln))]


class Vocabulary(unittest.TestCase):
    def test_no_banned_identifier_in_a_shipped_file(self):
        found = {str(p.relative_to(ROOT)): hits(p, BANNED) for p in sources() if hits(p, BANNED)}
        self.assertEqual(found, {})

    def test_no_old_verdict_word_in_a_shipped_file(self):
        found = {str(p.relative_to(ROOT)): hits(p, OLD_VERDICTS) for p in sources() if hits(p, OLD_VERDICTS)}
        self.assertEqual(found, {})

    def test_intent_stands_only_in_intent_record(self):
        word = re.compile(r"intent(?!_record)", re.I)
        found = {str(p.relative_to(ROOT)): hits(p, word) for p in sources() if hits(p, word)}
        self.assertEqual(found, {})

    def test_the_required_names_are_the_names(self):
        code = "\n".join(p.read_text(encoding="utf-8") for p in sources())
        for name in ("commitment", "field", "drift_class", "parent_id", "modality", "limit", "acceptance_criteria",
                     "fidelity_score", "intent_record"):
            self.assertIn(name, code)

    def test_no_banned_word_in_a_public_text(self):
        found = {str(p.relative_to(ROOT)): hits(p, BANNED) for p in shipped_texts() if hits(p, BANNED)}
        self.assertEqual(found, {})

    def test_no_old_verdict_word_in_a_shipped_text(self):
        found = {str(p.relative_to(ROOT)): hits(p, OLD_VERDICTS) for p in shipped_texts() if hits(p, OLD_VERDICTS)}
        self.assertEqual(found, {})

    def test_the_scan_reaches_the_fixtures(self):
        names = {str(p.relative_to(ROOT)) for p in shipped_texts()}
        self.assertIn("examples/planted-widened/01-order.md", names)
        self.assertIn("examples/planted-failure/commitments.json", names)

    def test_the_eleven_classes(self):
        from as_agreed.drift import DRIFT_CLASSES, TIER1_CLASSES
        self.assertEqual(len(DRIFT_CLASSES), 11)
        self.assertEqual(set(DRIFT_CLASSES) - set(TIER1_CLASSES), {"RESTATED"})


if __name__ == "__main__":
    unittest.main()
