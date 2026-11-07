"""The report (SPEC §2.5, §7): ledger.jsonl, counts.csv, flow.svg, the count lines with their denominators."""
from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .check import fidelity_score
from .commitment import OPTIONAL_FIELDS
from .drift import DRIFT_CLASSES, ESCALATED, FAITHFUL, UNDECLARED, describe
from .serialise import sha256, short

ORIGIN_COLOURS = {"MUST": "#3b7a57", "MUST_NOT": "#2f5d8a", "SHOULD": "#c7a24a", "MAY": "#8a7f8d",
                  "hedged": "#e5862e", "not_found": "#d9b92f", "flat": "#6f9f6f"}
# The map's colours (SPEC §7). LOSS is decided by what happened to the field, never by the class name alone: every
# commitment field whose label is a change (any drift class but PRESERVED and RESTATED) and every field named by a
# reason that held a commit is drawn in it. The colours below are for the rest: faithful, undecided, and claims.
LOSS = "#c0392b"
RED = LOSS
CLAIM_BAND = "#b8b8b8"  # claim sentences, matched by shared words: one grey band per link unless expanded
NODE_COLOURS = {"PRESERVED": "#5b8c5a", "restored_verbatim": "#2e8b57", "restored_equivalent": "#2e8b57",
                "RESTATED": "#7ba87a", "ESCALATED": "#c7a24a", "UNDECLARED": "#dcdcdc", "CHANGED": "#d98c5f",
                "DROPPED": "#9a9a9a"}
FAITHFUL_LABELS = ("PRESERVED", "RESTATED", "restored_verbatim", "restored_equivalent")
UNDECIDED_LABELS = (UNDECLARED, ESCALATED)
LABEL_ORDER = ["PRESERVED", "RESTATED", "restored_verbatim", "restored_equivalent", "CHANGED", "ESCALATED", "DROPPED",
               UNDECLARED]
PASSING = ("PRESERVED", "RESTATED")
# the shared form names a field by its kind, never by its dimension, id or value
FIELD_WORDS = {"obligor": "the obligor", "obligee": "the obligee", "act": "the act", "modality": "the modality",
               "condition": "a condition", "limit": "a limit", "deadline": "the deadline",
               "acceptance_criteria": "an acceptance criterion", "purpose": "the purpose"}
CLASS_WORDS = {"DROPPED": "missing", "ADDED_UNPARENTED": "added with no parent", "WIDENED": "wider than agreed",
               "NARROWED": "narrower than agreed", "STRENGTHENED": "stronger than agreed",
               "WEAKENED": "weaker than agreed", "RECONDITIONED": "under another condition",
               "REASSIGNED": "assigned to another party", "RESCHEDULED": "on another date",
               UNDECLARED: "not declared, nothing to compare", ESCALATED: "not decided by tier 1"}
_HOLD_FIELD = re.compile(r"^DANGER commitment (\S+) (\S+) (\S+) \((tier \d|ledger)\) at (\S+) ")
_HOLD_UNIT = re.compile(r"^DANGER commitment (\S+) (DROPPED|UNDECLARED) \((tier \d|ledger)\) at (\S+) ")
_HOLD_CLAIM = re.compile(r'^DANGER claim \[([^\]]+)\] (.+?) \((tier \d|ledger)\) at (\S+) against \S+ v\S+: "(.*)"$')
_HOLD_TIER = re.compile(r"^DANGER tier (\S+) failed: .* at (\S+)$")


@dataclass
class RecallRow:
    """One row of the recall table (SPEC §7): per tier, per drift class, per release, on a labelled sample."""
    tier: int
    drift_class: str
    release: str
    labelled: int
    found: int

    def __post_init__(self) -> None:
        if self.tier not in (2, 3):
            raise ValueError(f"tier {self.tier!r} has no recall to report; tier 1 is deterministic, so a recall row "
                             f"is for tier 2 or tier 3")
        if self.drift_class not in DRIFT_CLASSES:
            raise ValueError(f"drift_class {self.drift_class!r} is not one of {', '.join(DRIFT_CLASSES)}")
        if self.labelled <= 0:
            raise ValueError("a recall row needs a labelled sample; 0 of 0 is not a measurement")
        if not 0 <= self.found <= self.labelled:
            raise ValueError(f"found {self.found} of {self.labelled} labelled fields is not a count")

    def line(self) -> str:
        return (f"recall tier {self.tier} {self.drift_class} {self.found} of {self.labelled} labelled fields, "
                f"release {self.release}")

    def record(self, run_id: str, pipeline: str) -> dict:
        return {"kind": "recall", "format": "as-agreed/1", "pipeline": pipeline, "run_id": run_id, "tier": self.tier,
                "drift_class": self.drift_class, "release": self.release, "labelled": self.labelled,
                "found": self.found}


