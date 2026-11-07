"""The map's colours follow what happened to a field, not its class name (SPEC §7, K1.0d, 2026-11-04): a changed
commitment field and every field a hold named are drawn in the loss colour, the claim sentences are one grey band per
link, and the title line carries the verdict sentence, in kinds and counts unless the caller asks for the values."""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from as_agreed.demo import run_demo  # noqa: E402
from as_agreed.importers.stages import run_folder  # noqa: E402
from as_agreed.report import CLAIM_BAND, LOSS, NODE_COLOURS, redraw  # noqa: E402

SVG = "{http://www.w3.org/2000/svg}"


def parse(path):
    root = ET.parse(path).getroot()
    rects = []
    for r in root.iter(SVG + "rect"):
        t = r.find(SVG + "title")
        rects.append({"fill": r.get("fill"), "stroke": r.get("stroke"), "x": r.get("x"), "cls": r.get("class"),
                      "title": t.text if t is not None else ""})
    ribbons = []
    for p in root.iter(SVG + "path"):
        t = p.find(SVG + "title")
        ribbons.append({"fill": p.get("fill"), "title": t.text if t is not None else ""})
    title = " ".join("".join(t.itertext()) for t in root.iter(SVG + "text") if t.get("class") == "ttl")
    text = " ".join("".join(t.itertext()) for t in root.iter(SVG + "text"))
    return rects, ribbons, title, text


