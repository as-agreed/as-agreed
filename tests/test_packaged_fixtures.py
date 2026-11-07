"""The demo fixtures ship twice: in examples/ for the clone and in src/as_agreed/examples/ for the wheel.

The two copies must be byte-identical, so the demo prints the same lines from a clone and from `pip install`.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGED = ROOT / "src" / "as_agreed" / "examples"
FIXTURES = ("planted-failure", "planted-widened")


def inputs(folder):
    return sorted(p.name for p in folder.iterdir() if p.suffix in (".md", ".json"))


class PackagedFixtures(unittest.TestCase):
    def test_the_two_copies_hold_the_same_files(self):
        for name in FIXTURES:
            self.assertEqual(inputs(ROOT / "examples" / name), inputs(PACKAGED / name), name)

    def test_the_two_copies_are_byte_identical(self):
        for name in FIXTURES:
            for f in inputs(ROOT / "examples" / name):
                self.assertEqual((ROOT / "examples" / name / f).read_bytes(),
                                 (PACKAGED / name / f).read_bytes(), name + "/" + f)

    def test_only_the_demo_fixtures_are_packaged(self):
        self.assertEqual(sorted(p.name for p in PACKAGED.iterdir()), sorted(FIXTURES))


if __name__ == "__main__":
    unittest.main()
