"""The reason channel: a context variable set for the body's duration (SPEC §3, step 3)."""
from __future__ import annotations

import contextvars
import inspect
from typing import Any, Callable, List, Optional

_REASON: contextvars.ContextVar = contextvars.ContextVar("as_agreed_reason", default=None)
_CURRENT: contextvars.ContextVar = contextvars.ContextVar("as_agreed_current_record", default=None)


def current_reason() -> Optional[List[str]]:
    """The DANGER lines of the attempt being retried, or None on a first attempt."""
    return _REASON.get()


def set_reason(reason: Optional[List[str]]):
    """Hold the reason for a scope the caller controls: the async path sets it around the await, because a coroutine
    built inside `call_with_reason` would otherwise run after that function has already reset the variable."""
    return _REASON.set(reason)


def reset_reason(token) -> None:
    _REASON.reset(token)


def current_as_agreed() -> Any:
    return _CURRENT.get()


def set_current_as_agreed(agreed: Any):
    return _CURRENT.set(agreed)


def reset_current_as_agreed(token) -> None:
    _CURRENT.reset(token)


def accepts_kw(fn: Callable, name: str) -> bool:
    """True when fn takes the keyword `name` explicitly or through **kwargs; False when it cannot be inspected."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if p.name == name and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            return True
    return False


def call_with_reason(fn: Callable, args: tuple, kwargs: dict, reason: Optional[List[str]]) -> Any:
    """Run the body once; the reason reaches it as reason= when it can take it, and through current_reason()."""
    token = _REASON.set(reason)
    try:
        kw = dict(kwargs)
        if reason is not None and accepts_kw(fn, "reason"):
            kw["reason"] = reason
        return fn(*args, **kw)
    finally:
        _REASON.reset(token)


async def acall_with_reason(fn: Callable, args: tuple, kwargs: dict, reason: Optional[List[str]]) -> Any:
    token = _REASON.set(reason)
    try:
        kw = dict(kwargs)
        if reason is not None and accepts_kw(fn, "reason"):
            kw["reason"] = reason
        return await fn(*args, **kw)
    finally:
        _REASON.reset(token)
