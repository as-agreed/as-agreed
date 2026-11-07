"""The check (SPEC §2.2): every commitment and every claim against one link; tier 1, then the tiers above it."""
from __future__ import annotations

import inspect
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .commitment import FORMAT, MODALITIES, AsAgreed, Commitment
from .drift import (ADDED_UNPARENTED, DRIFT_CLASSES, ESCALATED, FAITHFUL, NARROWED, PRESERVED, RECONDITIONED,
                    RESTATED, RESTORED_EQUIVALENT, RESTORED_VERBATIM, SITE_OF_LINK, WIDENED, Declared, Finding,
                    decide_value, read, safe_id)
from .serialise import to_jsonable
from .match import claim_status, verbatim
from .serialise import canonical, claim_text, to_lines

SITES = ("design", "source", "seam", "sink")


@dataclass
class Verdict:
    run_id: str
    link: str
    state: str                 # as_briefed | as_delivered
    unit: str
    unit_kind: str             # commitment | claim
    unit_class: str            # the modality, or the claim's origin class
    must: bool                 # the commitment binds, or the claim is marked must-travel
    field: str                 # a field path; "status" for a claim
    tier: Any                  # 1, 2 or 3 for a commitment; "ledger" for a claim
    reason: str
    as_agreed: str
    version: str
    ts: str
    pipeline: str = "default"
    drift_class: Optional[str] = None
    status: Optional[str] = None
    undecided: Optional[str] = None
    trajectory: Optional[str] = None
    subject: Optional[str] = None
    marker_fates: Optional[Dict[str, str]] = None
    confidence: Optional[float] = None
    site: Optional[str] = None
    site_undecided: Optional[str] = None   # ESCALATED: a changed field whose first change was at an untyped link
    history_incomplete: bool = False
    missing_key: Optional[str] = None      # UNDECLARED: this carried key is not in the link (§1.2, draft 8)
    read: Optional[Dict[str, Any]] = None  # a value reader's read: the value and the quote tier 1 classed (§4, draft 8)
    proposed_class: Optional[str] = None   # ESCALATED as a proposal: the class the answer would give (§4, draft 9)
    refusal: Optional[str] = None          # ESCALATED by a named refusal: unit_mismatch (§1.2, §6, draft 9)
    unit_agreed: Optional[str] = None      # the agreed unit, where the link names another (draft 9)
    unit_read: Optional[str] = None        # the unit the link gave, declared or read (draft 9)
    unit_converted: bool = False           # the value was converted to the agreed unit, then classed (draft 9)

    @property
    def label(self) -> str:
        return self.trajectory or self.drift_class or self.status or self.undecided or ""

    def record(self) -> dict:
        d: Dict[str, Any] = {"kind": "verdict", "format": FORMAT, "pipeline": self.pipeline, "run_id": self.run_id,
                             "link": self.link, "state": self.state, "unit": self.unit, "unit_kind": self.unit_kind,
                             "unit_class": self.unit_class, "must": self.must, "field": self.field, "tier": self.tier,
                             "reason": self.reason, "as_agreed": self.as_agreed, "version": self.version, "ts": self.ts}
        if self.unit_kind == "commitment":
            d.update({"drift_class": self.drift_class, "site": self.site, "subject": self.subject})
        else:
            d["status"] = self.status
        for key in ("undecided", "trajectory", "marker_fates", "confidence", "site_undecided", "missing_key", "read",
                    "proposed_class", "refusal", "unit_agreed", "unit_read"):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        if self.unit_converted:
            d["unit_converted"] = True
        if self.history_incomplete:
            d["history_incomplete"] = True
        return d


@dataclass
class Escalation:
    """What a tier above tier 1 is asked: `agreed` is always the as-agreed value, never a previous hop's (SPEC §4)."""
    commitment: Commitment
    field: str
    agreed: Any
    declared: Any
    link: str
    link_text: str
    reason: str
    to_tier: int = 2


