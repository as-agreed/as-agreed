"""The LangGraph adapter (SPEC §5.2, second column): the records live under one declared state key, persisted by the
graph's checkpointer; a hold is a `Command` to the hold target.

    from typing import Annotated, TypedDict
    from as_agreed.adapters.langgraph import LangGraphAdapter, merge

    class State(TypedDict, total=False):
        invoice: dict
        as_agreed: Annotated[dict, merge]          # the one reserved key, with the reducer this module ships

    lg = LangGraphAdapter()

    def start(state):                              # the entry node registers the order of this invocation
        return lg.register(state, ORDER, COMMITMENTS)

    @lg.step("commit", commit=pay, schema=SCHEMA, hold_to="review")
    def commit(state):
        return {"amount": ..., "po": ...}

    graph = lg.attach(builder.compile(checkpointer=InMemorySaver()))

This module imports LangGraph only inside the functions that need it, so `import as_agreed` works without it;
a call that needs it and does not find it raises `ImportError` naming the package to install.

Two ways a step runs. Inside a graph node (LangGraph's config is present) the records of the run are read from the
state key and the step's own records travel in the node's returned update. Called directly, outside any graph (a
node function under test, and the conformance suite), the adapter writes each record through its `CheckpointSink`:
a one-key graph on a checkpointer, so every record is in a checkpoint before the call returns.
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional

from ..errors import DriftHold
from ..reason import call_with_reason
from ..serialise import RESERVED
from .base import Adapter, Binding, Sink, StepContext, rebuild

KEY = RESERVED  # "as_agreed": one declared field holding a mapping, never a dotted name
_SCOPE: contextvars.ContextVar = contextvars.ContextVar("as_agreed_langgraph_scope", default=None)


def _need_langgraph() -> None:
    try:
        import langgraph  # noqa: F401
    except ImportError:
        raise ImportError("the LangGraph adapter needs the langgraph package: pip install langgraph") from None


def merge(left: Optional[Mapping], right: Optional[Mapping]) -> dict:
    """The reducer of the state key: records are appended, every other entry is replaced by the newer value."""
    out = dict(left or {})
    new = dict(right or {})
    records = list(out.get("records") or []) + list(new.pop("records", None) or [])
    out.update(new)
    out["records"] = records
    return out


def _plain(record: dict) -> dict:
    return json.loads(json.dumps(record, ensure_ascii=False, default=str))


def _config() -> Optional[dict]:
    """LangGraph's config when this call runs inside a graph node, else None."""
    try:
        from langgraph.config import get_config
    except ImportError:
        return None
    try:
        return get_config()
    except RuntimeError:
        return None


class _Scope:
    """One node execution: the run's records as the state holds them, and the records this node adds."""

    def __init__(self, owner: Any, state_records: List[dict], run_id: str, thread_id: str):
        self.owner = owner
        self.state_records = state_records
        self.buffer: List[dict] = []
        self.run_id = run_id
        self.thread_id = thread_id
        self.update: Optional[dict] = None


