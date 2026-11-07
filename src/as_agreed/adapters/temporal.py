"""The Temporal adapter (SPEC §5.2, third column, and the determinism rule of §5.3): the record is computed in an
activity, never in workflow code.

    from as_agreed.adapters.temporal import TemporalAdapter, raise_if_held

    ta = TemporalAdapter()

    @activity.defn
    def register_order(order: str) -> dict:
        return ta.register(order, COMMITMENTS, run=[])           # {"records": [...]}: the workflow keeps them

    @activity.defn(name="pay")
    @ta.step("commit", commit=pay, schema=SCHEMA)
    def pay_body(note: str) -> dict:                             # the activity is called with (note, records)
        return {...}

    @workflow.defn
    class Pay:
        @workflow.run
        async def run(self, order: str) -> dict:
            records = (await workflow.execute_activity(register_order, order, ...))["records"]
            out = await workflow.execute_activity("pay", args=[note, records], result_type=Outcome, ...)
            records += out.records
            raise_if_held(out)                                    # deterministic: usable in workflow code
            return out.outputs

A decorated activity takes the run's records as its last positional argument; the wrapper strips it before the
step's inputs are hashed, rebuilds the as-agreed record from it, and returns an `Outcome` carrying the records the
step wrote. Model calls, every decorated body and every tier callable run only inside the activity: a decorated
body executed in workflow code is refused with `WorkflowContextError` before it runs (A4).

This module imports temporalio only inside the functions that need it, so `import as_agreed` works without it.

Not built yet (SPEC §5.2): the attempt count and the reason resumed across a Temporal retry (T2's engine part),
the labels mode and the per-unit summary in place of the full records, and the activity id as the discriminator
of parallel invocations.
"""
from __future__ import annotations

import contextvars
import dataclasses
import json
import time
import uuid
from typing import Any, List, Mapping, Optional

from ..errors import AsAgreedError, DriftHold, WorkflowContextError
from ..reason import call_with_reason
from ..serialise import canonical, sha256, to_jsonable
from .base import Adapter, Binding, Outcome, StepContext, rebuild

_SCOPE: contextvars.ContextVar = contextvars.ContextVar("as_agreed_temporal_scope", default=None)
CODES = {"DriftHold": 1, "NoCommitment": 2, "AsAgreedExists": 2, "EmptyAsAgreed": 2, "WorkflowContextError": 2,
         "SchemaError": 3}


def _need_temporal() -> None:
    try:
        import temporalio  # noqa: F401
    except ImportError:
        raise ImportError("the Temporal adapter needs the temporalio package: pip install temporalio") from None


def _in_workflow() -> bool:
    try:
        from temporalio import workflow
    except ImportError:
        return False
    return bool(workflow.in_workflow())


def _in_activity() -> bool:
    try:
        from temporalio import activity
    except ImportError:
        return False
    return bool(activity.in_activity())


def _plain(record: dict) -> dict:
    return json.loads(json.dumps(record, ensure_ascii=False, default=str))


class HistorySink:
    """The records a workflow carries between activities, held in memory. `reopen()` replays them: a new object
    over a copy of the list, as the next activity receives it. Not a `Sink` subclass: the engine's state, not a
    destination (SPEC §2.6, DS3)."""

    def __init__(self, records: Optional[List[dict]] = None):
        self.store: List[dict] = list(records or [])

    def append(self, record: dict) -> None:
        self.store.append(_plain(record))

    def read(self) -> List[dict]:
        return list(self.store)

    def reopen(self) -> "HistorySink":
        return HistorySink(_plain({"r": self.store})["r"])


class _Scope:
    def __init__(self, owner: Any, records: List[dict], run_id: str, info: Any):
        self.owner = owner
        self.records = records
        self.buffer: List[dict] = []
        self.run_id = run_id
        self.info = info
        self.attempt = 1


def _app_error(result: Any) -> Any:
    """The ApplicationError behind a result, following the causes Temporal wraps it in (ActivityError, ...)."""
    seen = 0
    e = result
    while isinstance(e, BaseException) and seen < 5:
        if type(e).__name__ == "ApplicationError":
            return e
        e = getattr(e, "cause", None) or e.__cause__
        seen += 1
    return None


def _code_of(result: Any) -> int:
    if isinstance(result, Outcome):
        return int(result.code)
    if isinstance(result, BaseException):
        app = _app_error(result)
        if app is not None:
            return CODES.get(getattr(app, "type", None) or "", 2)
        return int(getattr(result, "code", 2))
    if isinstance(result, Mapping) and "code" in result and "passed" in result:
        return int(result["code"])
    return int(getattr(result, "code", 0))


