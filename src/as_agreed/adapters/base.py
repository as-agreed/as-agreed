"""The adapter contract (SPEC §5.1, §5.2, §5.4): what the adapter gives the core, what the core gives back."""
from __future__ import annotations

import abc
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class StepContext:
    run_id: str
    step: str
    invocation: int
    attempt: int
    role: str
    kind: str                       # plain | node | activity | workflow
    args: tuple
    kwargs: dict
    body: Callable
    commit: Optional[Callable]
    policy: Any
    as_agreed: Any
    history: Optional[Dict[str, str]]
    sink: Any
    pipeline: str = "default"
    hold_to: Optional[str] = None
    parent: Optional[str] = None
    outcome: Any = None


@dataclass
class Outcome:
    passed: bool
    code: int
    outputs: Any
    verdicts: List[dict]
    hold: List[str]
    attempts: int
    idempotency_key: str
    records: List[dict] = field(default_factory=list)
    link: str = ""


@dataclass
class Binding:
    """How the suite's abstract outcomes appear on this engine (SPEC §5.4)."""
    name: str
    code_of: Callable[[Any], int]
    is_hold: Callable[[Any], bool]
    reopen: Callable[[Any], Any]
    hold_error: Optional[type] = None
    # what a passing step's result holds as the step's outputs: the result itself on plain Python, the
    # `Outcome.outputs` on Temporal; a shared sentence reads a returned value through this, never directly
    outputs_of: Callable[[Any], Any] = lambda result: result


def encode(record: dict) -> bytes:
    """A record as its stored line: the one serialisation every destination receives (SPEC §2.6)."""
    return (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")


class Sink(abc.ABC):
    @abc.abstractmethod
    def append(self, record: dict) -> None: ...

    @abc.abstractmethod
    def read(self) -> List[dict]: ...

    @abc.abstractmethod
    def reopen(self) -> "Sink": ...


class JsonlSink(Sink):
    """One JSON object per line in a file; durable across a process restart."""

    def __init__(self, path: str):
        self.path = path

    def append(self, record: dict) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "ab") as f:
            f.write(encode(record))

    def read(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def reopen(self) -> "JsonlSink":
        return JsonlSink(self.path)


class MemorySink(Sink):
    """In memory, for tests; reopen() hands the same store to a new object."""

    def __init__(self, store: Optional[List[dict]] = None):
        self.store = store if store is not None else []

    def append(self, record: dict) -> None:
        self.store.append(json.loads(json.dumps(record, ensure_ascii=False)))

    def read(self) -> List[dict]:
        return list(self.store)

    def reopen(self) -> "MemorySink":
        return MemorySink(self.store)


class Adapter(abc.ABC):
    """An adapter missing a hook cannot be instantiated: the error names the missing hooks (A2)."""
    run_id: str = ""
    pipeline: str = "default"
    binding: Binding
    sink: Sink

    @abc.abstractmethod
    def context(self, step: str, args: tuple, kwargs: dict, *, agreed: Any, policy: Any,
                hold_to: Optional[str] = None) -> StepContext: ...

    @abc.abstractmethod
    def rerun(self, ctx: StepContext, reason: List[str]) -> Any: ...

    @abc.abstractmethod
    def commit(self, ctx: StepContext, outputs: Any, key: str) -> Any: ...

    @abc.abstractmethod
    def discard(self, ctx: StepContext, outputs: Any) -> Any: ...

    @abc.abstractmethod
    def record(self, record: dict) -> None: ...

    @abc.abstractmethod
    def records(self, run_id: Optional[str] = None) -> List[dict]: ...

    @abc.abstractmethod
    def raise_hold(self, ctx: StepContext, hold: Any) -> Any: ...

    @abc.abstractmethod
    def clock(self) -> str: ...

    def history(self, run_id: Optional[str] = None) -> Optional[Dict[str, str]]:
        """unit/field -> the class or status at the latest earlier link that decided it; None when unavailable."""
        out: Dict[str, str] = {}
        for r in self.records(run_id):
            if r.get("kind") == "verdict":
                label = r.get("drift_class") or r.get("status")
                if label:
                    out[f"{r['unit']}/{r['field']}"] = label
        return out

    def invocation(self, step: str, run_id: Optional[str] = None) -> int:
        n = 0
        for r in self.records(run_id):
            if r.get("kind") == "link" and r.get("step") == step and r.get("attempt") == 1:
                n += 1
        return n + 1

    def first_changed(self, run_id: Optional[str] = None) -> Dict[str, Optional[str]]:
        """unit/field -> the link_type of the earliest link where the field was decided as changed (SPEC §1.5, D-25).
        A key that is present with None says the field first changed at an untyped link."""
        types: Dict[str, Optional[str]] = {}
        out: Dict[str, Optional[str]] = {}
        for r in self.records(run_id):
            if r.get("kind") == "link":
                types[r.get("id")] = r.get("link_type")
            elif r.get("kind") == "verdict" and r.get("unit_kind") == "commitment":
                cls = r.get("drift_class")
                if cls and cls not in ("PRESERVED", "RESTATED"):
                    out.setdefault(f"{r['unit']}/{r['field']}", types.get(r.get("link")))
        return out

    def last_link(self, run_id: Optional[str] = None) -> Optional[str]:
        last = None
        for r in self.records(run_id):
            if r.get("kind") == "link":
                last = r.get("id")
        return last


_STAMPS = ("kind", "as_agreed", "format", "pipeline", "run_id")


def rebuild(records: List[dict], run_id: str, adapter: Any = None) -> Any:
    """The as-agreed record of a run, rebuilt from the records an engine carries (its state key, or the history
    argument of an activity): the latest registration, its commitment rows and the claims found so far. None when
    the run has no registration. The rows are what `register` wrote, so the guard of SPEC §2.2 compares like with like."""
    from ..commitment import AsAgreed, Claim, Commitment
    heads = [r for r in records if r.get("kind") == "as_agreed" and r.get("run_id") == run_id]
    if not heads:
        return None
    head = max(heads, key=lambda r: int(str(r.get("version", "1")).split(".")[0]))
    major = int(str(head.get("version", "1")).split(".")[0])
    commitments = [Commitment.from_mapping({k: v for k, v in r.items() if k not in _STAMPS})
                   for r in records if r.get("kind") == "commitment" and r.get("as_agreed") == head["id"]
                   and r.get("run_id") == run_id]
    claims: List[Any] = []
    seen = set()
    minor = 0
    for r in records:
        if r.get("kind") == "claim" and r.get("as_agreed") == head["id"] and r.get("run_id") == run_id \
                and r["id"] not in seen:
            seen.add(r["id"])
            claims.append(Claim(r["id"], r["text"], dict(r.get("markers") or {}), r["origin"], bool(r.get("must")),
                                r.get("found_at", "order#1@1"), str(r.get("version", major))))
            parts = str(r.get("version", major)).split(".")
            if len(parts) > 1 and int(parts[0]) == major:
                minor = max(minor, int(parts[1]))
    agreed = AsAgreed(head["id"], head["digest"], major, minor, run_id, head.get("pipeline", "default"),
                      dict(head["intent_record"]), bool(head.get("proxy")), commitments, claims, head.get("ts", ""),
                      int(head.get("min_words", 3)), head.get("drafter"))
    agreed.adapter = adapter
    return agreed