class CheckpointSink:
    """Records under the state key of a one-node graph, one checkpoint per record. `reopen()` reloads the
    checkpoint: a new object over the same checkpointer and thread reads only what the checkpointer kept.
    It answers the sink's three calls without subclassing `Sink`: it is the engine's own state, not a destination
    (SPEC §2.6, DS3)."""

    def __init__(self, checkpointer: Any = None, thread_id: Optional[str] = None, key: str = KEY):
        _need_langgraph()
        from typing import Annotated, TypedDict

        from langgraph.graph import END, START, StateGraph
        if checkpointer is None:
            from langgraph.checkpoint.memory import InMemorySaver
            checkpointer = InMemorySaver()
        self.checkpointer = checkpointer
        self.thread_id = thread_id or uuid.uuid4().hex
        self.key = key
        state = TypedDict("CheckpointState", {key: Annotated[dict, merge]}, total=False)  # type: ignore[misc]
        builder = StateGraph(state)
        builder.add_node("ledger", lambda s: {})
        builder.add_edge(START, "ledger")
        builder.add_edge("ledger", END)
        self.graph = builder.compile(checkpointer=checkpointer)
        self.config = {"configurable": {"thread_id": self.thread_id}}
        self._cache: Optional[List[dict]] = None

    def append(self, record: dict) -> None:
        rec = _plain(record)
        self.graph.update_state(self.config, {self.key: {"records": [rec]}}, as_node="ledger")
        if self._cache is not None:
            self._cache.append(rec)

    def _load(self) -> List[dict]:
        values = self.graph.get_state(self.config).values or {}
        return list((values.get(self.key) or {}).get("records") or [])

    def read(self) -> List[dict]:
        # the checkpoint is read once per sink object; every later append goes to the checkpoint and to this copy
        if self._cache is None:
            self._cache = self._load()
        return list(self._cache)

    def reopen(self) -> "CheckpointSink":
        return CheckpointSink(self.checkpointer, self.thread_id, self.key)


def _command_update(result: Any) -> Optional[dict]:
    if type(result).__name__ == "Command" and hasattr(result, "update"):
        upd = result.update
        return dict(upd) if isinstance(upd, Mapping) else None
    return None


def _code_of(result: Any) -> int:
    if isinstance(result, BaseException):
        return int(getattr(result, "code", 2))
    upd = _command_update(result)
    if upd is None and isinstance(result, Mapping) and isinstance(result.get(KEY), Mapping):
        upd = dict(result)
    if upd is not None and isinstance(upd.get(KEY), Mapping) and upd[KEY].get("code") is not None:
        return int(upd[KEY]["code"])
    return int(getattr(result, "code", 0))


def _is_hold(result: Any) -> bool:
    if isinstance(result, DriftHold):
        return True
    upd = _command_update(result)
    if upd is None and isinstance(result, Mapping) and isinstance(result.get(KEY), Mapping):
        upd = dict(result)
    if upd is not None and isinstance(upd.get(KEY), Mapping):
        return bool(upd[KEY].get("held"))
    return bool(getattr(result, "passed", True) is False)


def _is_runnable(fn: Any) -> bool:
    try:
        from langchain_core.runnables import Runnable
    except ImportError:
        return False
    return isinstance(fn, Runnable)