@dataclass
class Report:
    run_id: str
    code: int
    unit_lines: List[str]
    count_lines: List[str]
    gate_line: str
    danger_lines: List[str]
    tail_lines: List[str]
    csv_path: str
    svg_path: str
    ledger_path: str
    proxy: bool
    min_words: int
    tiers: Dict[str, int] = field(default_factory=dict)
    fidelity: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    drafter: Optional[Dict[str, str]] = None  # who drafted a proxy record's commitments (SPEC §1.1, draft 8)

    def text(self, per_unit: bool = True) -> str:
        out: List[str] = []
        if per_unit:
            out.extend(self.unit_lines)
        out.append("counts:")
        out.extend("  " + ln for ln in self.count_lines)
        out.append(self.gate_line)
        out.extend("  " + ln for ln in self.danger_lines)
        out.extend(self.tail_lines)
        return "\n".join(out)


def _by_kind(records: List[dict], kind: str) -> List[dict]:
    return [r for r in records if r.get("kind") == kind]


def _paths(c: dict) -> List[str]:
    out = ["obligor", "obligee", "act", "modality"]
    cond = c.get("condition")
    if isinstance(cond, dict):
        out += [f"condition.{k}" for k in cond]
    elif cond:
        out.append("condition")
    out += [f"limit.{d}" for d in (c.get("limit") or {})]
    if c.get("deadline"):
        out.append("deadline")
    out += [f"acceptance_criteria.{x['criterion_id']}" for x in c.get("acceptance_criteria") or []]
    return out


def _label(v: dict) -> str:
    return v.get("trajectory") or v.get("drift_class") or v.get("status") or v.get("undecided") or ""


def _better(old: Optional[dict], new: dict) -> dict:
    """The cell's verdict: decided over undecided, drifted over faithful; among equals, the later."""
    if old is None:
        return new

    def rank(v: dict) -> Tuple[bool, bool]:
        label = v.get("drift_class") or v.get("status")
        return bool(label), bool(label) and label not in FAITHFUL

    return new if rank(new) >= rank(old) else old


