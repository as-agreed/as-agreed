#!/usr/bin/env python3
"""draw_flow: one command from a per-claim CSV to a status-flow SVG.

    python3 draw_flow.py claims.csv --stages s1_report,s2_parent,s3_marker --out flow.svg \
        --title "Where does the doubt go?" --stop-at s2_parent=lost

    python3 draw_flow.py run/ledger.jsonl --out flow.svg [--expand-claims] [--values]
    python3 draw_flow.py run/counts.csv --out flow.svg [--expand-claims]

A run's `ledger.jsonl`, or its `counts.csv` with no --stages, is drawn as the report draws it (SPEC §7): every
changed commitment field, and every field a hold named, in the loss colour; the claim sentences as one grey band
per link (--expand-claims draws one ribbon per claim); the verdict sentence on the title line, in kinds and counts
(--values, from a ledger only, names the field and the agreed and arrived values: that file is not for sharing).
From `counts.csv` the map cannot know which fields a hold named, so only changed fields are drawn as losses.

The CSV has one row per claim (or per copy) and one column per stage. Each stage column holds a
category label; the tool counts the rows for every pair of adjacent categories and draws them as
ribbons. Nothing else is read, so the CSV may hold texts, paths or ids in other columns and they
never reach the picture: the SVG contains the stage names, category labels, counts and the
title you give.

Options
  --stages      comma-separated column names, left to right (required for any other CSV)
  --out         output .svg (default: flow.svg)
  --title       title line
  --subtitle    second line
  --note        footnote line (repeatable)
  --stop-at     stage=value whose rows end at that node and do not continue (repeatable)
  --order       stage=v1|v2|v3 to fix the vertical order of a stage's nodes (repeatable)
  --color       stage=value=#hex to colour a first-stage category (repeatable); others get defaults
  --red         stage=value to outline a node in red and colour its outgoing ribbons red (repeatable)
  --width/--height  canvas size in px (default 1600 x 860)
  --expand-claims   a run's map: one ribbon per claim instead of one band per link
  --values          a run's map from its ledger.jsonl: the agreed and arrived values in the title and the reasons

Standard library only.
"""

import argparse
import csv
import sys
from collections import Counter, OrderedDict

from .flowsvg import Flow, Node, Stage, render

PALETTE = ["#6f9f6f", "#d9b92f", "#e5862e", "#4a6fa5", "#8a7f8d", "#c7a24a", "#5b8c5a", "#b0a9b3", "#9a9a9a", "#3b7a57"]
RED = "#c0392b"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--stages", default="")
    ap.add_argument("--out", default="flow.svg")
    ap.add_argument("--title", default="")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--note", action="append", default=[])
    ap.add_argument("--stop-at", action="append", default=[])
    ap.add_argument("--order", action="append", default=[])
    ap.add_argument("--color", action="append", default=[])
    ap.add_argument("--red", action="append", default=[])
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=860)
    ap.add_argument("--expand-claims", action="store_true")
    ap.add_argument("--values", action="store_true")
    a = ap.parse_args()

    if a.csv.endswith(".jsonl"):
        from .report import redraw
        redraw(a.csv, a.out, title=a.title, expand_claims=a.expand_claims, values=a.values)
        print(f"wrote {a.out} from the run's records")
        return
    if not a.stages:
        from .report import RUN_COLUMNS, draw_counts
        with open(a.csv, newline="") as f:
            head = next(csv.reader(f), [])
        if head[:len(RUN_COLUMNS)] != list(RUN_COLUMNS):
            sys.exit("--stages is required unless the input is a run's ledger.jsonl or counts.csv")
        if a.values:
            sys.exit("--values needs the run's ledger.jsonl: counts.csv holds labels only")
        draw_counts(a.csv, a.out, title=a.title, expand_claims=a.expand_claims)
        print(f"wrote {a.out} from the run's counts")
        return

    stages = [s.strip() for s in a.stages.split(",") if s.strip()]
    rows = list(csv.DictReader(open(a.csv, newline="")))
    missing = [s for s in stages if rows and s not in rows[0]]
    if missing:
        sys.exit(f"columns not in CSV: {missing}")

    stops = set()
    for s in a.stop_at:
        st, val = s.split("=", 1)
        stops.add((st.strip(), val.strip()))
    orders = {}
    for s in a.order:
        st, vals = s.split("=", 1)
        orders[st.strip()] = [v.strip() for v in vals.split("|")]
    reds = set()
    for s in a.red:
        st, val = s.split("=", 1)
        reds.add((st.strip(), val.strip()))
    colors = {}
    for s in a.color:
        st, val, hexv = s.split("=", 2)
        colors[(st.strip(), val.strip())] = hexv.strip()

    # node order per stage: explicit order first, then by descending count
    def node_keys(st):
        cnt = Counter(r[st] for r in rows if r[st] != "")
        ordered = [v for v in orders.get(st, []) if v in cnt]
        ordered += [v for v, _ in cnt.most_common() if v not in ordered]
        return ordered

    first = stages[0]
    first_keys = node_keys(first)
    origin_color = OrderedDict()
    for i, v in enumerate(first_keys):
        origin_color[v] = colors.get((first, v), PALETTE[i % len(PALETTE)])

    flow_stages = []
    for st in stages:
        nodes = []
        for v in node_keys(st):
            key = f"{st}::{v}"
            if st == first:
                col = origin_color[v]
            else:
                col = colors.get((st, v), "#b8b8b8")
            stroke = RED if (st, v) in reds else None
            if stroke:
                col = RED
            nodes.append(Node(key, v, col, stroke=stroke))
        flow_stages.append(Stage(st.replace("_", " "), nodes))

    flow = Flow(flow_stages, title=a.title, subtitle=a.subtitle, footnote=a.note)

    # a row continues from stage i to i+1 unless it hit a stop node at or before stage i
    alive = [True] * len(rows)
    for i in range(len(stages) - 1):
        s_from, s_to = stages[i], stages[i + 1]
        for k, r in enumerate(rows):
            if alive[k] and (s_from, r[s_from]) in stops:
                alive[k] = False
        keys_from = node_keys(s_from)
        keys_to = node_keys(s_to)
        touched_red = [False] * len(rows)
        for k, r in enumerate(rows):
            for j in range(i + 1):
                if (stages[j], r[stages[j]]) in reds:
                    touched_red[k] = True
        for vf in keys_from:
            for vt in keys_to:
                for ov in first_keys:
                    for red in (True, False):
                        n = sum(1 for k, r in enumerate(rows) if alive[k] and r[s_from] == vf and r[s_to] == vt
                                and r[first] == ov and touched_red[k] == red and r[s_to] != "")
                        if n:
                            col = RED if red else origin_color[ov]
                            flow.add(f"{s_from}::{vf}", f"{s_to}::{vt}", n, col,
                                     f"{vf} → {vt} (origin {ov}{', red path' if red else ''}): {n}")
    svg = render(flow, width=a.width, height=a.height)
    with open(a.out, "w") as f:
        f.write(svg)
    print(f"wrote {a.out}: {len(rows)} rows, {len(stages)} stages")


if __name__ == "__main__":
    main()
