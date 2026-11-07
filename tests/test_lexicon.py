"""The Russian hedge «наверное» counts as a hedge, so a «наверное» hardened downstream fails the gate,
while «наверняка» (for sure) stays a certainty word."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEDGED = "Поставщик, наверное, сменил банковские реквизиты в октябре."


def run(first, second):
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "01-notes.md").write_text(first + "\n", encoding="utf-8")
        (Path(folder) / "02-summary.md").write_text(second + "\n", encoding="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "ledger.py"), "run", folder,
                               "--only", "hedged", "--fail-on", "danger"], capture_output=True, text=True)


class RussianHedges(unittest.TestCase):
    def test_naverno_hardened_into_tochno_fails(self):
        r = run(HEDGED, "Поставщик точно сменил банковские реквизиты в октябре.")
        self.assertEqual(r.returncode, 1)
        self.assertIn("DANGER", r.stdout)

    def test_naverno_kept_passes(self):
        r = run(HEDGED, HEDGED)
        self.assertEqual(r.returncode, 0)
        self.assertIn("hedged 1", r.stdout)

    def test_navernyaka_is_not_a_hedge(self):
        r = run("Поставщик наверняка сменил банковские реквизиты в октябре.", HEDGED)
        self.assertIn("hedged 0", r.stdout)


if __name__ == "__main__":
    unittest.main()