def build(records: List[dict], out_dir: str, *, title: str = "", run_id: Optional[str] = None,
          recall: Iterable[Any] = (), expand_claims: bool = False, values: bool = False) -> Report:
    """Build the three files and the printed lines from a run's records.

    The map is in its shared form unless the caller asks: claim sentences collapsed into one grey band per link
    (`expand_claims=True` draws one ribbon per claim), and the title's verdict sentence in kinds and counts
    (`values=True` names the commitment, the field and the agreed and arrived values, so the file no longer travels)."""
    os.makedirs(out_dir, exist_ok=True)
    heads = _by_kind(records, "as_agreed")
    if run_id is not None:
        heads = [h for h in heads if h.get("run_id") == run_id] or heads
    if not heads:
        raise ValueError("no as-agreed record in the records; nothing to report")
    head = heads[-1]
    rid = head["run_id"]
    recs = [r for r in records if r.get("run_id") == rid]
    proxy = bool(head.get("proxy"))
    min_words = int(head.get("min_words", 3))
    recall = list(recall)

    commitments = OrderedDict((c["commitment_id"], c) for c in _by_kind(recs, "commitment")
                              if c.get("as_agreed") == head["id"])
    claims = OrderedDict((c["id"], c) for c in _by_kind(recs, "claim") if c.get("as_agreed") == head["id"])
    links = [l for l in _by_kind(recs, "link") if l.get("role") != "order"]
    columns = [l["id"] for l in links]
    verdicts = _by_kind(recs, "verdict")
    cell: Dict[Tuple[str, str, str], dict] = {}
    for v in verdicts:
        k = (v["link"], v["unit"], v["field"])
        cell[k] = _better(cell.get(k), v)

    keys: "OrderedDict[Tuple[str, str], Tuple[str, str, bool]]" = OrderedDict()
    for cid, c in commitments.items():
        for path in _paths(c):
            keys[(cid, path)] = ("commitment", c["modality"], c["modality"] in ("MUST", "MUST_NOT"))
    for v in verdicts:
        if v["unit_kind"] == "commitment" and (v["unit"], v["field"]) not in keys:
            keys[(v["unit"], v["field"])] = ("commitment", v["unit_class"], bool(v.get("must")))
    for cid, c in claims.items():
        keys[(cid, "status")] = ("claim", c["origin"], bool(c.get("must")))

    # counts.csv: one row per field or claim, one column per link, labels only
    csv_path = os.path.join(out_dir, "counts.csv")
    run_col = short(sha256(rid))
    header = ["run", "unit", "field", "kind", "class", "must", "proxy"] + columns
    rows: List[List[str]] = []
    for (uid, fld), (kind, cls, must) in keys.items():
        rows.append([run_col, uid, fld, kind, cls, "yes" if must else "no", "yes" if proxy else "no"]
                    + [_label(cell[(col, uid, fld)]) if (col, uid, fld) in cell else "" for col in columns])
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    ledger_path = os.path.join(out_dir, "ledger.jsonl")
    with open(ledger_path, "w", encoding="utf-8") as f:
        for r in recs + [row.record(rid, head.get("pipeline", "default")) for row in recall]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    svg_path = os.path.join(out_dir, "flow.svg")

    # per-unit lines, count lines
    unit_lines: List[str] = []
    count_lines: List[str] = []
    fidelity: Dict[str, Dict[str, Any]] = {}
    for col in columns:
        unit_lines.append(col)
        for uid in OrderedDict((u, None) for (u, _) in keys if keys[(u, _)][0] == "commitment"):
            silent = []
            for (u, fld), meta in keys.items():
                if u != uid or (col, u, fld) not in cell:
                    continue
                v = cell[(col, u, fld)]
                if v.get("undecided") == UNDECLARED and not v.get("drift_class"):
                    silent.append(fld)
                    continue
                # a proposal prints the class its answer would give, so two readers compare without a threshold-0 run
                proposal = f"proposed_class {v['proposed_class']}  " if v.get("proposed_class") else ""
                unit_lines.append(f"  {_label(v):<18} commitment {uid} {fld}  {proposal}{v.get('reason', '')}")
            if silent:
                unit_lines.append(f"  {UNDECLARED:<18} commitment {uid} {', '.join(silent)}")
        for cid, c in claims.items():
            v = cell.get((col, cid, "status"))
            if v:
                detail = ""
                if v.get("marker_fates") and v.get("status") in ("CHANGED", "DANGER"):
                    detail = "  " + ", ".join(f"{k} {x}" for k, x in v["marker_fates"].items() if x not in ("n/a", "kept", "unknown"))
                shown = _label(v) + ("!" if v.get("status") == "DROPPED" and c.get("must") else "")
                unit_lines.append(f"  {shown:<18} claim [{c['origin']}{', must' if c.get('must') else ''}] {cid}  \"{c['text'][:90]}\"{detail}")
        fields = [cell[(col, u, f)] for (u, f), meta in keys.items() if meta[0] == "commitment" and (col, u, f) in cell]
        cv = [cell[(col, u, "status")] for (u, f), meta in keys.items() if meta[0] == "claim" and (col, u, "status") in cell]
        if fields:
            score = fidelity_score(fields)
            fidelity[col] = score
            count_lines.append(f"commitment fidelity {score['preserved']} of {score['decided']} decided fields preserved at {col}")
            esc = sum(1 for v in fields if not v.get("drift_class") and v.get("undecided") == ESCALATED)
            und = sum(1 for v in fields if not v.get("drift_class") and v.get("undecided") == UNDECLARED)
            count_lines.append(f"fields {esc} of {len(fields)} escalated at {col}")
            count_lines.append(f"fields {und} of {len(fields)} undeclared at {col}")
            proposed = Counter(v["proposed_class"] for v in fields if not v.get("drift_class") and v.get("proposed_class"))
            if proposed:  # printed only where there is one, so a run without proposals reads as before (draft 9)
                count_lines.append(f"fields {sum(proposed.values())} of {len(fields)} proposed at {col} ("
                                   + ", ".join(f"{k} {n}" for k, n in sorted(proposed.items())) + ")")
        if cv:
            count_lines.append(f"claims {sum(1 for v in cv if v.get('status') == 'PRESERVED')} of {len(cv)} preserved at {col}")
            count_lines.append(f"claims {sum(1 for v in cv if v.get('status') == 'DANGER')} of {len(cv)} danger at {col}")
            count_lines.append(f"claims {sum(1 for v in cv if v.get('status') == 'DROPPED')} of {len(cv)} dropped at {col}")
    tiers = Counter(str(v.get("tier")) for v in verdicts)
    total = len(verdicts)
    count_lines.append(f"verdicts {tiers.get('1', 0)} of {total} tier 1, {tiers.get('ledger', 0)} of {total} ledger, "
                       f"{tiers.get('2', 0)} of {total} tier 2, {tiers.get('3', 0)} of {total} tier 3")
    count_lines.append(f"retries {len(_by_kind(recs, 'retry'))} of {len(links)} link attempts")
    count_lines.extend(row.line() for row in recall)

    holds = [h for h in _by_kind(recs, "hold") if (h.get("policy") or {}).get("on_loss") == "hold"]
    last = holds[-1] if holds else None
    decided_fields = sum(1 for v in cell.values() if v.get("drift_class") or v.get("status"))
    danger: List[str] = []
    if decided_fields == 0:
        code = 2
        gate = ("gate: NOTHING TO CHECK (no field and no claim was decided at any link: nothing was declared to "
                "compare against the as-agreed record)")
    elif last is None:
        code = 2
        gate = "gate: NOT GATED (no commit step ran, so nothing was held before anything took effect)"
    elif last.get("passed"):
        code = 0
        n = sum(1 for h in holds if not h.get("passed"))
        gate = f"gate: pass{' after ' + str(n) + ' retry' + ('' if n == 1 else 'ies') if n else ''}"
    else:
        code = int(last.get("code", 1))
        danger = list(last.get("lines", []))
        gate = f"gate: HOLD ({len(danger)} reasons)"
    # a schema failure decides the run whatever an earlier step held: the step it names never reached its hold,
    # so the commit was withheld and the caller saw code 3
    held_links = {h.get("link") for h in _by_kind(recs, "hold")}
    open_failures = [f for f in _by_kind(recs, "schema_failure") if f.get("link") not in held_links]
    if open_failures:
        code = 3
        gate = (f"gate: schema failed at {open_failures[-1].get('link')} "
                f"({'; '.join(open_failures[-1].get('problems', []))})")
    _draw(rows, header, columns, svg_path, title or f"Where did the commitments go? {head.get('pipeline', '')}", proxy,
          min_words, gate=_gate_view(recs, holds, code, gate, commitments, claims, cell), expand_claims=expand_claims,
          values=values)
    tail: List[str] = []
    drafter = head.get("drafter") if proxy else None
    if proxy:
        tail.append("as-agreed: proxy (task as stated; no order was recorded)"
                    + (f"; commitments drafted by {drafter.get('model')} {drafter.get('version')} on "
                       f"{drafter.get('date')}, prompt {drafter.get('prompt_id')}" if drafter else ""))
    tail.extend(_uncheckable(commitments))
    tail.extend(_attribution(keys, cell, columns))
    if not recall:
        tail.append("recall: not measured (no labelled sample given to this report)")
    tail.append(f"min_words {min_words}")
    tail.append(f"report: ledger.jsonl, counts.csv, flow.svg written to {out_dir}    code {code}")
    return Report(rid, code, unit_lines, count_lines, gate, danger, tail, csv_path, svg_path, ledger_path, proxy,
                  min_words, dict(tiers), fidelity, drafter)


