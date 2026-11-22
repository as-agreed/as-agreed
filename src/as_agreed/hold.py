"""The hold (SPEC §2.3): the policy, what holds, the DANGER lines, the codes."""
from __future__ import annotations

import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .check import Verdict
from .commitment import FORMAT, AsAgreed, Claim
from .drift import (ADDED_UNPARENTED, DROPPED, ESCALATED, FAITHFUL, NARROWED, RECONDITIONED, REASSIGNED, RESCHEDULED, STRENGTHENED,
                    UNDECLARED, WEAKENED, WIDENED)
from .match import marker_summary

FAIL_ON = ("danger", "dropped_must", "changed", "dropped")
DEFAULT_FAIL_ON = ("danger", "dropped_must")
CHANGES = (ADDED_UNPARENTED, WIDENED, STRENGTHENED, WEAKENED, RECONDITIONED, REASSIGNED, RESCHEDULED)  # at any modality
LOSSES = (DROPPED, NARROWED)                                                       # where the commitment binds


@dataclass
class Policy:
    role: str = "intermediate"          # intermediate | commit
    on_loss: str = "record"             # record | hold
    fail_on: Tuple[str, ...] = DEFAULT_FAIL_ON
    retries: int = 1
    on_undecided: str = "record"        # record | hold

    def __post_init__(self) -> None:
        bad = [f for f in self.fail_on if f not in FAIL_ON]
        if bad:
            raise ValueError(f"fail_on values not known: {', '.join(bad)}; use any of {', '.join(FAIL_ON)}")
        if self.on_loss not in ("record", "hold"):
            raise ValueError("on_loss is 'record' or 'hold'")
        if self.on_undecided not in ("record", "hold"):
            raise ValueError("on_undecided is 'record' or 'hold'")

    def record(self) -> dict:
        return {"role": self.role, "on_loss": self.on_loss, "fail_on": list(self.fail_on), "retries": self.retries,
                "on_undecided": self.on_undecided}


@dataclass
class Hold:
    run_id: str
    link: str
    passed: bool
    code: int
    attempt: int
    policy: dict
    lines: List[str]
    tiers: Dict[str, int]
    ts: str
    pipeline: str = "default"

    def record(self) -> dict:
        return {"kind": "hold", "format": FORMAT, "pipeline": self.pipeline, "run_id": self.run_id, "link": self.link,
                "passed": self.passed, "code": self.code, "attempt": self.attempt, "policy": self.policy,
                "lines": list(self.lines), "tiers": dict(self.tiers), "ts": self.ts}


def claim_selected(c: Claim) -> bool:
    """Which claims must travel: hedged, not found, sourced, dated or marked (the command line's recommended flags)."""
    if c.must or c.origin in ("hedged", "not_found"):
        return True
    m = c.markers or {}
    if m.get("provenance") not in (None, "none stated"):
        return True
    return bool(m.get("temporal"))


def tier_name(tier: Any) -> str:
    return "ledger" if tier == "ledger" else f"tier {tier}"


def holds(v: Verdict) -> bool:
    """A change to what was agreed holds at any modality; a loss holds where the commitment binds."""
    return v.drift_class in CHANGES or (v.drift_class in LOSSES and v.must)


def _where(v: Verdict) -> str:
    return f"({tier_name(v.tier)}) at {v.link} against {v.as_agreed} v{v.version}"


def _line(v: Verdict, word: str, reason: str) -> str:
    return f"DANGER commitment {v.unit} {v.field} {word} {_where(v)}: {reason}; {v.subject}"


