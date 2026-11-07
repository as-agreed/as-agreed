"""The plain-Python adapter (SPEC §5.2, first column): no engine, a local ledger, a raised hold."""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, List, Optional

from ..errors import DriftHold
from ..reason import call_with_reason
from ..destination import LocalDestination
from .base import Adapter, Binding, MemorySink, Sink, StepContext


def _code_of(result: Any) -> int:
    if isinstance(result, BaseException):
        return int(getattr(result, "code", 2))
    return int(getattr(result, "code", 0))


def _is_hold(result: Any) -> bool:
    if isinstance(result, DriftHold):
        return True
    return bool(getattr(result, "passed", True) is False)


class PlainAdapter(Adapter):
    def __init__(self, run_dir: Optional[str] = None, *, run_id: Optional[str] = None, pipeline: Optional[str] = None,
                 sink: Optional[Sink] = None, destination: Optional[Sink] = None):
        """`destination` is where the records go (SPEC §2.6): a LocalDestination file in `run_dir` by default.
        `sink` is the same parameter under its older name; pass one of the two."""
        if sink is not None and destination is not None:
            raise ValueError("pass a destination or a sink, not both")
        sink = sink if sink is not None else destination
        self.run_dir = run_dir or os.environ.get("AS_AGREED_LEDGER_DIR") or "."
        self.run_id = run_id or uuid.uuid4().hex
        self.pipeline = pipeline or "default"
        self.sink = sink if sink is not None else LocalDestination(os.path.join(self.run_dir, "ledger.jsonl"))
        self.binding = Binding("plain", _code_of, _is_hold, lambda s: s.reopen(), DriftHold)

    # hooks
    def context(self, step: str, args: tuple, kwargs: dict, *, agreed: Any, policy: Any,
                hold_to: Optional[str] = None) -> StepContext:
        return StepContext(self.run_id, step, self.invocation(step), 1, policy.role, "plain", args, dict(kwargs),
                           body=None, commit=None, policy=policy, as_agreed=agreed, history=self.history(),
                           sink=self.sink, pipeline=self.pipeline, hold_to=hold_to, parent=self.last_link())

    def rerun(self, ctx: StepContext, reason: List[str]) -> Any:
        return call_with_reason(ctx.body, ctx.args, ctx.kwargs, reason)

    def commit(self, ctx: StepContext, outputs: Any, key: str) -> Any:
        if ctx.commit is not None:
            ctx.commit(outputs, idempotency_key=key)
        return outputs

    def discard(self, ctx: StepContext, outputs: Any) -> Any:
        return None

    def record(self, record: dict) -> None:
        self.sink.append(record)

    def records(self, run_id: Optional[str] = None) -> List[dict]:
        rid = run_id or self.run_id
        return [r for r in self.sink.read() if r.get("run_id") == rid]

    def raise_hold(self, ctx: StepContext, hold: Any) -> Any:
        raise DriftHold(list(hold.lines), hold, ctx.outcome)

    def clock(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def memory_adapter(run_id: Optional[str] = None, pipeline: Optional[str] = None) -> PlainAdapter:
    """A plain adapter over an in-memory sink, for tests."""
    return PlainAdapter(run_id=run_id, pipeline=pipeline, sink=MemorySink())
