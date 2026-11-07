"""flowsvg: draw a Sankey-style flow of claim status across agent hops as a standalone SVG.

Standard library only. Input: ordered stages (columns) of nodes, and weighted links between
nodes of adjacent stages. Output: an SVG string. The picture holds category labels and counts
only, never claim texts, paths or identifiers, so it can be shared as an aggregate.

Layout rules
- Each stage is a column. Node height is proportional to max(inflow, outflow), so a flow may
  end at a node (a claim that stops here) without a dummy node downstream.
- Ribbons are cubic curves from a slot on the source node's right edge to a slot on the target
  node's left edge. Each ribbon carries its own colour (usually the colour of its origin
  category, so the eye can follow where each kind of claim went).
- An emphasised node (a loss) is drawn at least `min_px` tall and labelled in bold in its
  stroke colour, so one changed field among many is never a hairline.
- A band is a fixed-height strip under the flow, one per stage, joined across stages: many
  units collapsed into one count, drawn the same height whatever the count.
- A banner is a boxed block of lines under the title (the reasons a commit was held).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape


@dataclass
class Node:
    key: str
    label: str
    color: str = "#999999"
    sublabel: str = ""
    stroke: Optional[str] = None  # outline colour, e.g. red for a dangerous node
    emphasis: bool = False  # a loss: at least min_px tall, its label bold in the stroke colour
    min_px: float = 0.0


@dataclass
class Stage:
    title: str
    nodes: List[Node]


@dataclass
class Band:
    """A fixed-height strip in one stage's column, under the flow: many units collapsed into one count."""
    stage: int
    label: str
    count: int
    color: str = "#b8b8b8"
    sublabel: str = ""
    stroke: Optional[str] = None


@dataclass
class Link:
    src: str
    dst: str
    value: float
    color: str
    title: str = ""


@dataclass
class Flow:
    stages: List[Stage]
    links: List[Link] = field(default_factory=list)
    title: str = ""
    subtitle: str = ""
    footnote: List[str] = field(default_factory=list)
    bands: List[Band] = field(default_factory=list)
    banner: List[str] = field(default_factory=list)  # boxed lines under the title, e.g. the reasons for a hold
    banner_title: str = ""
    accent: str = "#c0392b"  # the banner's colour, and the title's when title_accent is set
    title_accent: bool = False
    wrap: int = 0  # wrap the title and the subtitle at this many characters; 0 keeps one line each

    def add(self, src: str, dst: str, value: float, color: str, title: str = "") -> None:
        if value <= 0:
            return
        self.links.append(Link(src, dst, value, color, title))


def _fmt(n: float) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