def _is_hold(result: Any) -> bool:
    if isinstance(result, Outcome):
        return not result.passed
    if isinstance(result, DriftHold):
        return True
    app = _app_error(result) if isinstance(result, BaseException) else None
    if app is not None:
        return getattr(app, "type", None) == "DriftHold"
    if isinstance(result, Mapping) and "passed" in result:
        return not result["passed"]
    return bool(getattr(result, "passed", True) is False)


def _outputs_of(result: Any) -> Any:
    if isinstance(result, Outcome):
        return result.outputs
    if isinstance(result, Mapping) and "outputs" in result and "passed" in result:
        return result["outputs"]
    return result


def raise_if_held(outcome: Any) -> Any:
    """In workflow code: a held `Outcome` becomes a non-retryable `ApplicationError` of type `DriftHold`, so the
    retry policy never re-runs the activity and a Saga can compensate. Deterministic; returns a passing outcome."""
    _need_temporal()
    from temporalio.exceptions import ApplicationError
    passed = outcome["passed"] if isinstance(outcome, Mapping) else outcome.passed
    lines = outcome.get("hold", []) if isinstance(outcome, Mapping) else outcome.hold
    if not passed:
        raise ApplicationError("drift hold", *list(lines or []), type="DriftHold", non_retryable=True)
    return outcome


