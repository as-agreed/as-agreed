"""The public surface of As-Agreed v0.1 (SPEC §2 and §3): register, step, run, check, hold, retry, report.

This module is the surface (`import as_agreed.sdk as ab`); the package root does not re-export it, because
four of the five verbs are module names there (SPEC §14, A83).
"""
from __future__ import annotations

import contextlib
import functools
import inspect
import json
import sys
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .adapters.base import Adapter, Outcome, StepContext
from .adapters.plain import PlainAdapter, memory_adapter
from .check import Escalation, Tier2, Tier3, ValueReader, Verdict, check_link, fidelity_score
from .commitment import FORMAT, MODALITIES, AsAgreed, Claim, Commitment, Criterion, build, cut
from .destination import Envelope, LocalDestination, RemoteDestination
from .drift import MISSING_KEY_MODES, DRIFT_CLASSES, LINK_TYPES
from .errors import (AsAgreedExists, AsAgreedError, DriftHold, EmptyAsAgreed, NoCommitment, SchemaError,
                     WorkflowContextError)
from .hold import DEFAULT_FAIL_ON, Hold, Policy, decide
from .reason import (acall_with_reason, accepts_kw, call_with_reason, current_as_agreed, current_reason,
                     reset_current_as_agreed, reset_reason, set_current_as_agreed, set_reason)
from .report import RecallRow, Report
from .report import build as build_report
from .retry import MAX_RETRIES, idempotency_key, link_id, retry_record
from .serialise import claim_text, inputs_hash, to_jsonable, to_lines

__all__ = ["register", "step", "run", "check", "hold", "retry", "report", "Commitment", "Criterion", "Claim", "AsAgreed",
           "Verdict", "Hold", "Outcome", "Tier2", "Tier3", "ValueReader", "Escalation", "RecallRow", "fidelity_score", "PlainAdapter",
           "memory_adapter", "LocalDestination", "RemoteDestination", "Envelope", "current_reason", "AsAgreedError",
           "NoCommitment", "AsAgreedExists", "EmptyAsAgreed",
           "SchemaError", "DriftHold", "WorkflowContextError", "Policy", "MAX_RETRIES", "DRIFT_CLASSES", "MODALITIES"]

SCHEMA_TYPES = (float, int, str, bool, list, dict)


def _is_async(fn: Any) -> bool:
    """True for `async def`, and for an object whose `__call__` is one: `inspect.iscoroutinefunction` is false for an
    instance, so a class with an async `__call__` would otherwise pass the refusals of SPEC §3."""
    if inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn):
        return True
    call = getattr(fn, "__call__", None)
    return call is not None and not inspect.isfunction(fn) and (
        inspect.iscoroutinefunction(call) or inspect.isasyncgenfunction(call))