class LangGraphAdapter(Adapter):
    """`LangGraphAdapter(graph=None, *, run_id=None, pipeline=None, checkpointer=None, thread_id=None)`.

    `run_id`, `checkpointer` and `thread_id` serve the direct calls (outside a graph); inside a graph the run id
    comes from the state key, where the entry node's `register` put it."""

    def __init__(self, graph: Any = None, *, run_id: Optional[str] = None, pipeline: Optional[str] = None,
                 checkpointer: Any = None, thread_id: Optional[str] = None, sink: Optional[Sink] = None):
        _need_langgraph()
        self.run_id = run_id or uuid.uuid4().hex
        self.pipeline = pipeline or "default"
        self._direct_sink = sink if sink is not None else CheckpointSink(checkpointer, thread_id)
        self.binding = Binding("LangGraph", _code_of, _is_hold, lambda s: s.reopen(), None)
        self.graph: Any = None
        if graph is not None:
            self.attach(graph)

    # ---- the sink: the state key inside a node, the checkpoint sink outside ----
    @property
    def sink(self) -> Sink:
        return self._direct_sink

    @sink.setter
    def sink(self, value: Sink) -> None:
        self._direct_sink = value

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

    # ---- set-up ----
    def attach(self, graph: Any) -> Any:
        """Check the compiled graph once: the state declares the key, and no decorated commit node has a static
        outgoing edge (a `Command` to the hold target would run beside it). Returns the graph."""
        builder = getattr(graph, "builder", None)
        if builder is None:
            raise ValueError("attach() takes a compiled StateGraph (graph = builder.compile(...))")
        if KEY not in getattr(builder, "channels", {}):
            raise ValueError(f"the graph's state declares no {KEY!r} key; add {KEY}: Annotated[dict, merge] "
                             f"(from as_agreed.adapters.langgraph import merge)")
        for name, spec in builder.nodes.items():
            fn = getattr(getattr(spec, "runnable", None), "func", None)
            policy = getattr(fn, "__as_agreed__", None)
            if policy is not None and policy.role == "commit":
                static = sorted(dst for src, dst in builder.edges if src == name)
                if static:
                    raise ValueError(f"commit node {name!r} has a static outgoing edge to {', '.join(static)}; a "
                                     f"decorated commit node routes by its own Command, so remove the edge")
        self.graph = graph
        return graph

    def step(self, name: str, agreed: Any = None, **kw: Any) -> Callable:
        """The decorator of SPEC §3 for a node callable; a prebuilt `Runnable` is refused (A4)."""
        from .. import sdk
        inner = sdk.step(name, agreed, adapter=self, **kw)

        def deco(fn: Callable) -> Callable:
            if _is_runnable(fn):
                raise ValueError(f"step {name!r}: a Runnable cannot be decorated in v0.1; decorate a node function")
            wrapped = inner(fn)
            if inspect.iscoroutinefunction(fn):
                return wrapped

            @functools.wraps(fn)  # LangGraph reads the body's signature (config, store, writer) through this
            def node(*args: Any, **kwargs: Any) -> Any:
                token = _SCOPE.set(None)  # a node's records never outlive the node, even when it raises
                try:
                    return wrapped(*args, **kwargs)
                finally:
                    _SCOPE.reset(token)
            node.__as_agreed__ = getattr(wrapped, "__as_agreed__", None)  # type: ignore[attr-defined]
            return node
        return deco

    def register(self, state: Any, order: str, commitments: Any = (), **kw: Any) -> Any:
        """Inside the entry node: register this invocation's as-agreed record and return the state update that
        carries it, with a fresh run id and `held` and `reason` reset (A8). Outside a graph: `sdk.register`."""
        from .. import sdk
        cfg = _config()
        if cfg is None:
            _SCOPE.set(None)
            return sdk.register(order, commitments, adapter=self, **kw)
        held = dict((state or {}).get(KEY) or {}) if isinstance(state, Mapping) else {}
        records = list(held.get("records") or [])
        configurable = cfg.get("configurable") or {}
        thread = str(configurable.get("thread_id") or "no-thread")
        given = configurable.get("run_id") or (cfg.get("metadata") or {}).get("run_id")
        runs = {r.get("run_id") for r in records if r.get("kind") == "as_agreed"}
        run_id = f"{thread}/{given}" if given else f"{thread}/r{len(runs) + 1}"
        scope = _Scope(self, records, run_id, thread)
        token = _SCOPE.set(scope)
        try:
            sdk.register(order, commitments, adapter=self, **kw)
        finally:
            _SCOPE.reset(token)
        return {KEY: {"records": scope.buffer, "run_id": run_id, "held": False, "reason": None, "code": None,
                      "committed": None}}

    # ---- hooks ----
    def context(self, step: str, args: tuple, kwargs: dict, *, agreed: Any, policy: Any,
                hold_to: Optional[str] = None) -> StepContext:
        cfg = _config()
        if cfg is not None:
            state = args[0] if args else None
            if not isinstance(state, Mapping) or not isinstance(state.get(KEY), Mapping):
                raise ValueError(f"step {step!r}: the state has no {KEY!r} key holding a mapping; declare "
                                 f"{KEY}: Annotated[dict, merge] and register in the entry node")
            if policy.role == "commit" and self.graph is None:
                raise ValueError(f"step {step!r} is a commit node and no compiled graph was attached; call "
                                 f"lg.attach(graph), which refuses a static outgoing edge from a commit node")
            held = state[KEY]
            run_id = held.get("run_id")
            if not run_id:
                raise ValueError(f"step {step!r}: no run id under {KEY!r}; the entry node returns lg.register(...)")
            thread = str((cfg.get("configurable") or {}).get("thread_id") or "no-thread")
            _SCOPE.set(_Scope(self, list(held.get("records") or []), str(run_id), thread))
            record = agreed if agreed is not None else rebuild(self.records(str(run_id)), str(run_id), self)
            return StepContext(str(run_id), step, self.invocation(step, str(run_id)), 1, policy.role, "node", args,
                               dict(kwargs), body=None, commit=None, policy=policy, as_agreed=record,
                               history=self.history(str(run_id)), sink=self.sink, pipeline=self.pipeline,
                               hold_to=hold_to, parent=self.last_link(str(run_id)))
        _SCOPE.set(None)
        record = agreed if agreed is not None else rebuild(self.records(), self.run_id, self)
        return StepContext(self.run_id, step, self.invocation(step), 1, policy.role, "node", args, dict(kwargs),
                           body=None, commit=None, policy=policy, as_agreed=record, history=self.history(),
                           sink=self.sink, pipeline=self.pipeline, hold_to=hold_to, parent=self.last_link())

    def rerun(self, ctx: StepContext, reason: List[str]) -> Any:
        return call_with_reason(ctx.body, ctx.args, ctx.kwargs, reason)

    def _update(self, scope: Optional[_Scope], **entries: Any) -> dict:
        body = {"records": list(scope.buffer) if scope is not None else []}
        if scope is not None:
            body["run_id"] = scope.run_id
        body.update(entries)
        return {KEY: body}

    def commit(self, ctx: StepContext, outputs: Any, key: str) -> Any:
        if ctx.commit is not None:
            ctx.commit(outputs, idempotency_key=key)
        scope = self._scope()
        if scope is None:
            return outputs
        if not isinstance(outputs, Mapping):
            raise ValueError(f"node {ctx.step!r} returned {type(outputs).__name__}; a node returns a mapping")
        update = {k: v for k, v in outputs.items() if k != KEY}
        update.update(self._update(scope, held=False, reason=None, code=0,
                                   committed=key if ctx.policy.role == "commit" else None))
        _SCOPE.set(None)
        return update

    def discard(self, ctx: StepContext, outputs: Any) -> Any:
        scope = self._scope()
        lines = list(getattr(ctx.outcome, "hold", None) or [])
        update = self._update(scope, held=True, reason=lines, code=1, committed=None)
        if scope is not None:
            scope.update = update
        return update

    def raise_hold(self, ctx: StepContext, hold: Any) -> Any:
        from langgraph.graph import END
        from langgraph.types import Command
        scope = self._scope()
        if scope is not None and scope.update is not None:
            update = scope.update
            _SCOPE.set(None)
        else:
            update = {KEY: {"records": [], "held": True, "reason": list(hold.lines), "code": 1}}
        return Command(update=update, goto=ctx.hold_to or END)

    def record(self, record: dict) -> None:
        scope = self._scope()
        if scope is not None:
            scope.buffer.append(_plain(record))
        else:
            self.sink.append(record)

    def records(self, run_id: Optional[str] = None) -> List[dict]:
        scope = self._scope()
        if scope is not None:
            rid = run_id or scope.run_id
            return [r for r in scope.state_records + scope.buffer if r.get("run_id") == rid]
        rid = run_id or self.run_id
        return [r for r in self.sink.read() if r.get("run_id") == rid]

    def clock(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def langgraph_adapter(run_id: Optional[str] = None, pipeline: Optional[str] = None) -> LangGraphAdapter:
    """The suite's factory: a fresh in-memory checkpointer and thread per adapter."""
    return LangGraphAdapter(run_id=run_id, pipeline=pipeline)