def _threshold(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not _unit_interval(value):
        raise ValueError(f"threshold is a number from 0 to 1, not {value!r}")
    return float(value)


class Tier2:
    """Model-assisted: fn(escalation) -> {"drift_class", "confidence", "reason", "site"?} | None.
    Below `threshold` the answer stands as a proposal and the field goes to tier 3. The library ships no model.
    With batch=True, fn receives every escalation of one link at once, as a list, and returns one answer per
    escalation, in order (SPEC §4, draft 8)."""
    tier = 2

    def __init__(self, fn: Callable[[Escalation], Any], threshold: float = 0.8, *, batch: bool = False):
        self.fn = fn
        self.threshold = _threshold(threshold)
        self.batch = bool(batch)


class Tier3:
    """A person: fn(escalation) -> {"drift_class", "reason", "site"?} | None while the review is pending.
    With batch=True, fn receives the link's escalations as a list and returns a list of answers."""
    tier = 3

    def __init__(self, fn: Callable[[Escalation], Any], *, batch: bool = False):
        self.fn = fn
        self.batch = bool(batch)


class ValueReader:
    """Model-assisted reading without a class: fn(escalation) -> {"value", "confidence", "quote"} | None (SPEC §4,
    draft 8). The value is what the link delivers for the field (None: the link says the field is gone), and the
    quote is the span of the link's text it was read from. Tier 1's decision procedure classes the value, so the class
    is deterministic given the read and the model never asserts one. A read below `threshold`, or whose quote is not in
    the link's text, stands as a proposal (ESCALATED, with `proposed_class` the class tier 1 gives the value, draft 9)
    and goes to tier 3; None leaves the field as it was. A value in another unit is converted by tier 1's table or
    refused as unit_mismatch (draft 9). Its verdicts carry tier 2, the confidence and the read. batch=True as on Tier2."""
    tier = 2

    def __init__(self, fn: Callable[[Escalation], Any], threshold: float = 0.8, *, batch: bool = False):
        self.fn = fn
        self.threshold = _threshold(threshold)
        self.batch = bool(batch)


History = Optional[Dict[str, str]]  # "unit/field" -> the class or status last decided; None: unavailable


def _lost(label: Optional[str]) -> bool:
    return bool(label) and label not in FAITHFUL


def _trajectory(drift_class: Optional[str], key: str, history: History, conforming: bool = False) -> Tuple[Optional[str], bool]:
    """`restored_verbatim` is tier 1's; `restored_equivalent` never is (SPEC §1.6).

    A value that merely sits inside its bound earns no trajectory: "verbatim" is a claim about what was declared,
    and 17,000 against a cap of 18,400 conforms without anything having come back.
    """
    if drift_class != PRESERVED:
        return None, False
    if history is None:
        return None, True
    if conforming:
        return None, False
    return (RESTORED_VERBATIM if _lost(history.get(key)) else None), False


# who may assert what (SPEC §1.6, the last column). Tier 1's own classes are decided in `drift.py`; a tier above it
# may only say what its evidence can support, so that the headline metric is not settable by the caller's callable.
TIER_MAY = {2: (RESTATED, WIDENED, NARROWED, RECONDITIONED), 3: DRIFT_CLASSES}


def _answer(result: Any, tier: Any) -> Dict[str, Any]:
    if inspect.isawaitable(result):
        raise TypeError("a tier callable returned an awaitable; async tier callables are not in v0.1")
    if not isinstance(result, Mapping):
        raise TypeError(f"a tier callable returns a mapping or None, not {type(result).__name__}")
    cls = result.get("drift_class")
    if cls not in DRIFT_CLASSES:
        raise ValueError(f"drift_class {cls!r} is not one of {', '.join(DRIFT_CLASSES)}")
    allowed = TIER_MAY.get(tier.tier, ())
    if cls not in allowed:
        raise ValueError(f"tier {tier.tier} may not assert {cls}; it may assert {', '.join(allowed)}")
    site = result.get("site")
    if site is not None and site not in SITES:
        raise ValueError(f"site {site!r} is not one of {', '.join(SITES)}")
    if tier.tier == 2:
        _confidence(result.get("confidence"))
    return dict(result)


def _unit_interval(value: Any) -> bool:
    """A finite number from 0 to 1, compared exactly: math.isfinite raises OverflowError on a huge int."""
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return 0 <= value <= 1


def _confidence(conf: Any) -> float:
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not _unit_interval(conf):
        raise ValueError(f"every tier 2 verdict carries a confidence from 0 to 1, not {conf!r}")
    return float(conf)


def _read_answer(result: Any) -> Dict[str, Any]:
    """A value reader's answer: value, confidence and quote, nothing else taken (K11); a drift class is refused,
    because a reader that names a class would be a tier 2 asserting it."""
    if inspect.isawaitable(result):
        raise TypeError("a tier callable returned an awaitable; async tier callables are not in v0.1")
    if not isinstance(result, Mapping):
        raise TypeError(f"a value reader returns a mapping or None, not {type(result).__name__}")
    missing = [k for k in ("value", "confidence", "quote") if k not in result]
    if missing:
        raise ValueError(f"a value read carries value, confidence and quote; missing: {', '.join(missing)}")
    if result.get("drift_class") is not None:
        raise ValueError("a value reader returns a value, never a drift class: tier 1 classes the value")
    quote = result["quote"]
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError(f"a value read's quote is the span it was read from, a non-empty string, not {quote!r}")
    return {"value": result["value"], "confidence": _confidence(result["confidence"]), "quote": quote}


def _in_text(quote: str, text: str) -> bool:
    """The quote is in the link's text, whitespace collapsed on both sides and nothing else changed."""
    return " ".join(quote.split()) in " ".join(text.split())


def _shown(value: Any) -> Any:
    """A read value as a record carries it: plain JSON, or its text when it is not."""
    try:
        return to_jsonable(value)
    except ValueError:
        return str(value)[:200]


def _ask(tier: Any, escalations: List[Escalation]) -> List[Any]:
    """One call per escalation, or one call per link in the batch form, which returns one answer per escalation."""
    if not escalations:
        return []
    if not getattr(tier, "batch", False):
        return [tier.fn(e) for e in escalations]
    answers = tier.fn(list(escalations))
    if inspect.isawaitable(answers):
        raise TypeError("a tier callable returned an awaitable; async tier callables are not in v0.1")
    if not isinstance(answers, (list, tuple)) or len(answers) != len(escalations):
        got = f"{len(answers)} answers" if isinstance(answers, (list, tuple)) else type(answers).__name__
        raise ValueError(f"a batch tier callable returns one answer per escalation, in order: {got} for "
                         f"{len(escalations)} escalations")
    return list(answers)


def check_link(outputs: Any, agreed: AsAgreed, *, run_id: str, link_id: str, ts: str, history: History,
               tiers: Iterable[Any] = (), pipeline: str = "default", delivered: bool = False,
               link_type: Optional[str] = None, first_changed: Optional[Mapping[str, Optional[str]]] = None,
               on_missing_key: str = "dropped") -> Tuple[List[Verdict], List[dict]]:
    """Tier 1 on every declared field, the ledger on every claim, then the tiers on what stayed undecided.
    Every reference is the as-agreed record; `history` only names what was lost, for the trajectory labels.

    `link_type` is this link's type and `first_changed` maps unit/field to the type of the earliest link where the
    field changed (D-25): a field tier 1 finds changed takes its site from that link's type, or from this link's when
    it changes here first; an untyped link gives site None with `site_undecided` ESCALATED."""
    state = "as_delivered" if delivered else "as_briefed"
    declared = Declared(outputs, delivered=delivered, on_missing_key=on_missing_key)
    ids = {c.commitment_id for c in agreed.commitments}
    verdicts: List[Verdict] = []
    failures: List[dict] = []
    pending: List[Tuple[Verdict, Escalation]] = []

    def site_of(key: str, cls: Optional[str]) -> Dict[str, Optional[str]]:
        if not cls or cls in FAITHFUL:
            return {}
        lt = first_changed[key] if first_changed is not None and key in first_changed else link_type
        site = SITE_OF_LINK.get(lt) if lt else None
        return {"site": site, "site_undecided": None if site else ESCALATED}

    def stamp(**kw: Any) -> Verdict:
        return Verdict(run_id=run_id, link=link_id, state=state, as_agreed=agreed.id, version=agreed.version, ts=ts,
                       pipeline=pipeline, **kw)

    def commitment_verdict(c: Commitment, f: Finding, tier: int = 1, trajectory: Optional[str] = None,
                           confidence: Optional[float] = None, site: Optional[str] = None,
                           read_value: Optional[Dict[str, Any]] = None, proposed: Optional[str] = None) -> Verdict:
        inc = False
        where: Dict[str, Optional[str]] = {"site": site}
        if tier == 1:
            # a bound converted from another unit came back in other words: tier 1 may not call it verbatim
            trajectory, inc = _trajectory(f.drift_class, f"{c.commitment_id}/{f.field}", history,
                                          f.conforming or f.unit_converted)
            where = site_of(f"{c.commitment_id}/{f.field}", f.drift_class) or where
        elif read_value is not None:  # tier 1's procedure classed the read, so tier 1's rule names the site
            where = site_of(f"{c.commitment_id}/{f.field}", f.drift_class) or where
        return stamp(unit=c.commitment_id, unit_kind="commitment", unit_class=c.modality, must=c.binding, field=f.field,
                     tier=tier, reason=f.reason, drift_class=f.drift_class, undecided=f.undecided, trajectory=trajectory,
                     subject=c.subject, confidence=confidence, history_incomplete=inc, missing_key=f.missing_key,
                     read=read_value, proposed_class=proposed, refusal=f.refusal, unit_agreed=f.unit_agreed,
                     unit_read=f.unit_read, unit_converted=f.unit_converted, **where)

    def from_read(t: Any, esc: Escalation, answer: Any) -> Tuple[Verdict, bool]:
        """A value reader's read classed by tier 1's procedure; True when the field stays undecided."""
        a = _read_answer(answer)
        conf, value, quote = a["confidence"], a["value"], a["quote"]
        shown = {"value": _shown(value), "quote": quote[:300]}
        got = decide_value(esc.commitment, esc.field, value)
        f = Finding(esc.field, got.drift_class, f"read at {conf:.2f}: {got.reason}", conforming=got.conforming)
        proposed = None  # a read tier 1 classed but the core did not accept carries the class it would give
        if got.drift_class is None:
            f = Finding(esc.field, None, f"read at {conf:.2f}, and tier 1 cannot decide the value read: {got.reason}",
                        undecided=ESCALATED)
        elif not _in_text(quote, esc.link_text):
            proposed = got.drift_class
            f = Finding(esc.field, None, f"proposed {got.drift_class} from a read whose quote is not in the link's "
                                         f"text: {got.reason}", undecided=ESCALATED)
        elif conf < t.threshold:
            proposed = got.drift_class
            f = Finding(esc.field, None, f"proposed {got.drift_class} from a read below the threshold "
                                         f"({conf:.2f} < {t.threshold:.2f}): {got.reason}", undecided=ESCALATED)
        f.refusal, f.unit_agreed, f.unit_read, f.unit_converted = (got.refusal, got.unit_agreed, got.unit_read,
                                                                   got.unit_converted)
        traj = None
        key = f"{esc.commitment.commitment_id}/{esc.field}"
        if f.drift_class in FAITHFUL and not f.conforming and _lost((history or {}).get(key)):
            traj = RESTORED_EQUIVALENT  # a model's read, not a declaration: tier 1's verbatim label is not earned
        v = commitment_verdict(esc.commitment, f, tier=t.tier, confidence=conf, trajectory=traj, read_value=shown,
                               proposed=proposed)
        return v, f.drift_class is None

    text = to_lines(outputs)
    for c in agreed.commitments:
        for f in read(c, declared, ids):
            v = commitment_verdict(c, f)
            verdicts.append(v)
            if f.drift_class is None:
                pending.append((v, Escalation(c, f.field, f.agreed, f.declared, link_id, text, f.reason, f.to_tier)))
    for r in declared.unparented(ids):
        uid = safe_id(r.get("commitment_id") or canonical(r))
        why = "no parent_id key" if "parent_id" not in r else (
            "parent_id is null" if r.get("parent_id") is None else f"parent_id {r['parent_id']} resolves to nothing")
        who = f"{r.get('obligor', '?')} to {r.get('obligee', '?')}, {r.get('modality', '?')}: {r.get('act', '?')}"
        cls = r.get("modality") if r.get("modality") in MODALITIES else "?"  # the class reaches the map: labels only
        verdicts.append(stamp(unit=uid, unit_kind="commitment", unit_class=cls, must=False,
                              field="parent_id", tier=1, reason=f"present downstream with nothing upstream: {why}",
                              drift_class=ADDED_UNPARENTED, subject=who, **site_of(f"{uid}/parent_id", ADDED_UNPARENTED)))
    prose = claim_text(outputs)
    for c in agreed.claims:
        status, fates, reason, _ = claim_status(c, prose)
        key = f"{c.id}/status"
        traj, inc = _trajectory(status, key, history)
        if traj is not None and not verbatim(c, prose):
            traj = None  # present again in other words: tier 1 may not say equivalent
        verdicts.append(stamp(unit=c.id, unit_kind="claim", unit_class=c.origin, must=bool(c.must), field="status",
                              tier="ledger", reason=reason, status=status, trajectory=traj, marker_fates=fates or None,
                              history_incomplete=inc))

    for t in sorted(tiers, key=lambda x: getattr(x, "tier", 0)):
        level = getattr(t, "tier", None)
        mine: List[Verdict] = []       # a tier that fails leaves none of its own verdicts behind
        unresolved: List[Tuple[Verdict, Escalation]] = []
        try:
            if not isinstance(t, (Tier2, Tier3, ValueReader)):
                raise TypeError(f"unknown tier {type(t).__name__}; use Tier2, Tier3 or ValueReader")
            asked = [i for i, (_, esc) in enumerate(pending) if level >= esc.to_tier]
            answers = dict(zip(asked, _ask(t, [pending[i][1] for i in asked])))
            for i, (v, esc) in enumerate(pending):
                answer = answers.get(i)
                if answer is None:  # not asked (purpose skips tier 2), or no answer: the field stays as it was
                    unresolved.append((v, esc))
                    continue
                if isinstance(t, ValueReader):
                    mv, open_ = from_read(t, esc, answer)
                    mine.append(mv)
                    if open_:
                        unresolved.append((v, esc))
                    continue
                a = _answer(answer, t)
                cls, reason = a["drift_class"], str(a.get("reason", ""))
                conf = float(a["confidence"]) if "confidence" in a and a["confidence"] is not None else None
                f = Finding(esc.field, cls, reason)
                proposed = None
                if level == 2 and conf < t.threshold:
                    proposed = cls
                    f = Finding(esc.field, None, f"proposed {cls} below the threshold: {reason}", undecided=ESCALATED)
                    unresolved.append((v, esc))
                traj = None
                if f.drift_class in FAITHFUL and _lost((history or {}).get(f"{esc.commitment.commitment_id}/{esc.field}")):
                    traj = RESTORED_EQUIVALENT
                mine.append(commitment_verdict(esc.commitment, f, tier=level, confidence=conf, site=a.get("site"),
                                               trajectory=traj, proposed=proposed))
            verdicts.extend(mine)
            pending = unresolved
        except Exception as e:  # a tier's failure never fails the check (K12); its partial verdicts are dropped
            failures.append({"kind": "tier_failure", "format": FORMAT, "pipeline": pipeline, "run_id": run_id,
                             "link": link_id, "tier": level, "error": f"{type(e).__name__}: {e}"[:300], "ts": ts})
    return verdicts, failures


def _row(v: Any) -> Mapping:
    return v.record() if isinstance(v, Verdict) else v


def fidelity_score(verdicts: Iterable[Any], link: Optional[str] = None) -> Dict[str, Any]:
    """Commitment fidelity: of the fields a tier decided, how many are PRESERVED or RESTATED. The pair travels with
    the share, and a report prints the pair (SPEC §7). `score` is None when nothing was decided."""
    decided: Dict[Tuple[str, str, str], str] = {}
    for v in map(_row, verdicts):
        if v.get("unit_kind") == "commitment" and v.get("drift_class") and (link is None or v.get("link") == link):
            key = (v["link"], v["unit"], v["field"])
            if decided.get(key, PRESERVED) in FAITHFUL:   # a field decided twice counts at its worst
                decided[key] = v["drift_class"]
    preserved = sum(1 for c in decided.values() if c in FAITHFUL)
    return {"preserved": preserved, "decided": len(decided), "score": preserved / len(decided) if decided else None}