class Map(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="as-agreed-map-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def demo(self, fixture, retry=False):
        out = os.path.join(self.tmp, fixture + ("-retry" if retry else ""))
        run_demo(fixture, retry=retry, out_dir=out, quiet=True)
        return out

    def test_the_phone_check_that_held_the_payment_is_in_the_loss_colour(self):
        out = self.demo("dropped")
        rects, ribbons, title, text = parse(os.path.join(out, "flow.svg"))
        dropped = [r for r in rects if r["title"].startswith("DROPPED, held")]
        self.assertEqual(len(dropped), 1, rects)
        self.assertEqual(dropped[0]["fill"], LOSS)
        self.assertEqual(dropped[0]["stroke"], LOSS)
        # nothing of a commitment is drawn in the old grey of a dropped claim
        self.assertFalse([r for r in rects if r["title"].startswith("DROPPED") and r["fill"] == NODE_COLOURS["DROPPED"]])
        into = [p for p in ribbons if "DROPPED, held" in p["title"].split("->")[-1]]
        self.assertTrue(into and all(p["fill"] == LOSS for p in into), ribbons)
        # the verdict sentence is the title line, and the hold reasons are on the map
        self.assertIn("agreed an acceptance criterion of a MUST commitment, arrived missing (DROPPED), at step "
                      "commit#1@1, held", title)
        self.assertIn("HELD at commit#1@1: 3 reasons", text)

    def test_a_claim_only_link_is_a_single_grey_band(self):
        folder = os.path.join(self.tmp, "claims")
        os.makedirs(folder)
        Path(folder, "01-order.md").write_text("Build the game; captures follow the standard rules.\n"
                                               "Kinging probably follows the rule, but it was not found.\n")
        Path(folder, "02-code.md").write_text("We built the game. Captures follow standard rules.\n")
        Path(folder, "03-review.md").write_text("Review passed. Kinging follows the rule.\n")
        out = os.path.join(self.tmp, "claims-out")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            run_folder(folder, out_dir=out)
        rects, ribbons, title, text = parse(os.path.join(out, "flow.svg"))
        bands = [r for r in rects if r["cls"] == "band"]
        self.assertEqual(len(bands), 3)  # origin and two links
        self.assertEqual(len({b["x"] for b in bands}), 3)  # one per column
        self.assertTrue(all(b["fill"] == CLAIM_BAND for b in bands))
        self.assertFalse([r for r in rects if r["cls"] != "band" and ("(claim)" in r["title"]
                                                                       or r["title"].startswith("claim "))])
        self.assertEqual(len(ribbons), 2)  # only the band's two joins
        self.assertIn("claim sentences, word-matched", text)
        # the flag expands them back into one ribbon per claim
        expanded = os.path.join(self.tmp, "expanded.svg")
        redraw(os.path.join(out, "ledger.jsonl"), expanded, expand_claims=True)
        rects, ribbons, _, _ = parse(expanded)
        self.assertFalse([r for r in rects if r["cls"] == "band"])
        self.assertTrue([r for r in rects if "(claim)" in r["title"]])

    def test_the_demo_link_with_commitments_keeps_one_band_for_its_claims(self):
        rects, _, _, _ = parse(os.path.join(self.demo("dropped"), "flow.svg"))
        bands = [r for r in rects if r["cls"] == "band"]
        self.assertEqual(len(bands), 3)
        self.assertFalse([r for r in rects if "(claim)" in r["title"]])

    def test_a_widened_limit_is_a_loss_and_the_shared_form_holds_no_value(self):
        out = self.demo("widened", retry=True)
        svg = os.path.join(out, "flow.svg")
        rects, _, title, text = parse(svg)
        widened = [r for r in rects if r["title"].startswith("WIDENED, held")]
        self.assertEqual([r["fill"] for r in widened], [LOSS])
        self.assertIn("arrived wider than agreed (WIDENED), at step commit#1@1, held, then passed at commit#1@2", title)
        raw = Path(svg).read_text(encoding="utf-8")
        for value in ("19800", "18400", "Northwind", "pay-invoice", "limit.amount"):
            self.assertNotIn(value, raw)
        shown = os.path.join(self.tmp, "values.svg")
        redraw(os.path.join(out, "ledger.jsonl"), shown, values=True)
        _, _, title, text = parse(shown)
        self.assertIn("agreed pay-invoice limit.amount at most 18400 USD, arrived 19800 (WIDENED)", title)

    def test_draw_flow_draws_a_run_from_its_counts(self):
        out = self.demo("dropped")
        svg = os.path.join(self.tmp, "from-counts.svg")
        r = subprocess.run([sys.executable, str(ROOT / "draw_flow.py"), os.path.join(out, "counts.csv"), "--out", svg],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rects, _, title, _ = parse(svg)
        self.assertEqual([x["fill"] for x in rects if x["title"].startswith("DROPPED")], [LOSS])
        self.assertIn("the gate is not in counts.csv", title)
        r = subprocess.run([sys.executable, str(ROOT / "draw_flow.py"), os.path.join(out, "counts.csv"), "--out", svg,
                            "--values"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_the_claim_ledger_map_draws_a_dropped_must_travel_claim_red(self):
        folder = os.path.join(self.tmp, "pf")
        shutil.copytree(ROOT / "examples" / "planted-failure", folder)
        subprocess.run([sys.executable, str(ROOT / "ledger.py"), "run", folder, "--against-all", "--only",
                        "hedged,not_found,sourced,dated,must", "--fail-on", "danger,dropped_must"], capture_output=True)
        rects, ribbons, _, _ = parse(os.path.join(folder, "flow.svg"))
        lost = [r for r in rects if r["title"].startswith("dropped, must-travel")]
        self.assertEqual([r["fill"] for r in lost], [LOSS])
        self.assertEqual([r["fill"] for r in rects if r["title"].startswith("hardened")], [LOSS])
        self.assertTrue(all(p["fill"] == LOSS for p in ribbons if "::lost" in p["title"]))

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", "-m", "as_agreed", *args], capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=str(ROOT / "src")))

    def test_reconcile_takes_values(self):
        out = os.path.join(self.tmp, "rc")
        r = self.cli("reconcile", str(ROOT / "examples" / "planted-failure"), "--out", out, "--values")
        self.assertEqual(r.returncode, 1, r.stderr)
        _, _, title, text = parse(os.path.join(out, "flow.svg"))
        self.assertIn("phone-check acceptance_criteria.phone", title)
        self.assertIn("values are in this file", text)

    def test_demo_takes_expand_claims(self):
        out = os.path.join(self.tmp, "dm")
        r = self.cli("demo", "--fixture", "dropped", "--no-retry", "--out", out, "--expand-claims")
        self.assertEqual(r.returncode, 1, r.stderr)
        rects, _, _, _ = parse(os.path.join(out, "flow.svg"))
        self.assertFalse([x for x in rects if x["cls"] == "band"])
        self.assertTrue([x for x in rects if "(claim)" in x["title"]])

    def test_sdk_report_passes_both_flags(self):
        import as_agreed.sdk as ab
        from as_agreed.adapters.plain import PlainAdapter
        ad = PlainAdapter(run_dir=os.path.join(self.tmp, "sdk"), pipeline="t")
        agreed = ab.register("Pay at most 100 USD. The vendor probably agreed.", [ab.Commitment(
            commitment_id="pay", act="pay", obligor="a", obligee="b", modality="MUST",
            limit={"amount": {"max": 100, "unit": "USD"}}, carried_by={"limit.amount": "amount"})], adapter=ad)

        @ab.step("commit", agreed, commit=lambda note, idempotency_key: None, retries=0)
        def commit(x, reason=None):
            return {"amount": 150}

        with contextlib.suppress(Exception):
            commit("go")
        rep = ab.report(agreed, os.path.join(self.tmp, "sdk-out"), quiet=True, values=True, expand_claims=True)
        rects, _, title, _ = parse(rep.svg_path)
        self.assertIn("agreed pay limit.amount at most 100 USD, arrived 150 (WIDENED)", title)
        self.assertFalse([x for x in rects if x["cls"] == "band"])


if __name__ == "__main__":
    unittest.main()