# ---------- register ----------
def register(order: str, commitments: Iterable[Any] = (), *, claims: Iterable[str] = (), source: Optional[str] = None,
             version: int = 1, proxy: bool = False, pipeline: Optional[str] = None, adapter: Optional[Adapter] = None,
             min_words: int = 3, drafter: Optional[Dict[str, str]] = None) -> AsAgreed:
    """Register the as-agreed record once per run and write it through the adapter's sink (SPEC §2.1).

    A commitment needs act, obligor, obligee and modality; everything else is optional, and the report names what
    was left out, because a field nobody declared cannot be checked. A proxy record a model drafted names its drafter
    (model, version, date, prompt_id); the record and every report carry it (SPEC §1.1, draft 8)."""
    ad = adapter or PlainAdapter()
    pipeline = pipeline or ad.pipeline
    existing = [r for r in ad.records() if r.get("kind") == "as_agreed" and r.get("run_id") == ad.run_id]
    if existing:
        top = max(int(str(r.get("version", "1")).split(".")[0]) for r in existing)
        if top >= int(version):
            raise AsAgreedExists(f"run {ad.run_id} already holds an as-agreed record at version {top}; "
                                 f"register a higher major version or start a new run")
    ts = ad.clock()
    agreed, warnings = build(order, commitments, claims=claims, source=source, version=version, proxy=proxy,
                             pipeline=pipeline, run_id=ad.run_id, ts=ts, min_words=min_words, drafter=drafter)
    agreed.adapter = ad
    ad.record(agreed.record())
    for c in agreed.commitments:
        ad.record(_stamp(c.record(agreed.id), pipeline, ad.run_id))
    for c in agreed.claims:
        ad.record(_stamp(c.record(agreed.id), pipeline, ad.run_id))
    ad.record({"kind": "link", "format": FORMAT, "pipeline": pipeline, "run_id": ad.run_id, "step": "order",
               "invocation": 1, "attempt": 1, "id": "order#1@1", "parent": None, "role": "order", "state": "as_agreed",
               "link_type": None, "text": order, "inputs_hash": None, "idempotency_key": None, "reads": [], "writes": [], "ts": ts})
    for w in warnings:
        rec = dict(w)
        rec.update({"pipeline": pipeline, "run_id": ad.run_id, "source": source or "order", "ts": ts})
        ad.record(rec)
        print(f"warning: {source or 'order'} line {rec.get('line')}: {rec['text']}", file=sys.stderr)
    return agreed


def _stamp(record: dict, pipeline: str, run_id: str) -> dict:
    record.setdefault("format", FORMAT)
    record["pipeline"] = pipeline
    record["run_id"] = run_id
    return record


NO_HISTORY = object()  # pass as history= to check(): the run's earlier verdicts are unavailable (an engine lost them)


def _plain(value: Any) -> Any:
    """A value as the sink holds it: what a JSON round trip gives back (tuples as lists, keys as text)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def _changed_fields(c: Commitment, row: dict, as_agreed_id: str) -> List[str]:
    """The fields of the commitment's own record that differ from the row registered for it, in the record's order.
    Key order cannot matter and neither can the order of `carried_by` entries (they are a mapping, written as a
    list); the row's extra keys (`format`, `pipeline`, `run_id`) are the stamps of `register`, not commitment fields."""
    mine = _plain(c.record(as_agreed_id))
    theirs = _plain({k: row.get(k) for k in mine})
    for rec in (mine, theirs):
        rec["carried_by"] = sorted(rec.get("carried_by") or [], key=lambda e: json.dumps(e, sort_keys=True))
    return [k for k in mine if not _same(mine[k], theirs[k])]


def _disagreements(record: AsAgreed, rows: List[dict], run_id: str) -> List[Tuple[str, str]]:
    """(commitment id, why) for each commitment of `record` that differs from the `commitment` rows registered for it."""
    recorded: Dict[str, dict] = {}
    for r in rows:
        if r.get("kind") == "commitment" and r.get("as_agreed") == record.id and r.get("run_id") == run_id:
            recorded.setdefault(str(r.get("commitment_id")), r)
    held: Dict[str, Commitment] = {}
    out: List[Tuple[str, str]] = []
    for c in record.commitments:
        if c.commitment_id in held:
            out.append((c.commitment_id, "appears more than once in the record in memory"))
        held[c.commitment_id] = c
    for cid in sorted(set(recorded) | set(held)):
        if cid not in held:
            out.append((cid, "was registered and is missing from the record in memory"))
        elif cid not in recorded:
            out.append((cid, "is in the record in memory and was never registered"))
        else:
            try:
                changed = _changed_fields(held[cid], recorded[cid], record.id)
            except (ValueError, TypeError, AttributeError) as e:
                out.append((cid, f"cannot be compared with its registered row ({e})"))
                continue
            if changed:
                out.append((cid, f"differs from what was registered, in {', '.join(changed)}"))
    return out