def _uncheckable(commitments: Dict[str, dict]) -> List[str]:
    """A field nobody declared cannot be checked, and an unverifiable criterion is a finding, not a tool failure."""
    out: List[str] = []
    for cid, c in commitments.items():
        unset = [f for f in OPTIONAL_FIELDS if not c.get(f)]
        if unset:
            out.append(f"uncheckable: {cid} {', '.join(unset)} (not declared at registration)")
        for x in c.get("acceptance_criteria") or []:
            if x.get("verification") == "unverifiable":
                out.append(f"unverifiable: {cid} acceptance_criteria.{x['criterion_id']} \"{x['text'][:80]}\"")
    return out


def _attribution(keys: Dict[Tuple[str, str], Any], cell: Dict[Tuple[str, str, str], dict], columns: List[str]) -> List[str]:
    """For each field that ended drifted, the link where its drift first appeared: which hop, not which agent."""
    out: List[str] = []
    for (uid, fld), meta in keys.items():
        if meta[0] != "commitment":
            continue
        seen = [(col, cell[(col, uid, fld)].get("drift_class")) for col in columns if (col, uid, fld) in cell]
        decided = [(col, c) for col, c in seen if c]
        if not decided or decided[-1][1] in FAITHFUL:
            continue
        first = next(col for col, c in decided if c not in FAITHFUL)
        out.append(f"drift first seen: {uid} {fld} {decided[-1][1]} at {first}")
    return out


