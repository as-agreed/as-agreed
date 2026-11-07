"""The hold (SPEC §2.3): the policy, what holds, the DANGER lines, the codes."""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

from .check import Verdict
from .commitment import FORMAT, AsAgreed, Claim
from .drift import (ADDED_UNPARENTED, DROPPED, ESCALATED, NARROWED, RECONDITIONED, REASSIGNED, RESCHEDULED, STRENGTHENED,
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
