"""The one serialiser of SPEC §1.8: a step's outputs become link text, a step's inputs become a hash.

Standard library only. The same expansion feeds both, so the same output gives the same link
text and the same hash on every adapter. Mappings, dataclasses and pydantic models are expanded;
any other object is refused by name, so a client or a connection in a step's inputs never has its
internals written to the ledger or hashed.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import decimal
import enum
import hashlib
import inspect
import json
import pathlib
import re
from typing import Any, Dict, List, Set, Tuple

RESERVED = "as_agreed"  # an engine's reserved state key; never part of inputs, text or hashes
_ADDRESS = re.compile(r"^<[^<>\n]* at 0x[0-9a-fA-F]+>$")  # an object's default text, whole; a quoted traceback is text


def to_jsonable(value: Any, path: str = "", _seen: Set[int] = None) -> Any:
    """Expand mappings, dataclasses and pydantic models; refuse what cannot be text, naming the key."""
    where = path or "top level"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if _ADDRESS.match(value.strip()):
            raise ValueError(f"string at {where} is an object's default text ({value[:40]!r}); give the value, not the object")
        return value
    if inspect.iscoroutine(value):
        raise ValueError(f"a coroutine reached the serialiser at {where}; the async wrapper awaits the body first")
    if isinstance(value, enum.Enum):
        return to_jsonable(value.value, path, _seen)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, pathlib.PurePath):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        raise ValueError(f"bytes at {where} cannot be serialised as text")
    seen = _seen if _seen is not None else set()
    if id(value) in seen:
        raise ValueError(f"value at {where} refers back to itself; cycles cannot be serialised")
    seen = seen | {id(value)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable({f.name: getattr(value, f.name) for f in dataclasses.fields(value)}, path, seen)
    dump = getattr(value, "model_dump", None)
    if callable(dump) and not isinstance(value, type):
        return to_jsonable(dump(), path, seen)
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            ks = str(k)
            if "." in ks:
                raise ValueError(f"key {ks!r} at {where} contains a dot; dots join nested keys")
            out[ks] = to_jsonable(v, f"{path}.{ks}" if path else ks, seen)
        return out
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v, f"{path}.{i}" if path else str(i), seen) for i, v in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        items = [to_jsonable(v, path, seen) for v in value]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
    raise ValueError(f"value of type {type(value).__name__} at {where} cannot be serialised; "
                     f"pass a string, a number, a mapping, a dataclass or a pydantic model")


def _scalar(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def leaves(value: Any) -> List[Tuple[str, str]]:
    """(key path, text) pairs: one per leaf, lists of scalars joined with '; ', lists of mappings indexed."""
    out: List[Tuple[str, str]] = []

    def walk(v: Any, p: str) -> None:
        if isinstance(v, dict):
            for k, vv in v.items():
                walk(vv, f"{p}.{k}" if p else k)
        elif isinstance(v, list):
            if all(not isinstance(x, (dict, list)) for x in v):
                out.append((p, "; ".join(_scalar(x).replace("\n", " ") for x in v)))
            else:
                for i, x in enumerate(v):
                    walk(x, f"{p}.{i}" if p else str(i))
        else:
            out.append((p, _scalar(v)))

    walk(to_jsonable(value), "")
    return out


def to_lines(outputs: Any) -> str:
    """The link text: a string as is; anything else as 'key: value' lines, one per source line of a value."""
    if isinstance(outputs, str):
        return outputs
    lines: List[str] = []
    for k, v in leaves(outputs):
        parts = [p.strip() for p in v.splitlines() if p.strip()] or [""]
        for p in parts:
            lines.append(f"{k}: {p}" if k else p)
    return "\n".join(lines)


def claim_text(outputs: Any) -> str:
    """The text claims are read from: a link's `commitments` records are typed fields, not sentences."""
    if isinstance(outputs, str):
        return outputs
    data = to_jsonable(outputs)
    if isinstance(data, dict) and "commitments" in data:
        data = {k: v for k, v in data.items() if k != "commitments"}
    return to_lines(data)


def strip_reserved(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k != RESERVED}
    return value


def canonical(value: Any) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inputs_hash(args: tuple, kwargs: dict,
                exclude: Tuple[str, ...] = ("reason", "agreed", "history", RESERVED)) -> str:
    payload = {"args": [strip_reserved(a) for a in args],
               "kwargs": {k: strip_reserved(v) for k, v in kwargs.items() if k not in exclude}}
    return sha256(canonical(payload))


def digest(*parts: str) -> str:
    return sha256("|".join(parts))


def short(d: str) -> str:
    return d[:12]