def _gate_view(recs: List[dict], holds: List[dict], code: int, gate: str, commitments: Dict[str, dict],
               claims: Dict[str, dict], cell: Dict[Tuple[str, str, str], dict]) -> Dict[str, Any]:
    """What the map needs from the gate: the fields and claims a hold named, the hold to show, and the run's code."""
    held_fields: set = set()
    held_claims: Dict[str, set] = {}
    claim_reasons: List[Tuple[str, str, str]] = []  # (link, origin class, what happened), for the title line
    failing = [h for h in holds if not h.get("passed")]
    texts = {c.get("text"): cid for cid, c in claims.items()}
    for h in failing:
        for ln in h.get("lines", []):
            m = _HOLD_UNIT.match(ln)
            if m:
                held_fields.add((m.group(4), m.group(1), "*"))
                continue
            m = _HOLD_FIELD.match(ln)
            if m:
                held_fields.add((m.group(5), m.group(1), m.group(2)))
                continue
            m = _HOLD_CLAIM.match(ln)
            if m:
                claim_reasons.append((m.group(4), m.group(1), m.group(2)))
                if m.group(5) in texts:
                    held_claims.setdefault(m.group(4), set()).add(texts[m.group(5)])
    return {"held_fields": held_fields, "held_claims": held_claims, "claim_reasons": claim_reasons, "failing": failing, "last": holds[-1] if holds else None,
            "code": code, "gate": gate, "commitments": commitments, "cell": cell, "retries": len(_by_kind(recs, "retry"))}


def _field_kind(fld: str) -> str:
    return FIELD_WORDS.get(fld.split(".", 1)[0], "a field")


def _class_words(label: str) -> str:
    words = CLASS_WORDS.get(label)
    return f"{words} ({label})" if words else label


def _agreed_value(c: Optional[dict], fld: str) -> str:
    """The value form's agreed side, read from the commitment record."""
    if not c:
        return "?"
    top, _, rest = fld.partition(".")
    if top == "limit":
        return describe((c.get("limit") or {}).get(rest))
    if top == "condition":
        cond = c.get("condition")
        return str(cond.get(rest) if isinstance(cond, dict) else cond)
    if top == "acceptance_criteria":
        crit = next((x for x in c.get("acceptance_criteria") or [] if x.get("criterion_id") == rest), {})
        return f'"{crit.get("text", "")}"' + (f" ({crit['field']} = {crit.get('equals')})" if crit.get("field") else "")
    return str(c.get(top))


def _value_pair(c: Optional[dict], fld: str, v: dict) -> Tuple[str, str]:
    reason = str(v.get("reason", ""))
    m = re.match(r"^(.*?) where (.*?) was agreed\b(.*)$", reason)
    if m:
        return m.group(2), m.group(1) + m.group(3)
    return _agreed_value(c, fld), reason or v.get("drift_class") or v.get("undecided") or "?"