def _commitment_lines(verdicts: List[Verdict], policy: Policy) -> List[str]:
    units: "OrderedDict[str, OrderedDict[str, List[Verdict]]]" = OrderedDict()
    for v in verdicts:
        if v.unit_kind == "commitment":
            units.setdefault(v.unit, OrderedDict()).setdefault(v.field, []).append(v)
    lines: List[str] = []
    for unit, fields in units.items():
        first = next(iter(fields.values()))[0]
        decided = [v for vs in fields.values() for v in vs if v.drift_class]
        if len(decided) >= len(fields) and all(d.drift_class == DROPPED for d in decided) and first.must:
            lines.append(f"DANGER commitment {unit} DROPPED {_where(first)}: {first.reason}; {first.subject}")
            continue
        for d in decided:  # every decided verdict: a commitment split into two records is decided twice
            if holds(d):
                lines.append(_line(d, d.drift_class or "", d.reason))
        if policy.on_undecided != "hold" or not first.must:
            continue
        silent = [vs for vs in fields.values() if all(v.undecided == UNDECLARED for v in vs)]
        if len(silent) == len(fields):
            lines.append(f"DANGER commitment {unit} UNDECLARED {_where(first)}: nothing declared for it at this link, "
                         f"and tier 1 reads declared fields, never prose; {first.subject}")
            continue
        # an escalation is answered only by a tier above tier 1. Where a commitment is split into two records, one
        # of them preserving a field must not cover for the other leaving it undecided, so a sibling's tier 1
        # verdict does not silence the escalation; a tier 2 or tier 3 answer for the same field does.
        for vs in fields.values():
            escalated = [v for v in vs if v.undecided == ESCALATED]
            answered = any(v.drift_class and v.tier != 1 for v in vs)
            if escalated and not answered:
                last = escalated[-1]
                lines.append(_line(last, ESCALATED, f"{last.reason}; no tier decided it"))
                continue
            # a carried key the link does not hold, read as not addressed (on_missing_key="undeclared", SPEC §1.2,
            # draft 8): the payload is what takes effect, so under this policy a binding commitment holds on it, and a
            # sibling record's verdict does not cover for it (K15, A76); a tier above that read the field does
            gone = [v for v in vs if v.missing_key and v.undecided == UNDECLARED]
            if gone and not answered:
                last = gone[-1]
                lines.append(_line(last, UNDECLARED, f"{last.reason}; a carried key this link does not deliver is "
                                                     f"unchecked"))
    return lines


def _line_claim(u: Claim, v: Verdict) -> str:
    cls = u.origin + (", must" if u.must else "")
    detail = marker_summary(v.marker_fates) if v.marker_fates and v.status != "DROPPED" else (v.status or "").lower()
    return f'DANGER claim [{cls}] {detail} ({tier_name(v.tier)}) at {v.link} against {v.as_agreed} v{v.version}: "{u.text}"'