class TemporalAdapter(Adapter):
    """`TemporalAdapter(*, run_id=None, pipeline=None)`.

    Inside an activity built with `step()` (or a `register(..., run=records)` call) the run is the workflow's: its
    id is the run id (the business identity, so a reset or a continue-as-new does not re-commit) and the records
    come from the activity's last argument. Any other call inside an activity uses the adapter's own record list,
    which is how the conformance suite runs inside one activity on a worker. Outside an activity a step is refused."""

    def __init__(self, *, run_id: Optional[str] = None, pipeline: Optional[str] = None, sink: Any = None):
        _need_temporal()
        self.run_id = run_id or uuid.uuid4().hex
        self.pipeline = pipeline or "default"
        self.sink = sink if sink is not None else HistorySink()
        self.binding = Binding("Temporal", _code_of, _is_hold, lambda s: s.reopen(), None, outputs_of=_outputs_of)
        self._written: List[dict] = []

    def _scope(self) -> Optional[_Scope]:
        scope = _SCOPE.get()
        return scope if scope is not None and scope.owner is self else None

    @property
    def run_id(self) -> str:  # type: ignore[override]
        """The run of the step being executed (from the engine), else the adapter's own run."""
        scope = self._scope()
        return scope.run_id if scope is not None else self._run_id

    @run_id.setter
    def run_id(self, value: str) -> None:
        self._run_id = value

    # ---- the activity side ----
    def register(self, order: str, commitments: Any = (), *, run: Any = None, **kw: Any) -> Any:
        """In a `register` activity: `run` is the records the workflow holds (empty at the start); returns
        `{"records": [...]}` for the workflow to keep. Without `run`: `sdk.register` on the adapter's own list."""
        from .. import sdk
        if _in_workflow():
            raise WorkflowContextError("register runs in an activity, never in workflow code (SPEC §5.3)")
        if run is None:
            return sdk.register(order, commitments, adapter=self, **kw)
        if not _in_activity():
            raise ValueError("register(..., run=...) runs inside a Temporal activity")
        from temporalio import activity
        info = activity.info()
        scope = _Scope(self, list(run.get("records") if isinstance(run, Mapping) else run), info.workflow_id, info)
        token = _SCOPE.set(scope)
        try:
            sdk.register(order, commitments, adapter=self, **kw)
        finally:
            _SCOPE.reset(token)
        return {"records": scope.buffer}

    def step(self, name: str, agreed: Any = None, **kw: Any) -> Any:
        """The decorator of SPEC §3 for an activity body. The returned function takes the body's arguments and the
        run's records last, and returns an `Outcome`; decorate it with `@activity.defn`."""
        from .. import sdk
        inner = sdk.step(name, agreed, adapter=self, **kw)

        def deco(fn: Any) -> Any:
            wrapped = inner(fn)

            def activity_body(*args: Any) -> Any:
                if _in_workflow():
                    raise WorkflowContextError(f"step {name!r} was called in workflow code; call the activity")
                if not args:
                    raise ValueError(f"activity {name!r} takes the run's records as its last argument")
                from temporalio import activity
                from temporalio.exceptions import ApplicationError
                *inputs, run = args
                info = activity.info()
                for d in reversed(list(info.heartbeat_details or [])):
                    if isinstance(d, Mapping) and d.get("committed"):
                        # a retry after the commit callable ran: the pass stands, the body is not run again
                        return Outcome(True, 0, None, [], [], int(d.get("attempt", 1)), str(d.get("key", "")),
                                       [], str(d.get("link", "")))
                records = list(run.get("records") if isinstance(run, Mapping) else (run or []))
                scope = _Scope(self, records, info.workflow_id, info)
                token = _SCOPE.set(scope)
                try:
                    return wrapped(*inputs)
                except AsAgreedError as e:
                    raise ApplicationError(str(e), {"records": scope.buffer, "code": e.code},
                                           type=type(e).__name__, non_retryable=True) from None
                finally:
                    _SCOPE.reset(token)

            activity_body.__name__ = getattr(fn, "__name__", name)
            activity_body.__qualname__ = getattr(fn, "__qualname__", name)
            activity_body.__doc__ = getattr(fn, "__doc__", None)
            activity_body.__as_agreed__ = getattr(wrapped, "__as_agreed__", None)  # type: ignore[attr-defined]
            return activity_body
        return deco

    # ---- hooks ----
    def context(self, step: str, args: tuple, kwargs: dict, *, agreed: Any, policy: Any,
                hold_to: Optional[str] = None) -> StepContext:
        if _in_workflow():  # the decorator refuses this kind before the body runs (A4); nothing else is read
            return StepContext("", step, 0, 0, policy.role, "workflow", args, dict(kwargs), body=None, commit=None,
                               policy=policy, as_agreed=agreed, history=None, sink=None, pipeline=self.pipeline)
        if not _in_activity():
            raise ValueError(f"step {step!r}: a Temporal step runs inside an activity (SPEC §5.3)")
        scope = self._scope()
        run_id = scope.run_id if scope is not None else self.run_id
        self._written = []
        record = agreed if agreed is not None and scope is None else rebuild(self.records(run_id), run_id, self)
        if agreed is not None and record is None:
            record = agreed
        return StepContext(run_id, step, self.invocation(step, run_id), 1, policy.role, "activity", args,
                           dict(kwargs), body=None, commit=None, policy=policy, as_agreed=record,
                           history=self.history(run_id), sink=self.sink, pipeline=self.pipeline, hold_to=hold_to,
                           parent=self.last_link(run_id))

    def _heartbeat(self, details: dict) -> None:
        if _in_activity():
            from temporalio import activity
            activity.heartbeat(details)

    def rerun(self, ctx: StepContext, reason: List[str]) -> Any:
        scope = self._scope()
        attempt = (scope.attempt if scope is not None else 1)
        self._heartbeat({"attempt": attempt, "lines": list(reason)})
        if scope is not None:
            scope.attempt += 1
        return call_with_reason(ctx.body, ctx.args, ctx.kwargs, reason)

    def _outcome(self, ctx: StepContext) -> Outcome:
        scope = self._scope()
        written = list(scope.buffer) if scope is not None else list(self._written)
        base = ctx.outcome
        return dataclasses.replace(base, records=written) if base is not None else Outcome(
            True, 0, None, [], [], 1, "", written, "")

    def commit(self, ctx: StepContext, outputs: Any, key: str) -> Any:
        if ctx.commit is not None:
            self._heartbeat({"committed": True, "key": key, "link": getattr(ctx.outcome, "link", ""),
                             "attempt": getattr(ctx.outcome, "attempts", 1),
                             "outputs_digest": sha256(canonical(to_jsonable(outputs)))[:16]})
            ctx.commit(outputs, idempotency_key=key)
        return self._outcome(ctx)

    def discard(self, ctx: StepContext, outputs: Any) -> Any:
        return None

    def raise_hold(self, ctx: StepContext, hold: Any) -> Any:
        out = self._outcome(ctx)
        return dataclasses.replace(out, passed=False, code=1, hold=list(hold.lines))

    def record(self, record: dict) -> None:
        scope = self._scope()
        if scope is not None:
            scope.buffer.append(_plain(record))
        else:
            self.sink.append(record)
            self._written.append(_plain(record))

    def records(self, run_id: Optional[str] = None) -> List[dict]:
        scope = self._scope()
        if scope is not None:
            rid = run_id or scope.run_id
            return [r for r in scope.records + scope.buffer if r.get("run_id") == rid]
        rid = run_id or self.run_id
        return [r for r in self.sink.read() if r.get("run_id") == rid]

    def clock(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def temporal_adapter(run_id: Optional[str] = None, pipeline: Optional[str] = None) -> TemporalAdapter:
    """The suite's factory: its own record list per adapter; the suite itself runs inside an activity."""
    return TemporalAdapter(run_id=run_id, pipeline=pipeline)