def _require_registered(record: Any, ad: Adapter, run_id: str) -> None:
    """The one test of SPEC §2.2 and §3, shared by the decorator, `check` and `hold`: the record is registered in the
    sink for this run, and every commitment in memory is still the one that was registered. Reads the sink once and
    writes nothing."""
    rid = getattr(record, "id", None)
    rows = ad.records(run_id)
    if rid is None or not any(r.get("kind") == "as_agreed" and r.get("id") == rid
                              and r.get("run_id") == run_id for r in rows):
        raise NoCommitment(f"as-agreed record {rid} is not registered in run {run_id}; use register(...)")
    bad = _disagreements(record, rows, run_id)
    if bad:
        raise NoCommitment(
            f"as-agreed record {rid} no longer matches what was registered in run {run_id}: "
            + "; ".join(f"commitment {cid} {why}" for cid, why in bad)
            + ". The as-agreed record is the reference and cannot be changed after registration; "
              "register a new version (a higher major) instead")


@contextlib.contextmanager
def run(agreed: AsAgreed):
    """Make `agreed` the current as-agreed record for steps decorated without one (a long-lived process)."""
    token = set_current_as_agreed(agreed)
    try:
        yield agreed
    finally:
        reset_current_as_agreed(token)


# ---------- schema ----------
def _validate_schema(schema: Dict[str, Any]) -> None:
    for k, t in schema.items():
        if t not in SCHEMA_TYPES:
            raise ValueError(f"schema type for {k!r} must be a bare type among float, int, str, bool, list, dict")


def check_schema(outputs: Any, schema: Dict[str, Any]) -> List[str]:
    """Every declared field present with its bare type; float accepts an int; int refuses a bool."""
    try:
        data = to_jsonable(outputs)
    except ValueError as e:
        return [str(e)]
    if not isinstance(data, dict):
        return [f"outputs are {type(outputs).__name__}, not a mapping"]
    problems: List[str] = []
    for k, t in schema.items():
        if k not in data:
            problems.append(f"{k}: missing")
            continue
        v = data[k]
        if t is float:
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        elif t is int:
            ok = isinstance(v, int) and not isinstance(v, bool)
        else:
            ok = isinstance(v, t)
        if not ok:
            problems.append(f"{k}: expected {t.__name__}, got {type(v).__name__}")
    return problems