def _gate_holds(rows: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """The hold records that can stop a step: a policy that holds on a loss (a link that only records one never stops)."""
    return [h for h in rows if h.get("kind") == "hold" and (h.get("policy") or {}).get("on_loss") == "hold"]


def unstopped_losses(rows: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """Each binding term changed or lost at a link that no hold stopped, at the first such link. A later link that
    restores it does not undo the loss: what that link took effect with was already lost (ASAG-50, the data lab's
    middle-hop miss of 0.1.0)."""
    rows = list(rows)
    stopped = {h.get("link") for h in _gate_holds(rows) if not h.get("passed")}
    first: "OrderedDict[Tuple[str, str], Mapping[str, Any]]" = OrderedDict()
    for r in rows:
        if (r.get("kind") == "verdict" and r.get("unit_kind") == "commitment" and r.get("must") and _rank(r) == 2
                and r.get("link") not in stopped):
            first.setdefault((r.get("unit"), r.get("field")), r)
    return list(first.values())


def _line_upstream(r: Mapping[str, Any]) -> str:
    return (f"DANGER commitment {r.get('unit')} {r.get('field')} {r.get('drift_class')} ({tier_name(r.get('tier'))}) "
            f"at {r.get('link')} against {r.get('as_agreed')} v{r.get('version')}: {r.get('reason')}; lost at a link no "
            f"hold stopped, and a later link does not undo it; {r.get('subject')}")


def decide(verdicts: Iterable[Verdict], agreed: AsAgreed, policy: Policy, tier_failures: Iterable[dict], *,
           run_id: str, link_id: str, attempt: int, ts: str, pipeline: str = "default",
           earlier: Iterable[Mapping[str, Any]] = ()) -> Hold:
    """`earlier`: the run's records before this link. At the commit, a binding term changed or lost at an earlier link
    that no hold stopped holds the gate, unless this link's own verdict on it already does."""
    verdicts = list(verdicts)
    claims = {c.id: c for c in agreed.claims}
    lines = _commitment_lines(verdicts, policy)
    if policy.role == "commit":
        here = {(v.unit, v.field) for v in verdicts if v.unit_kind == "commitment" and holds(v)}
        lines.extend(_line_upstream(r) for r in unstopped_losses(earlier) if (r.get("unit"), r.get("field")) not in here)
    for v in verdicts:
        u = claims.get(v.unit) if v.unit_kind == "claim" else None
        if u is None or not claim_selected(u):
            continue
        if v.status == "DANGER" and "danger" in policy.fail_on:
            lines.append(_line_claim(u, v))
        elif v.status == "DROPPED" and ("dropped" in policy.fail_on or ("dropped_must" in policy.fail_on and u.must)):
            lines.append(_line_claim(u, v))
        elif v.status == "CHANGED" and "changed" in policy.fail_on:
            lines.append(_line_claim(u, v))
    for tf in tier_failures:
        if policy.on_undecided == "hold":
            lines.append(f"DANGER tier {tf.get('tier')} failed: {tf.get('error')} at {link_id}")
    passed = not lines
    counts = Counter(v.tier for v in verdicts)
    tiers = {"tier 1": counts.get(1, 0), "ledger": counts.get("ledger", 0), "tier 2": counts.get(2, 0),
             "tier 3": counts.get(3, 0)}
    code = 0 if passed or policy.on_loss != "hold" else 1
    return Hold(run_id, link_id, passed, code, attempt, policy.record(), lines, tiers, ts, pipeline)


def _currency(bound: Any) -> Optional[Tuple[str, float]]:
    """The agreed amount of a money bound, or None. shortcut: a unit counts as money only when it is an ISO-style
    three-letter code (USD, EUR); add a unit table when a record states money another way."""
    if not isinstance(bound, Mapping) or not re.fullmatch(r"[A-Z]{3}", str(bound.get("unit") or "")):
        return None
    for kind in ("max", "min"):
        if isinstance(bound.get(kind), (int, float)) and not isinstance(bound.get(kind), bool):
            return bound["unit"], float(bound[kind])
    return None


def _minutes(m: float) -> str:
    h, mm = divmod(int(round(m)), 60)
    return f"{h} h {mm} min" if h else f"{mm} min"


def _rank(r: Mapping[str, Any]) -> int:
    """Within one link, the worst verdict of a field stands: a change or a loss, then undecided, then kept."""
    cls = r.get("drift_class")
    return 2 if cls in CHANGES + LOSSES else 0 if cls in FAITHFUL else 1


@dataclass
class Tally:
    """The counts of the run's flag, and the rows behind them; `flag()` and the band read the same one."""
    last: "OrderedDict[Tuple[str, str], Mapping[str, Any]]"  # each typed binding term: its standing verdict
    kept: List[Tuple[str, str]]
    held: List[Mapping[str, Any]]
    through: List[Mapping[str, Any]]                          # changed and not held
    open_: List[Mapping[str, Any]]                            # unchecked
    saved: List[Tuple[str, str]]                              # kept because a hold stopped it first
    stake: Dict[str, float]
    where: str
    untyped: List[Tuple[str, str]] = field(default_factory=list)  # binding fields no link declared: not checked by code
    unrestated: List[Dict[str, Any]] = field(default_factory=list)  # untyped terms, any modality: unit, modality, act, held


# Code decides these fields only where a link declares them; the lab's chains never do, and 672 of 816 terms per set
# were these fields, unchecked, in the headline (the data lab's third miss of 0.1.0).
UNTYPED_FIELDS = ("obligor", "obligee", "act", "modality")


def term_name(r: Mapping[str, Any]) -> str:
    """A lost term as the flag names it: unit, field, class and reason."""
    return f"{r['unit']} {r['field']} {r['drift_class']}: {r.get('reason', '')}"


def _declared(r: Mapping[str, Any]) -> bool:
    """The link declared the field: tier 1 decided it, or read it and could not decide it."""
    return bool(r.get("drift_class")) or r.get("undecided") not in (None, UNDECLARED)


def tally(rows: Iterable[Mapping[str, Any]]) -> Tally:
    """Counted over the typed fields of the binding commitments (MUST, MUST_NOT): every field but obligor, obligee, act
    and modality, and those four where some link declared them. Each term stands at its last link in the run (within a
    link, its worst verdict), unless a link that no hold stopped changed or lost it before: that loss stands, even when
    a later link restores the term. Kept (PRESERVED or RESTATED), held (changed or lost, and a hold stopped the run at
    or after that link), changed and not held, unchecked (code could not decide). A term counts as kept because of the
    check when a hold stopped it at one link and it was kept at a later link that passed its hold.

    Apart: the binding fields that are not typed (`untyped`), and each term with no typed field (`unrestated`, any
    modality), held when its modality binds and the hold at its last link holds what code cannot decide."""
    rows = list(rows)
    holds_ = _gate_holds(rows)
    stopped = {h.get("link") for h in holds_ if not h.get("passed")}
    passed = {h.get("link") for h in holds_ if h.get("passed")}
    last_stop = max((i for i, r in enumerate(rows) if r.get("kind") == "hold" and not r.get("passed")
                     and (r.get("policy") or {}).get("on_loss") == "hold"), default=-1)
    commitments = {r.get("commitment_id"): r for r in rows if r.get("kind") == "commitment"}
    verdicts = [(i, r) for i, r in enumerate(rows) if r.get("kind") == "verdict" and r.get("unit_kind") == "commitment"]
    declared = {(r.get("unit"), r.get("field")) for _, r in verdicts if _declared(r)}

    def typed(key: Tuple[Any, Any]) -> bool:
        return key[1] not in UNTYPED_FIELDS or key in declared

    last: "OrderedDict[Tuple[str, str], Mapping[str, Any]]" = OrderedDict()
    at: Dict[Tuple[str, str], int] = {}
    lost: Dict[Tuple[str, str], Tuple[int, Mapping[str, Any]]] = {}
    caught = set()
    untyped: "OrderedDict[Tuple[str, str], None]" = OrderedDict()
    for i, r in verdicts:
        if not r.get("must"):
            continue
        key = (r.get("unit"), r.get("field"))
        if not typed(key):
            untyped[key] = None
            continue
        before = last.get(key)
        if before is not None and before.get("link") == r.get("link") and _rank(before) >= _rank(r):
            continue
        last.pop(key, None)
        last[key], at[key] = r, i
        if _rank(r) == 2:
            if r.get("link") in stopped:
                caught.add(key)
            else:
                lost.setdefault(key, (i, r))
    for key, (i, r) in lost.items():
        if _rank(last[key]) != 2:  # kept again, or undecided, after a loss no hold stopped: the loss stands
            last[key], at[key] = r, i

    def stopped_after(key: Tuple[str, str], r: Mapping[str, Any]) -> bool:
        return r.get("link") in stopped or last_stop > at[key]

    kept = [k for k, r in last.items() if _rank(r) == 0]
    held = [r for k, r in last.items() if _rank(r) == 2 and stopped_after(k, r)]
    through = [r for k, r in last.items() if _rank(r) == 2 and not stopped_after(k, r)]
    open_ = [r for r in last.values() if _rank(r) == 1]
    where = " and ".join(sorted({"delivery" if r.get("state") == "as_delivered" else "a brief" for r in last.values()}))
    saved = [k for k in kept if k in caught and last[k].get("link") in passed]
    stake: Dict[str, float] = {}
    for unit, fld in saved:
        limit = (commitments.get(unit) or {}).get("limit") or {}
        money = _currency(limit.get(fld.partition(".")[2])) if fld.startswith("limit.") else None
        if money:
            stake[money[0]] = stake.get(money[0], 0.0) + money[1]
    units: "OrderedDict[Any, List[Mapping[str, Any]]]" = OrderedDict()
    for _, r in verdicts:
        units.setdefault(r.get("unit"), []).append(r)
    unrestated: List[Dict[str, Any]] = []
    for unit, rs in units.items():
        if any(typed((unit, r.get("field"))) for r in rs):
            continue
        end = rs[-1]
        gate = [h for h in holds_ if h.get("link") == end.get("link")]
        held_here = (bool(end.get("must")) and bool(gate) and not gate[-1].get("passed")
                     and (gate[-1].get("policy") or {}).get("on_undecided") == "hold")
        unrestated.append({"unit": unit, "modality": end.get("unit_class"), "held": held_here,
                           "act": (commitments.get(unit) or {}).get("act") or end.get("subject") or ""})
    return Tally(last, kept, held, through, open_, saved, stake, where, list(untyped), unrestated)


def _untyped_lines(t: Tally) -> List[str]:
    lines = []
    if t.unrestated:
        def named(ts: List[Dict[str, Any]]) -> str:
            return " (" + "; ".join(f"{u['unit']} {u['modality']}: {u['act']}" for u in ts) + ")" if ts else ""
        held = [u for u in t.unrestated if u["held"]]
        kept = [u for u in t.unrestated if not u["held"]]
        lines.append(f"Terms not restated, untyped: {len(held)} held{named(held)}"
                     + (f"; {len(kept)} recorded, not held{named(kept)}" if kept else ""))
    if t.untyped:
        kinds = [f for f in UNTYPED_FIELDS if any(k[1] == f for k in t.untyped)]
        lines.append(f"Not checked by code: {len(t.untyped)} untyped fields of binding terms ({', '.join(kinds)}), "
                     f"declared at no link")
    return lines


def flag(rows: Iterable[Mapping[str, Any]], baseline_minutes: Optional[float] = None) -> List[str]:
    """The run's flag (D-181, D-191), the verdict first and the value second, from the run's records, counted by
    `tally()`. Review time appears only against a baseline the user sets, in minutes of manual check per run. The
    lines on untyped terms come last, so the first three keep their places."""
    t = tally(rows)
    last, kept, held, through, open_, saved, stake = t.last, t.kept, t.held, t.through, t.open_, t.saved, t.stake
    if not last and not t.untyped and not t.unrestated:
        return ["Kept as agreed: no binding term was checked in this run"]

    def named(rs: List[Mapping[str, Any]]) -> str:
        return " (" + "; ".join(term_name(r) for r in rs) + ")"

    if last:
        lines = [f"Kept as agreed: {len(kept)} of {len(last)} binding terms, checked at {t.where}; "
                 f"{len(held)} held{named(held) if held else ''}; "
                 + (f"{len(through)} changed and not held{named(through)}; " if through else "")
                 + f"{len(open_)} unchecked"]
    else:
        lines = ["Kept as agreed: no binding term was checked in this run"]
    lines.append(f"{len(saved)} of {len(last)} terms kept that would have been lost"
                 + ("".join(f", {v:,.0f} {c}" for c, v in sorted(stake.items())) + " at stake" if stake else ""))
    if baseline_minutes is not None:
        # shortcut: a run that held, let a change through or left a term unchecked returns no review time; a binding
        # field code did not check waits for a person's read as an unchecked one does
        wait = len(held) + len(through) + len(open_) + len(t.untyped)
        if wait or any(u["held"] for u in t.unrestated):
            lines.append(f"Review returned: none; {wait} terms wait for your read "
                         f"(your baseline: {_minutes(baseline_minutes)} per run)")
        else:
            lines.append(f"Review returned: {_minutes(baseline_minutes)} (your baseline per run)")
    return lines + _untyped_lines(t)