def _verdict(rows: List[List[str]], header: List[str], columns: List[str], g: Dict[str, Any],
             values: bool) -> Tuple[str, str]:
    """The title line: agreed X, arrived Y, at step Z, and what the gate did, for the first changed MUST field; and
    the counts that go with it, each with its denominator."""
    first = len(header) - len(columns)
    unit_at, fld_at, kind_at, class_at, must_at = (header.index(k) for k in ("unit", "field", "kind", "class", "must"))
    com = [r for r in rows if r[kind_at] == "commitment"]
    must = [r for r in com if r[must_at] == "yes"]

    def changed(r: List[str], i: int) -> bool:
        lab = r[first + i]
        return bool(lab) and lab not in FAITHFUL_LABELS and lab not in UNDECIDED_LABELS

    def held(r: List[str], i: int) -> bool:
        col = columns[i]
        return (col, r[unit_at], r[fld_at]) in g["held_fields"] or (col, r[unit_at], "*") in g["held_fields"]

    pick = None
    for test, pool in ((changed, must), (held, must), (changed, com), (held, com)):
        pick = next(((r, i) for i in range(len(columns)) for r in pool if r[first + i] and test(r, i)), None)
        if pick:
            break
    failing, code, last = g["failing"], g["code"], g["last"]
    decided_must = [r for r in must if any(r[first + i] and r[first + i] not in UNDECIDED_LABELS for i in range(len(columns)))]
    changed_must = [r for r in must if any(changed(r, i) for i in range(len(columns)))]
    counts = f"{len(changed_must)} of {len(decided_must)} decided MUST fields changed"
    if failing:
        n = len(failing[-1].get("lines", []))
        counts += f"; {n} hold reason{'' if n == 1 else 's'}"
    if pick is None:
        if g.get("claim_reasons"):
            col, cls, what = g["claim_reasons"][0]
            return f"agreed a claim sentence [{cls}], arrived {what}, at step {col}, held", counts
        if code == 3:
            return g["gate"][6:], counts
        if code == 0:
            return f"agreed {len(decided_must)} decided MUST fields, arrived as agreed, gate pass", counts
        return f"no changed field found; {g['gate'][6:]}", counts
    r, i = pick
    col, lab = columns[i], r[first + i]
    if values:
        c = g["commitments"].get(r[unit_at])
        agreed, arrived = _value_pair(c, r[fld_at], g["cell"].get((col, r[unit_at], r[fld_at]), {}))
        x, y = f"{r[unit_at]} {r[fld_at]} {agreed}", f"{arrived} ({lab})"
    elif (col, r[unit_at], "*") in g["held_fields"] and lab in UNDECIDED_LABELS:
        x, y = f"a {r[class_at]} commitment", (f"with no field declared, nothing to compare ({lab})" if lab == UNDECLARED
                                               else f"with no field decided by tier 1 ({lab})")
    else:
        x, y = f"{_field_kind(r[fld_at])} of a {r[class_at]} commitment", _class_words(lab)
    held_here = [h for h in failing if h.get("link") == col]
    if g.get("unknown"):
        fate = "the gate is not in counts.csv"
    elif code == 3:
        fate = g["gate"][6:]
    elif held_here and code == 1 and last is not None and last.get("link") == col:
        fate = "held"
    elif held_here and code == 0 and last is not None:
        fate = f"held, then passed at {last.get('link')} after {g['retries']} retr{'y' if g['retries'] == 1 else 'ies'}"
    elif code == 1 and last is not None:
        fate = f"held at {last.get('link')}"
    elif code == 0:
        fate = "not held (the commit passed)"
    else:
        fate = "not held (no commit step ran)"
    return f"agreed {x}, arrived {y}, at step {col}, {fate}", counts


def _reason_rows(g: Dict[str, Any], kinds: Dict[str, str], values: bool, limit: int = 6) -> Tuple[str, List[str]]:
    """The banner: the reasons of the hold that decided the run, in the shared form unless values are asked for."""
    if not g["failing"]:
        return "", []
    h = g["failing"][-1]
    lines = list(h.get("lines", []))
    out: List[str] = []
    for ln in lines[:limit]:
        if values:
            out.append(ln if len(ln) <= 170 else ln[:167] + "...")
            continue
        m = _HOLD_UNIT.match(ln)
        if m:
            out.append(f"a {kinds.get(m.group(1), '')} commitment, every field: {_class_words(m.group(2))}, "
                       f"{m.group(3)}, at {m.group(4)}")
            continue
        m = _HOLD_FIELD.match(ln)
        if m:
            out.append(f"{_field_kind(m.group(2))} of a {kinds.get(m.group(1), '')} commitment: "
                       f"{_class_words(m.group(3))}, {m.group(4)}, at {m.group(5)}")
            continue
        m = _HOLD_CLAIM.match(ln)
        if m:
            out.append(f"a claim sentence [{m.group(1)}]: {m.group(2)}, {m.group(3)}, at {m.group(4)}")
            continue
        m = _HOLD_TIER.match(ln)
        out.append(f"tier {m.group(1)} failed at {m.group(2)}" if m else "a reason shown only in the value form")
    if len(lines) > limit:
        out.append(f"and {len(lines) - limit} more of {len(lines)} reasons (in the printed report)")
    n = len(lines)
    head = f"HELD at {h.get('link')}: {n} reason{'' if n == 1 else 's'}, nothing took effect"
    last = g["last"]
    if g["code"] == 0 and last is not None and last is not h:
        head += f" there; passed at {last.get('link')} after {g['retries']} retr{'y' if g['retries'] == 1 else 'ies'}"
    return head, out