def render(flow: Flow, width: int = 1400, height: int = 820, node_width: int = 18,
           pad: int = 14, margin_left: int = 250, margin_right: int = 290,
           margin_top: int = 110, margin_bottom: int = 120, font: str = "Helvetica, Arial, sans-serif",
           min_node_px: float = 2.0) -> str:
    stages = flow.stages
    n_stages = len(stages)
    assert n_stages >= 2

    inflow: Dict[str, float] = {}
    outflow: Dict[str, float] = {}
    for l in flow.links:
        outflow[l.src] = outflow.get(l.src, 0) + l.value
        inflow[l.dst] = inflow.get(l.dst, 0) + l.value

    def size(key: str) -> float:
        return max(inflow.get(key, 0), outflow.get(key, 0))

    # vertical scale: the tallest column (sum of node sizes + padding) must fit
    plot_h = height - margin_top - margin_bottom
    col_totals = []
    for st in stages:
        live = [n for n in st.nodes if size(n.key) > 0]
        col_totals.append((sum(size(n.key) for n in live), len(live)))
    scale = min(((plot_h - pad * max(k - 1, 0)) / tot for tot, k in col_totals if tot > 0), default=1.0)

    # the header: title and subtitle lines, then the banner; whatever it needs beyond the default margin is added
    # to the canvas, so the plot keeps its height
    ttl_lines = _wrap(flow.title, flow.wrap) if flow.title and flow.wrap else ([flow.title] if flow.title else [])
    stt_lines = _wrap(flow.subtitle, flow.wrap + 40) if flow.subtitle and flow.wrap else ([flow.subtitle] if flow.subtitle else [])
    ttl_y = [34 + 26 * k for k in range(len(ttl_lines))]
    stt_top = (ttl_y[-1] if ttl_y else 34) + 22
    stt_y = [stt_top + 17 * k for k in range(len(stt_lines))]
    banner_top = (stt_y[-1] if stt_y else stt_top) + 16
    banner_rows = ([flow.banner_title] if flow.banner_title else []) + list(flow.banner)
    banner_h = 14 + 18 * len(banner_rows) if banner_rows else 0
    header_bottom = banner_top + banner_h if banner_rows else (stt_y[-1] if stt_y else 34)
    top_extra = max(0, int(header_bottom + 54 - margin_top)) if (banner_rows or len(ttl_lines) > 1
                                                                   or len(stt_lines) > 1) else 0
    band_h, band_space = 20.0, (78 if flow.bands else 0)
    margin_top += top_extra
    height += top_extra + band_space

    # node geometry
    plot_w = width - margin_left - margin_right
    xs = [margin_left + i * (plot_w - node_width) / (n_stages - 1) for i in range(n_stages)]
    geom: Dict[str, Tuple[float, float, float, float]] = {}  # key -> (x, y, w, h)
    for i, st in enumerate(stages):
        live = [n for n in st.nodes if size(n.key) > 0]
        total_h = sum(max(size(n.key) * scale, n.min_px or min_node_px) for n in live) + pad * max(len(live) - 1, 0)
        y = margin_top + (plot_h - total_h) / 2
        for n in live:
            h = max(size(n.key) * scale, n.min_px or min_node_px)
            geom[n.key] = (xs[i], y, node_width, h)
            y += h + pad

    # slot bookkeeping: ribbons stack in the order links were added, per node side
    out_cursor: Dict[str, float] = {k: 0.0 for k in geom}
    in_cursor: Dict[str, float] = {k: 0.0 for k in geom}
    # scale per node so that stacked ribbons fill the node even when node was clamped to min px
    def side_scale(key: str, total: float) -> float:
        _, _, _, h = geom[key]
        return h / total if total > 0 else scale

    out = []
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
               f'viewBox="0 0 {width} {height}" font-family="{font}">')
    out.append(f'<rect width="{width}" height="{height}" fill="#ffffff"/>')
    out.append('<style>.lbl{font-size:13px;fill:#222}.sub{font-size:11px;fill:#666}'
               '.hdr{font-size:14px;font-weight:600;fill:#222}.ttl{font-size:20px;font-weight:600;fill:#111}'
               '.stt{font-size:13px;fill:#444}.fn{font-size:11px;fill:#555}path.rib:hover{opacity:0.95}</style>')
    accent = f' style="fill:{flow.accent}"' if flow.title_accent else ""
    for y_, line in zip(ttl_y, ttl_lines):
        out.append(f'<text class="ttl" x="{margin_left}" y="{y_}"{accent}>{escape(line)}</text>')
    for y_, line in zip(stt_y, stt_lines):
        out.append(f'<text class="stt" x="{margin_left}" y="{y_}">{escape(line)}</text>')
    if banner_rows:
        bw = width - margin_left - 40
        out.append(f'<rect x="{margin_left - 10}" y="{banner_top}" width="{bw}" height="{banner_h}" fill="#fdecea" '
                   f'stroke="{flow.accent}" stroke-width="2"/>')
        for k, line in enumerate(banner_rows):
            weight = ";font-weight:700" if k == 0 and flow.banner_title else ""
            out.append(f'<text class="lbl" x="{margin_left}" y="{banner_top + 20 + 18 * k}" '
                       f'style="fill:{flow.accent}{weight}">{escape(line)}</text>')

    # column headers, wrapped to two lines when long
    for i, st in enumerate(stages):
        lines = _wrap(st.title, 24)[:2]
        y0 = margin_top - 22 - (len(lines) - 1) * 16
        for k, line in enumerate(lines):
            out.append(f'<text class="hdr" x="{xs[i] + node_width / 2:.1f}" y="{y0 + k * 16}" '
                       f'text-anchor="middle">{escape(line)}</text>')

    # ribbons (drawn first so nodes sit on top)
    for l in flow.links:
        if l.src not in geom or l.dst not in geom:
            continue
        sx, sy, sw, sh = geom[l.src]
        tx, ty, tw, th = geom[l.dst]
        ss = side_scale(l.src, outflow.get(l.src, 0))
        ts = side_scale(l.dst, inflow.get(l.dst, 0))
        h_s = l.value * ss
        h_t = l.value * ts
        y0 = sy + out_cursor[l.src]
        y1 = ty + in_cursor[l.dst]
        out_cursor[l.src] += h_s
        in_cursor[l.dst] += h_t
        x0 = sx + sw
        x1 = tx
        cx = (x0 + x1) / 2
        d = (f"M{x0:.1f},{y0:.1f} C{cx:.1f},{y0:.1f} {cx:.1f},{y1:.1f} {x1:.1f},{y1:.1f} "
             f"L{x1:.1f},{y1 + h_t:.1f} C{cx:.1f},{y1 + h_t:.1f} {cx:.1f},{y0 + h_s:.1f} {x0:.1f},{y0 + h_s:.1f} Z")
        title = l.title or f"{l.src} → {l.dst}: {_fmt(l.value)}"
        out.append(f'<path class="rib" d="{d}" fill="{l.color}" fill-opacity="0.55" stroke="none">'
                   f'<title>{escape(title)}</title></path>')

    # nodes and labels. Labels in a column are pushed apart so that small adjacent nodes do not
    # overprint each other; a sublabel is shown only when there is room for it.
    LINE = 16.0
    for i, st in enumerate(stages):
        live = [n for n in st.nodes if n.key in geom]
        for n in live:
            x, y, w, h = geom[n.key]
            stroke = f' stroke="{n.stroke}" stroke-width="2.5"' if n.stroke else ' stroke="#333" stroke-width="0.8"'
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h:.1f}" fill="{n.color}"{stroke}>'
                       f'<title>{escape(n.label)}: {_fmt(size(n.key))}</title></rect>')
        # label placement pass
        placed: List[Tuple[Node, float, bool]] = []
        last_bottom = -1e9
        for n in live:
            x, y, w, h = geom[n.key]
            want = y + h / 2
            with_sub = bool(n.sublabel) and h >= 2 * LINE
            need = LINE * (2 if with_sub else 1)
            top = max(want - need / 2, last_bottom + 4)
            placed.append((n, top, with_sub))
            last_bottom = top + need
        for n, top, with_sub in placed:
            x, y, w, h = geom[n.key]
            count = _fmt(size(n.key))
            ty_ = top + LINE * 0.75
            loud = f' style="fill:{n.stroke or flow.accent};font-weight:700"' if n.emphasis else ""
            if i == 0:
                lx = x - 10
                out.append(f'<text class="lbl" x="{lx:.1f}" y="{ty_:.1f}" text-anchor="end"{loud}>{escape(n.label)}  '
                           f'<tspan font-weight="600">{count}</tspan></text>')
                if with_sub:
                    out.append(f'<text class="sub" x="{lx:.1f}" y="{ty_ + LINE:.1f}" text-anchor="end">{escape(n.sublabel)}</text>')
            else:
                lx = x + w + 10
                out.append(f'<text class="lbl" x="{lx:.1f}" y="{ty_:.1f}" text-anchor="start"{loud}>'
                           f'<tspan font-weight="600">{count}</tspan>  {escape(n.label)}</text>')
                if with_sub:
                    out.append(f'<text class="sub" x="{lx:.1f}" y="{ty_ + LINE:.1f}" text-anchor="start">{escape(n.sublabel)}</text>')
            # a thin leader line when the label had to move away from its node
            if abs(ty_ - (y + h / 2)) > LINE:
                lx0 = x - 6 if i == 0 else x + w + 6
                out.append(f'<line x1="{lx0:.1f}" y1="{y + h / 2:.1f}" x2="{lx0:.1f}" y2="{ty_ - 4:.1f}" stroke="#999" stroke-width="0.8"/>')

    # bands: one fixed-height strip per stage under the plot, joined across stages, each with its count
    by = margin_top + plot_h + 22
    bands = sorted(flow.bands, key=lambda b: b.stage)
    for a, b in zip(bands, bands[1:]):
        x0, x1 = xs[a.stage] + node_width, xs[b.stage]
        out.append(f'<path class="rib" d="M{x0:.1f},{by:.1f} L{x1:.1f},{by:.1f} L{x1:.1f},{by + band_h:.1f} '
                   f'L{x0:.1f},{by + band_h:.1f} Z" fill="{b.color}" fill-opacity="0.35" stroke="none">'
                   f'<title>{escape(a.label)}: {_fmt(a.count)} to {_fmt(b.count)}</title></path>')
    for b in bands:
        x = xs[b.stage]
        stroke = f' stroke="{b.stroke}" stroke-width="2.5"' if b.stroke else ' stroke="#666" stroke-width="0.8"'
        out.append(f'<rect class="band" x="{x:.1f}" y="{by:.1f}" width="{node_width}" height="{band_h:.0f}" '
                   f'fill="{b.color}"{stroke}><title>{escape(b.label)}: {_fmt(b.count)}</title></rect>')
        anchor, lx = ("end", x - 10) if b.stage == 0 else ("start", x + node_width + 10)
        out.append(f'<text class="lbl" x="{lx:.1f}" y="{by + 14:.1f}" text-anchor="{anchor}">'
                   f'<tspan font-weight="600">{_fmt(b.count)}</tspan>  {escape(b.label)}</text>')
        for k, line in enumerate(b.sublabel.split("\n") if b.sublabel else []):
            loud = f' style="fill:{b.stroke}"' if b.stroke and k else ""
            out.append(f'<text class="sub" x="{lx:.1f}" y="{by + 30 + 14 * k:.1f}" text-anchor="{anchor}"{loud}>'
                       f'{escape(line)}</text>')

    # footnote, wrapped to the drawable width (about 6.2 px per character at 11 px)
    fy = height - margin_bottom + 28
    max_chars = max(60, int((width - margin_left - 40) / 6.2))
    for line in flow.footnote:
        for piece in _wrap(line, max_chars):
            out.append(f'<text class="fn" x="{margin_left}" y="{fy}">{escape(piece)}</text>')
            fy += 15
        fy += 3
    out.append("</svg>")
    return "\n".join(out)


