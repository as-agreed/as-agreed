"""The hold (SPEC §2.3): the policy, what holds, the DANGER lines, the codes."""
from __future__ import annotations

import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
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


def decide(verdicts: Iterable[Verdict], agreed: AsAgreed, policy: Policy, tier_failures: Iterable[dict], *,
           run_id: str, link_id: str, attempt: int, ts: str, pipeline: str = "default") -> Hold:
    verdicts = list(verdicts)
    claims = {c.id: c for c in agreed.claims}
    lines = _commitment_lines(verdicts, policy)
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


def flag(rows: Iterable[Mapping[str, Any]], baseline_minutes: Optional[float] = None) -> List[str]:
    """The run's flag (D-181, D-191), the verdict first and the value second, from the run's records.

    Counted over the fields of the binding commitments (MUST, MUST_NOT), each at its last link in the run (within a
    link, its worst verdict): kept (PRESERVED or RESTATED), held (changed or lost at a link that a hold stopped),
    changed and not held, unchecked (code could not decide). A term counts as kept because of the check when a hold
    stopped it at one link and it was kept at a later link that passed its hold. Review time appears only against a
    baseline the user sets, in minutes of manual check per run."""
    rows = list(rows)
    holds_ = [h for h in rows if h.get("kind") == "hold" and (h.get("policy") or {}).get("on_loss") == "hold"]
    stopped = {h.get("link") for h in holds_ if not h.get("passed")}
    passed = {h.get("link") for h in holds_ if h.get("passed")}
    limits = {r.get("commitment_id"): r.get("limit") or {} for r in rows if r.get("kind") == "commitment"}
    last: "OrderedDict[Tuple[str, str], Mapping[str, Any]]" = OrderedDict()
    caught = set()
    for r in rows:
        if r.get("kind") != "verdict" or r.get("unit_kind") != "commitment" or not r.get("must"):
            continue
        key = (r.get("unit"), r.get("field"))
        before = last.get(key)
        if before is not None and before.get("link") == r.get("link") and _rank(before) >= _rank(r):
            continue
        last.pop(key, None)
        last[key] = r
        if r.get("link") in stopped and _rank(r) == 2:
            caught.add(key)
    if not last:
        return ["Kept as agreed: no binding term was checked in this run"]
    kept = [k for k, r in last.items() if _rank(r) == 0]
    held = [r for r in last.values() if _rank(r) == 2 and r.get("link") in stopped]
    through = [r for r in last.values() if _rank(r) == 2 and r.get("link") not in stopped]
    open_ = [r for r in last.values() if _rank(r) == 1]
    where = " and ".join(sorted({"delivery" if r.get("state") == "as_delivered" else "a brief" for r in last.values()}))

    def named(rs: List[Mapping[str, Any]]) -> str:
        return " (" + "; ".join(f"{r['unit']} {r['field']} {r['drift_class']}: {r.get('reason', '')}" for r in rs) + ")"

    lines = [f"Kept as agreed: {len(kept)} of {len(last)} binding terms, checked at {where}; "
             f"{len(held)} held{named(held) if held else ''}; "
             + (f"{len(through)} changed and not held{named(through)}; " if through else "")
             + f"{len(open_)} unchecked"]
    saved = [k for k in kept if k in caught and last[k].get("link") in passed]
    stake: Dict[str, float] = {}
    for unit, field in saved:
        money = _currency(limits.get(unit, {}).get(field.partition(".")[2])) if field.startswith("limit.") else None
        if money:
            stake[money[0]] = stake.get(money[0], 0.0) + money[1]
    lines.append(f"{len(saved)} of {len(last)} terms kept that would have been lost"
                 + ("".join(f", {v:,.0f} {c}" for c, v in sorted(stake.items())) + " at stake" if stake else ""))
    if baseline_minutes is not None:
        # shortcut: a run that held, let a change through or left a term unchecked returns no review time
        if held or through or open_:
            lines.append(f"Review returned: none; {len(held) + len(through) + len(open_)} terms wait for your read "
                         f"(your baseline: {_minutes(baseline_minutes)} per run)")
        else:
            lines.append(f"Review returned: {_minutes(baseline_minutes)} (your baseline per run)")
    return lines