def _draw(rows: List[List[str]], header: List[str], columns: List[str], svg_path: str, title: str, proxy: bool,
          min_words: int, gate: Optional[Dict[str, Any]] = None, expand_claims: bool = False,
          values: bool = False) -> None:
    from .flowsvg import Band, Flow, Node, Stage, render
    if not columns or not rows:
        return
    g = gate or {"held_fields": set(), "held_claims": {}, "claim_reasons": [], "failing": [], "last": None, "code": 2,
                 "gate": "gate: NOT GATED", "commitments": {}, "cell": {}, "retries": 0}
    unit_at, fld_at, kind_at, class_at, must_at = (header.index(k) for k in ("unit", "field", "kind", "class", "must"))
    first = len(header) - len(columns)
    drawn = [r for r in rows if expand_claims or r[kind_at] == "commitment"]
    band_rows = [] if expand_claims else [r for r in rows if r[kind_at] == "claim"]

    def loss(r: List[str], i: int) -> bool:
        lab, col = r[first + i], columns[i]
        if r[kind_at] == "claim":
            return lab == "DANGER" or (lab == "DROPPED" and r[must_at] == "yes") or \
                r[unit_at] in g["held_claims"].get(col, ())
        if lab and lab not in FAITHFUL_LABELS and lab not in UNDECIDED_LABELS:
            return True
        return (col, r[unit_at], r[fld_at]) in g["held_fields"] or (col, r[unit_at], "*") in g["held_fields"]

    def node_label(lab: str, is_loss: bool, kind: str) -> str:
        shown = lab + ("!" if kind == "claim" and lab == "DROPPED" and is_loss else "")
        return f"{shown}, held" if is_loss and kind == "commitment" else shown

    origin_nodes: "OrderedDict[str, Node]" = OrderedDict()
    for r in drawn:
        key = f"o::{r[kind_at]}::{r[class_at]}"
        if key not in origin_nodes:
            origin_nodes[key] = Node(key, f"{r[kind_at]} {r[class_at]}", ORIGIN_COLOURS.get(r[class_at], "#4a6fa5"))
    stages = [Stage("fields and claims at origin" if expand_claims else
                    "commitment fields at origin" if drawn else "no commitment declared", list(origin_nodes.values()))]

    def rank(key: str) -> Tuple[int, int]:
        _, kind, lab, flag = key.split("::")
        return (0 if flag == "loss" else 1, LABEL_ORDER.index(lab) if lab in LABEL_ORDER else 50)

    for i, col in enumerate(columns):
        keys = sorted({f"{col}::{r[kind_at]}::{r[first + i]}::{'loss' if loss(r, i) else 'ok'}"
                       for r in drawn if r[first + i]}, key=lambda k: rank(k))
        nodes = []
        for k in keys:
            _, kind, lab, flag = k.split("::")
            if flag == "loss":
                nodes.append(Node(k, node_label(lab, True, kind), LOSS, stroke=LOSS, emphasis=True, min_px=14))
            else:
                nodes.append(Node(k, node_label(lab, False, kind) + (" (claim)" if kind == "claim" else ""),
                                  NODE_COLOURS.get(lab, "#b8b8b8")))
        stages.append(Stage(f"at {col}", nodes))
    flow_title, verdict_counts = _verdict(rows, header, columns, g, values)
    kinds = OrderedDict((r[unit_at], r[class_at]) for r in rows if r[kind_at] == "commitment")
    banner_title, banner = _reason_rows(g, kinds, values)
    claims_note = ("claim sentences, matched by shared words, are drawn one ribbon each" if expand_claims else
                   "claim sentences, matched by shared words, are one grey band per link")
    flow = Flow(stages, title=flow_title, subtitle=f"{verdict_counts}. {title}. One ribbon per commitment field per link; red = a "
                                                    f"change to what was agreed, or a field that held the commit; "
                                                    f"{claims_note}. "
                                                    + ("Values shown: do not share this file." if values else
                                                       "Counts only."),
                banner=banner, banner_title=banner_title, title_accent=bool(g["failing"]), wrap=112)
    counts: Counter = Counter()
    for r in drawn:
        prev = f"o::{r[kind_at]}::{r[class_at]}"
        colour = ORIGIN_COLOURS.get(r[class_at], "#4a6fa5")
        for i, col in enumerate(columns):
            lab = r[first + i]
            if not lab:
                continue
            is_loss = loss(r, i)
            dst = f"{col}::{r[kind_at]}::{lab}::{'loss' if is_loss else 'ok'}"
            counts[(prev, dst, LOSS if is_loss else colour)] += 1
            prev = dst
    # ribbons stack in the order of their target nodes, so a loss at the top of a column leaves from the top
    position = {n.key: k for st in stages for k, n in enumerate(st.nodes)}
    for (src, dst, colour), n in sorted(counts.items(), key=lambda kv: (position.get(kv[0][0], 0),
                                                                         position.get(kv[0][1], 0))):
        flow.add(src, dst, n, colour, f"{src.replace('::loss', ', held').replace('::ok', '')} -> "
                                      f"{dst.replace('::loss', ', held').replace('::ok', '')}: {n}")
    if band_rows:
        word = lambda n: "claim sentence, word-matched" if n == 1 else "claim sentences, word-matched"
        flow.bands.append(Band(0, word(len(band_rows)), len(band_rows), CLAIM_BAND,
                               sublabel="the claim ledger's rows, collapsed"))
        for i, col in enumerate(columns):
            here = [r[first + i] for r in band_rows if r[first + i]]
            if not here:
                continue
            tally = Counter(here)
            held_n = len(g["held_claims"].get(col, ()))
            sub = ", ".join(f"{tally[k]} {k.lower()}" for k in ("PRESERVED", "CHANGED", "DROPPED", "DANGER") if tally.get(k))
            if held_n:
                sub += f"\n{held_n} of {len(here)} in the hold"
            flow.bands.append(Band(i + 1, word(len(here)), len(here), CLAIM_BAND, sublabel=sub,
                                   stroke=LOSS if held_n else None))
    n_com = sum(1 for r in rows if r[kind_at] == "commitment")
    flow.footnote = [f"{n_com} commitment fields and {len(rows) - n_com} claims, {len(columns)} links. Tier 1 and the "
                     f"ledger; min_words {min_words}." + (" as-agreed: proxy (task as stated)." if proxy else ""),
                     ("The agreed and arrived values are in this file." if values else
                      "No act, condition, value or claim text in this file.")]
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(render(flow, width=1500, height=720 if drawn else 300, margin_left=230, margin_right=300))