def _wrap(text: str, max_chars: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    return lines or [""]


def render_bars(groups: List[Tuple[str, str, List[Tuple[str, int, int]]]], title: str, subtitle: str,
                footnote: List[str], width: int = 1400, height: int = 560) -> str:
    """Stacked bars for decay across hops.

    groups: list of (group_label, color, [(hop_label, n_present, n_with_marker), ...]).
    Each bar: total height = n_present (log scale would hide the point, so linear, with the
    first hop as the reference); the lower, darker part is n_with_marker.
    """
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'font-family="Helvetica, Arial, sans-serif">', f'<rect width="{width}" height="{height}" fill="#fff"/>',
           '<style>.lbl{font-size:12px;fill:#222}.hdr{font-size:14px;font-weight:600;fill:#222}'
           '.ttl{font-size:20px;font-weight:600;fill:#111}.stt{font-size:13px;fill:#444}.fn{font-size:11px;fill:#555}</style>']
    out.append(f'<text class="ttl" x="40" y="34">{escape(title)}</text>')
    out.append(f'<text class="stt" x="40" y="56">{escape(subtitle)}</text>')
    top, bottom, left = 100, 140, 40
    plot_h = height - top - bottom
    panel_w = (width - 2 * left) / len(groups)
    for gi, (glabel, color, bars) in enumerate(groups):
        px = left + gi * panel_w
        out.append(f'<text class="hdr" x="{px + panel_w / 2:.1f}" y="{top - 20}" text-anchor="middle">{escape(glabel)}</text>')
        ref = max(b[1] for b in bars) or 1
        bw = (panel_w - 40) / len(bars)
        for bi, (hl, n, m) in enumerate(bars):
            bx = px + 20 + bi * bw
            h_n = plot_h * n / ref
            h_m = plot_h * m / ref
            y_n = top + plot_h - h_n
            y_m = top + plot_h - h_m
            out.append(f'<rect x="{bx + 6:.1f}" y="{y_n:.1f}" width="{bw - 12:.1f}" height="{max(h_n, 1):.1f}" fill="{color}" fill-opacity="0.3"/>')
            out.append(f'<rect x="{bx + 6:.1f}" y="{y_m:.1f}" width="{bw - 12:.1f}" height="{max(h_m, 1):.1f}" fill="{color}"/>')
            share = f"{100 * m / n:.0f}%" if n else "–"
            out.append(f'<text class="lbl" x="{bx + bw / 2:.1f}" y="{top + plot_h + 18}" text-anchor="middle">{escape(hl)}</text>')
            out.append(f'<text class="lbl" x="{bx + bw / 2:.1f}" y="{max(y_n - 22, top - 2):.1f}" text-anchor="middle">{_fmt(n)}</text>')
            out.append(f'<text class="lbl" x="{bx + bw / 2:.1f}" y="{max(y_n - 8, top + 12):.1f}" text-anchor="middle" fill="#555">{share}</text>')
    out.append(f'<text class="lbl" x="{width / 2:.1f}" y="{top + plot_h + 40}" text-anchor="middle" fill="#555">'
               f'count above each bar; percentage = share of those sentences with the marker still attached</text>')
    fy = height - bottom + 70
    max_chars = max(60, int((width - 2 * left) / 6.2))
    for line in footnote:
        for piece in _wrap(line, max_chars):
            out.append(f'<text class="fn" x="{left}" y="{fy}">{escape(piece)}</text>')
            fy += 15
    out.append("</svg>")
    return "\n".join(out)