# ---------- the decorator ----------
class _Execution:
    """One call of a decorated step: the attempts, the checks, the hold, the hooks (SPEC §3)."""

    def __init__(self, fn: Callable, args: tuple, kwargs: dict, *, name: str, agreed: Optional[AsAgreed],
                 adapter: Optional[Adapter], commit_fn: Optional[Callable], schema: Optional[Dict[str, Any]],
                 policy: Policy, tiers: Tuple[Any, ...], hold_to: Optional[str], reads: Tuple[str, ...],
                 writes: Tuple[str, ...], link_type: Optional[str] = None, on_missing_key: str = "dropped"):
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.name, self.schema, self.policy, self.tiers = name, schema, policy, tiers
        self.commit_fn, self.hold_to, self.reads, self.writes = commit_fn, hold_to, reads, writes
        self.link_type = link_type
        self.on_missing_key = on_missing_key
        cur = current_as_agreed()
        record = cur if cur is not None else agreed  # inside ab.run(), the run's as-agreed record wins
        ad = adapter or (getattr(record, "adapter", None) if record is not None else None)
        if ad is None:
            raise NoCommitment("no as-agreed record for this step: pass agreed=register(...), or run it inside ab.run(agreed)")
        self.ad = ad
        self.ctx = ad.context(name, args, kwargs, agreed=record, policy=policy, hold_to=hold_to)
        if self.ctx.kind == "workflow":
            raise WorkflowContextError(f"step {name!r} was decorated in workflow code; decorate the activity body")
        self.ctx.body = fn
        self.ctx.commit = commit_fn
        record = self.ctx.as_agreed
        if record is None:
            raise NoCommitment("no commitment record registered for this run; call register(...) first")
        _require_registered(record, ad, self.ctx.run_id)
        self.agreed = record
        self.inputs_hash = inputs_hash(args, kwargs)
        self.key = idempotency_key(record.digest, self.ctx.run_id, name, self.ctx.invocation, self.inputs_hash)
        self.attempt = 1
        self.records: List[dict] = []

    def rec(self, record: dict) -> None:
        self.ad.record(record)
        self.records.append(record)

    def first(self) -> Any:
        return call_with_reason(self.fn, self.args, self.kwargs, None)

    def process(self, outputs: Any) -> Tuple[str, Any]:
        """One attempt's outputs: ('done', result) | ('retry', reason) | ('hold', result)."""
        ctx, agreed, pol = self.ctx, self.agreed, self.policy
        ts = self.ad.clock()
        lid = link_id(self.name, ctx.invocation, self.attempt)
        delivered = pol.role == "commit"
        if self.schema is not None:
            problems = check_schema(outputs, self.schema)
            if problems:
                self.rec({"kind": "schema_failure", "format": FORMAT, "pipeline": ctx.pipeline, "run_id": ctx.run_id,
                          "link": lid, "problems": problems, "ts": ts})
                raise SchemaError(problems, self.name)
        text = to_lines(outputs)
        self.rec({"kind": "link", "format": FORMAT, "pipeline": ctx.pipeline, "run_id": ctx.run_id, "step": self.name,
                  "invocation": ctx.invocation, "attempt": self.attempt, "id": lid, "parent": ctx.parent,
                  "role": pol.role, "state": "as_delivered" if delivered else "as_briefed",
                  "link_type": self.link_type, "text": text,
                  "inputs_hash": self.inputs_hash, "idempotency_key": self.key, "reads": list(self.reads),
                  "writes": list(self.writes), "ts": ts})
        history = self.ad.history(ctx.run_id)
        verdicts, failures = check_link(outputs, agreed, run_id=ctx.run_id, link_id=lid, ts=ts, history=history,
                                        tiers=self.tiers, pipeline=ctx.pipeline, delivered=delivered,
                                        link_type=self.link_type, first_changed=self.ad.first_changed(ctx.run_id),
                                        on_missing_key=self.on_missing_key)
        for v in verdicts:
            self.rec(v.record())
        for f in failures:
            self.rec(f)
        h = decide(verdicts, agreed, pol, failures, run_id=ctx.run_id, link_id=lid, attempt=self.attempt, ts=ts,
                   pipeline=ctx.pipeline)
        self.rec(h.record())
        outcome = Outcome(h.passed, h.code, outputs, [v.record() for v in verdicts], list(h.lines), self.attempt,
                          self.key, list(self.records), lid)
        ctx.outcome = outcome
        if h.passed or pol.on_loss == "record":
            found = agreed.add_found(cut(claim_text(outputs), found_at=lid, version=agreed.version,
                                         min_words=agreed.min_words).claims)
            for c in found:
                self.rec(_stamp(c.record(agreed.id), ctx.pipeline, ctx.run_id))
            committed = self.ad.commit(ctx, outputs, self.key)
            if inspect.isawaitable(committed):  # an un-awaited coroutine means the commit never happened
                raise ValueError(f"the commit hook of step {self.name!r} returned an awaitable; a commit must have "
                                 f"taken effect before the gate reports a pass")
            return "done", committed
        if self.attempt <= pol.retries:
            nxt = link_id(self.name, ctx.invocation, self.attempt + 1)
            self.rec(retry_record(ctx.run_id, lid, nxt, len(h.lines), ts, ctx.pipeline))
            self.attempt += 1
            return "retry", list(h.lines)
        self.ad.discard(ctx, outputs)
        return "hold", self.ad.raise_hold(ctx, h)


def _execute(fn: Callable, args: tuple, kwargs: dict, **opts: Any) -> Any:
    ex = _Execution(fn, args, kwargs, **opts)
    outputs = ex.first()
    while True:
        state, value = ex.process(outputs)
        if state == "retry":
            outputs = ex.ad.rerun(ex.ctx, value)
            continue
        return value