RUN_COLUMNS = ("run", "unit", "field", "kind", "class", "must", "proxy")


def draw_counts(csv_path: str, svg_path: str, *, title: str = "", expand_claims: bool = False) -> str:
    """Draw a run's counts.csv as the report draws it. The CSV holds labels only, so the map knows which fields
    changed but not which a hold named, nor the gate: the title says so."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        table = list(csv.reader(f))
    header, rows = table[0], table[1:]
    columns = header[len(RUN_COLUMNS):]
    proxy = any(r[header.index("proxy")] == "yes" for r in rows)
    gate = {"held_fields": set(), "held_claims": {}, "claim_reasons": [], "failing": [], "last": None, "code": 2,
            "gate": "gate: not in counts.csv", "commitments": {}, "cell": {}, "retries": 0, "unknown": True}
    _draw(rows, header, columns, svg_path, title or os.path.basename(os.path.dirname(os.path.abspath(csv_path))),
          proxy, 3, gate=gate, expand_claims=expand_claims)
    return svg_path


def redraw(ledger_path: str, svg_path: str, *, title: str = "", expand_claims: bool = False,
           values: bool = False) -> str:
    """Draw a run's map again from its ledger.jsonl, e.g. with the claims expanded or the values shown; the other files
    of the run are rebuilt beside the map in a scratch folder and left alone."""
    import tempfile
    with open(ledger_path, encoding="utf-8") as f:
        records = [json.loads(ln) for ln in f if ln.strip()]
    scratch = tempfile.mkdtemp(prefix="as-agreed-redraw-")
    rep = build(records, scratch, title=title, expand_claims=expand_claims, values=values)
    with open(rep.svg_path, encoding="utf-8") as src, open(svg_path, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    return svg_path