async def _aexecute(fn: Callable, args: tuple, kwargs: dict, **opts: Any) -> Any:
    ex = _Execution(fn, args, kwargs, **opts)
    outputs = await acall_with_reason(fn, args, kwargs, None)
    while True:
        state, value = ex.process(outputs)
        if state == "retry":
            token = set_reason(value)  # held across the await: the body runs after `rerun` has returned
            try:
                outputs = ex.ad.rerun(ex.ctx, value)  # through the adapter's hook, as on the sync path
                if inspect.isawaitable(outputs):
                    outputs = await outputs
            finally:
                reset_reason(token)
            continue
        return value


def step(name: str, agreed: Optional[AsAgreed] = None, *, commit: Any = None, schema: Optional[Dict[str, Any]] = None,
         retries: int = 1, on_loss: Optional[str] = None, fail_on: Tuple[str, ...] = DEFAULT_FAIL_ON,
         on_undecided: Optional[str] = None, tiers: Iterable[Any] = (), hold_to: Optional[str] = None,
         reads: Iterable[str] = (), writes: Iterable[str] = (), adapter: Optional[Adapter] = None,
         link_type: Optional[str] = None, on_missing_key: str = "dropped") -> Callable:
    """Wrap one step: compare what it declares with the as-agreed record, hold a commit where a field drifted, retry
    with the reason. `link_type` (schema-write, step, compression, delivery) names the site of a change first seen
    here (D-25); an untyped step leaves the site to a reader. `on_missing_key="undeclared"` reads a carried key the
    step's outputs do not hold as not addressed (UNDECLARED) rather than DROPPED (SPEC §1.2, draft 8)."""
    if link_type is not None and link_type not in LINK_TYPES:
        raise ValueError(f"link_type {link_type!r} is not one of {', '.join(LINK_TYPES)}")
    if on_missing_key not in MISSING_KEY_MODES:
        raise ValueError(f"on_missing_key is one of {', '.join(MISSING_KEY_MODES)}, not {on_missing_key!r}")
    if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0 or retries > MAX_RETRIES:
        raise ValueError(f"retries must be an integer from 0 to {MAX_RETRIES}, got {retries!r}")
    if commit is not None and commit is not True and not callable(commit):
        raise ValueError(f"commit= takes a callable, True, or nothing, not {type(commit).__name__}; a step that "
                         f"cannot call it would silently become an intermediate step")
    commit_fn = commit if callable(commit) else None
    is_commit = commit is True or commit_fn is not None
    if commit_fn is not None and not accepts_kw(commit_fn, "idempotency_key"):
        raise ValueError("the commit callable must accept idempotency_key= (the key is never dropped silently)")
    if commit_fn is not None and _is_async(commit_fn):
        raise ValueError("an async commit callable is not in v0.1; wrap the await in a plain function")
    if is_commit and on_loss == "record":
        raise ValueError("a commit step cannot record a loss and still commit; drop on_loss or drop commit=")
    if schema is not None:
        _validate_schema(schema)
    tiers = tuple(tiers)
    for t in tiers:
        fn_of = getattr(t, "fn", None)
        if fn_of is not None and _is_async(fn_of):
            raise ValueError("an async tier callable is not in v0.1; wrap the await in a plain function")
    policy = Policy("commit" if is_commit else "intermediate",
                    on_loss or ("hold" if is_commit else "record"), tuple(fail_on), retries,
                    on_undecided or ("hold" if is_commit else "record"))
    opts = dict(name=name, agreed=agreed, adapter=adapter, commit_fn=commit_fn, schema=schema, policy=policy,
                tiers=tiers, hold_to=hold_to, reads=tuple(reads), writes=tuple(writes), link_type=link_type,
                on_missing_key=on_missing_key)

    def deco(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                return await _aexecute(fn, args, kwargs, **opts)
            awrapper.__as_agreed__ = policy  # type: ignore[attr-defined]
            return awrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return _execute(fn, args, kwargs, **opts)
        wrapper.__as_agreed__ = policy  # type: ignore[attr-defined]
        return wrapper

    return deco


# ---------- the verbs as functions ----------
def check(outputs: Any, agreed: AsAgreed, *, link: str = "check#1@1", tiers: Iterable[Any] = (),
          history: Any = None, delivered: bool = False, adapter: Optional[Adapter] = None,
          link_type: Optional[str] = None, on_missing_key: str = "dropped") -> List[Verdict]:
    """Reconcile one link against the as-agreed record as a dry run (SPEC §2.2): the verdicts come back and nothing
    is recorded; the decorator and `reconcile` are the recording paths.

    history: None reads the run's records through the adapter; NO_HISTORY says they are unavailable; a mapping is used as is.
    """
    ad = adapter or getattr(agreed, "adapter", None)
    if ad is None:
        raise NoCommitment("the as-agreed record carries no adapter; register it first")
    _require_registered(agreed, ad, ad.run_id)
    if history is NO_HISTORY:
        hist = None
    elif history is None:
        hist = ad.history(ad.run_id)
    else:
        hist = history
    if link_type is not None and link_type not in LINK_TYPES:
        raise ValueError(f"link_type {link_type!r} is not one of {', '.join(LINK_TYPES)}")
    verdicts, _ = check_link(outputs, agreed, run_id=ad.run_id, link_id=link, ts=ad.clock(), history=hist, tiers=tiers,
                             pipeline=ad.pipeline, delivered=delivered, link_type=link_type,
                             first_changed=ad.first_changed(ad.run_id), on_missing_key=on_missing_key)
    return verdicts


def hold(verdicts: Iterable[Verdict], agreed: AsAgreed, *, policy: Optional[Policy] = None, link: str = "check#1@1",
         tier_failures: Iterable[dict] = (), adapter: Optional[Adapter] = None) -> Hold:
    """Decide the hold for verdicts already in hand (SPEC §2.2): refuses, as `check` does, a record that is not
    registered in the adapter's sink for the run or no longer matches it; nothing is written."""
    ad = adapter or getattr(agreed, "adapter", None)
    if ad is None:
        raise NoCommitment("the as-agreed record carries no adapter; register it first")
    _require_registered(agreed, ad, ad.run_id)
    pol = policy or Policy("commit", "hold", on_undecided="hold")
    return decide(verdicts, agreed, pol, tier_failures, run_id=ad.run_id, link_id=link,
                  attempt=1, ts=ad.clock(), pipeline=agreed.pipeline)


def retry(fn: Callable, args: tuple = (), kwargs: Optional[dict] = None, *, reason: List[str]) -> Any:
    """The bare re-call: run a body once more with the DANGER lines as the reason. The bounded loop of SPEC §2.4,
    with its checks, records and the hold, lives in the decorator."""
    return call_with_reason(fn, args, kwargs or {}, list(reason))


def report(agreed_or_adapter: Any, out_dir: Optional[str] = None, *, title: str = "", per_unit: bool = True,
           quiet: bool = False, recall: Iterable[RecallRow] = (), values: bool = False,
           expand_claims: bool = False) -> Report:
    """ledger.jsonl, counts.csv, flow.svg, and the printed lines of SPEC §2.5 (quiet=True writes without printing).
    The map is in its shared form unless asked (SPEC §7): `expand_claims=True` draws one ribbon per claim instead of one
    band per link, `values=True` puts the agreed and arrived values on it, so that file is not for sending."""
    ad = agreed_or_adapter if isinstance(agreed_or_adapter, Adapter) else getattr(agreed_or_adapter, "adapter", None)
    if ad is None:
        raise NoCommitment("nothing to report: pass a registered as-agreed record or an adapter")
    out = out_dir or getattr(ad, "run_dir", ".")
    rep = build_report(ad.records(ad.run_id), out, title=title, run_id=ad.run_id, recall=recall, values=values,
                       expand_claims=expand_claims)
    if not quiet:
        print(rep.text(per_unit))
    return rep
