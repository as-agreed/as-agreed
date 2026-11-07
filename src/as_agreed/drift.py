"""Tier 1 (SPEC §1.6): the decision procedure of each declared field of a commitment.

Every function compares a declared value with the as-agreed value and names the drift class, or says that the
field escalates. Nothing here reads prose: a text field is compared only for an exact or normalised match, and a
link that declares nothing leaves its fields UNDECLARED.
"""
from __future__ import annotations

import decimal
import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from . import ledger
from .commitment import (BOUND_KINDS, ID_SHAPE, MAX_INT_BITS, MODALITIES, VERIFICATIONS, Commitment, Criterion,
                         bound_kind, number, parse_date)
from .serialise import digest, short, to_jsonable

PRESERVED = "PRESERVED"
DROPPED = "DROPPED"
ADDED_UNPARENTED = "ADDED_UNPARENTED"
WIDENED = "WIDENED"
NARROWED = "NARROWED"
STRENGTHENED = "STRENGTHENED"
WEAKENED = "WEAKENED"
RECONDITIONED = "RECONDITIONED"
REASSIGNED = "REASSIGNED"
RESCHEDULED = "RESCHEDULED"
RESTATED = "RESTATED"
DRIFT_CLASSES = (PRESERVED, DROPPED, ADDED_UNPARENTED, WIDENED, NARROWED, STRENGTHENED, WEAKENED, RECONDITIONED,
                 REASSIGNED, RESCHEDULED, RESTATED)
TIER1_CLASSES = tuple(c for c in DRIFT_CLASSES if c != RESTATED)  # only tier 2 may assert the equivalence
FAITHFUL = (PRESERVED, RESTATED)
RESTORED_VERBATIM = "restored_verbatim"
RESTORED_EQUIVALENT = "restored_equivalent"  # a trajectory only a tier above tier 1 may assert
UNDECLARED = "UNDECLARED"  # nothing declared for the field at this link: uncheckable here, and not a drift class
ESCALATED = "ESCALATED"    # declared, and tier 1 cannot decide it: not a drift class either
# the type of a link, declared on the step (D-25), and the site it names when a field first changed there (SPEC §1.4)
LINK_TYPES = ("schema-write", "step", "compression", "delivery")
SITE_OF_LINK = {"schema-write": "design", "step": "source", "compression": "seam", "delivery": "sink"}
MISSING = object()
# how a structured link's missing carried key reads (SPEC §1.2, draft 8): "dropped", the default, makes the field
# DROPPED; "undeclared" leaves it UNDECLARED, for a link that addresses only some fields (a trace), and the field is
# DROPPED only where the key is present with no value, which is how such a link declares the field dropped
MISSING_KEY_MODES = ("dropped", "undeclared")


@dataclass
class Finding:
    field: str
    drift_class: Optional[str]
    reason: str
    agreed: Any = None
    declared: Any = None
    undecided: Optional[str] = None
    to_tier: int = 2
    conforming: bool = False  # PRESERVED because a delivered value sits inside its bound, not because it came back
    missing_key: Optional[str] = None  # UNDECLARED: this carried key is not in the link (on_missing_key="undeclared")
    refusal: Optional[str] = None      # ESCALATED with a named code: unit_mismatch (SPEC §1.2, §6, draft 9)
    unit_agreed: Optional[str] = None  # where the two sides name different units: the agreed one
    unit_read: Optional[str] = None    # ... and the one the link gave, declared or read
    unit_converted: bool = False       # the link's value was converted to the agreed unit by UNITS, then classed


def escalate(field: str, reason: str, agreed: Any, declared: Any, to_tier: int = 2) -> Finding:
    return Finding(field, None, reason, agreed, declared, ESCALATED, to_tier)


def undeclared(field: str, reason: str, agreed: Any) -> Finding:
    return Finding(field, None, reason, agreed, None, UNDECLARED)


def safe_id(value: Any) -> str:
    """A name written downstream, for the CSV: an id stays, and a sentence or a path becomes a hash of itself, so the
    files that travel hold labels only."""
    text = str(value)
    return text if ID_SHAPE.match(text) and len(text) <= 40 else short(digest("downstream", text))


def _absent(value: Any) -> bool:
    return value is MISSING or value is None or value == "" or value == [] or value == {}


def strict(value: Any) -> str:
    """The normalisation of the downward direction: case and whitespace only, and the final punctuation of a sentence.

    Every other character is kept, because the characters the claim ledger is right to strip are the ones that carry a
    bound's direction (`<`, `>`, `≤`), a currency (`$`, `€`) and the identity of a party or an account (`.`, `-`, `+`,
    `/`, `@`). `ledger.normalise` stays the ledger's own, where losing punctuation is intended (SPEC §1.7).
    """
    return _TRAILING.sub("", " ".join(str(value).casefold().split()))


_TRAILING = re.compile(r"[.!?;:,\s]+$")


def _text_equal(a: Any, b: Any) -> bool:
    return strict(a) == strict(b)


def _equal(a: Any, b: Any) -> bool:
    """Numbers numerically, but only where both sides are really numbers; everything else as strict text.

    A string that looks like a number is text: an account, a purchase order or an invoice reference is an identifier,
    not a quantity, and comparing it as a float would make two different accounts equal. A quantity declared as a
    number still matches the same quantity written as text, because `str(4471)` and `"4471"` are the same string.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if _is_number(a) and _is_number(b):
        return _same_number(a, b)
    return _text_equal(a, b)


def _same_number(a: Any, b: Any) -> bool:
    """Two real numbers, exactly: two integers as integers (2**60 and 2**60 + 1 are one float and two numbers), an
    integer beyond float range against anything by Python's exact comparison, and otherwise through float, so a
    `Decimal("100.00")` still equals 100.0."""
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    try:
        return float(a) == float(b)
    except OverflowError:
        return a == b


def _is_number(value: Any) -> bool:
    """A real, finite number object. A `Decimal` counts: it reaches a comparison as itself when a caller passes one
    directly, and as a string once `to_jsonable` has written it out."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):  # an int is finite; math.isfinite would raise OverflowError past float range
        return value.bit_length() <= MAX_INT_BITS
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, decimal.Decimal):
        return value.is_finite()
    return False


# a plain decimal spelling: no leading zero before a digit, no leading plus, nothing else in the string. "0012" and
# "+1" are not plain, so an account or a padded reference is never read as a quantity (SPEC §1)
_PLAIN_NUMBER = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")


def _canonical_number(value: Any) -> Optional[str]:
    """The token two numbers are compared by, or None when the value is not plainly a number. An integer, and a
    string that spells one, is its own exact digits; anything else goes through float, and a value float cannot hold
    is not plainly a number."""
    if isinstance(value, str):
        text = value.strip()
        if not _PLAIN_NUMBER.match(text):
            return None
        if "." not in text:
            n = number(text)
            return str(n) if n is not None else None
        value = float(text)
    if not _is_number(value):
        return None
    if isinstance(value, int):
        return str(int(value))
    try:
        f = float(value)
    except OverflowError:
        return None
    if not math.isfinite(f):
        return None
    return str(int(f)) if f.is_integer() else repr(f)


def _fmt(value: Any) -> str:
    n = number(value)
    if n is None:
        if isinstance(value, int) and not isinstance(value, bool):
            return f"an integer of {value.bit_length()} bits"
        text = str(value)
        return text if len(text) <= 80 else text[:77] + "..."
    try:
        return f"{n:.10g}"
    except OverflowError:  # an exact integer beyond float range: its ends and its length, so two neighbours differ
        digits = str(abs(int(n)))
        return f"{'-' if n < 0 else ''}{digits[:12]}...{digits[-12:]} ({len(digits)} digits)"


def describe(bound: Any) -> str:
    if not isinstance(bound, Mapping):
        return str(bound)
    kind = bound_kind(bound)
    if kind is None:  # a downstream bound of a shape no decision procedure covers
        return "a bound of no known shape (" + ", ".join(sorted(str(k) for k in bound)) + ")"
    unit = f" {bound['unit']}" if bound.get("unit") else ""
    words = {"max": "at most", "min": "at least", "until": "until", "from": "from", "in": "one of", "not_in": "none of"}
    raw = bound[kind]
    shown = ", ".join(str(x) for x in raw) if isinstance(raw, list) else _fmt(raw)
    return f"{words[kind]} {shown}{unit}"


TRUTHY = {"true", "yes", "y", "1", "on"}
FALSEY = {"false", "no", "n", "0", "off"}


def as_bool(value: Any) -> Optional[bool]:
    """True, False, or None when the value says neither. A flag crosses a JSON boundary as `true`, `1` or `"yes"`
    at least as often as it arrives as a Python bool, and a criterion that only accepts the bool would hold every
    commit of an ordinary pipeline."""
    if isinstance(value, bool):
        return value
    if _is_number(value):
        return True if value == 1 else False if value == 0 else None
    if isinstance(value, str):
        s = strict(value)
        return True if s in TRUTHY else False if s in FALSEY else None
    return None


# ---------- one field ----------
def party(field: str, agreed: str, value: Any) -> Finding:
    if _absent(value):
        return Finding(field, DROPPED, "present upstream, absent downstream", agreed)
    if _text_equal(agreed, value):
        return Finding(field, PRESERVED, "same party", agreed, value)
    return Finding(field, REASSIGNED, f"{value} where {agreed} was agreed", agreed, value)


def modality(agreed: str, value: Any) -> Finding:
    if _absent(value):
        return Finding("modality", DROPPED, "present upstream, absent downstream", agreed)
    v = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    if v == agreed:
        return Finding("modality", PRESERVED, "same rung", agreed, value)
    if v not in MODALITIES:
        return escalate("modality", f"{value} is not on the ladder", agreed, value)
    up = MODALITIES.index(v) < MODALITIES.index(agreed)
    note = "; the obligation is reversed" if {agreed, v} == {"MUST", "MUST_NOT"} else ""
    return Finding("modality", STRENGTHENED if up else WEAKENED, f"{v} where {agreed} was agreed{note}", agreed, v)


def text(field: str, agreed: str, value: Any) -> Finding:
    if _absent(value):
        return Finding(field, DROPPED, "present upstream, absent downstream", agreed)
    if _text_equal(agreed, value):
        return Finding(field, PRESERVED, "same words under normalisation", agreed, value)
    return escalate(field, "the wording differs; equivalence is not tier 1's to decide", agreed, value)


def deadline(agreed: Optional[str], value: Any) -> Finding:
    if _absent(value):
        return Finding("deadline", DROPPED, "present upstream, absent downstream", agreed)
    if agreed is None:
        return Finding("deadline", RESCHEDULED, f"a deadline of {value} where none was agreed", None, value)
    d = parse_date(value)
    if d is None:
        return escalate("deadline", f"{value} is not a date", agreed, value)
    a = parse_date(agreed)
    if d == a:
        return Finding("deadline", PRESERVED, "same date", agreed, value)
    return Finding("deadline", RESCHEDULED, f"{d.isoformat()} where {agreed} was agreed "
                   f"({'later' if d > a else 'earlier'})", agreed, value)


def condition_entry(field: str, agreed: Any, value: Any) -> Finding:
    """One typed precondition: added, removed or altered is RECONDITIONED."""
    if value is MISSING or value is None:
        return Finding(field, RECONDITIONED, f"the condition {agreed} was removed", agreed)
    if agreed is None:
        return Finding(field, RECONDITIONED, f"a condition {value} was added", None, value)
    if _equal(agreed, value):
        return Finding(field, PRESERVED, "same condition", agreed, value)
    return Finding(field, RECONDITIONED, f"{value} where {agreed} was agreed", agreed, value)


def condition_prose(agreed: str, value: Any) -> Finding:
    if value is MISSING or value is None or value == "":
        return Finding("condition", RECONDITIONED, "the condition was removed", agreed)
    if isinstance(value, str) and _text_equal(agreed, value):
        return Finding("condition", PRESERVED, "same words under normalisation", agreed, value)
    return escalate("condition", "a prose condition differs; tier 2 decides", agreed, value)


def _member(value: Any) -> str:
    """One member of a set, as the token two sides are compared by.

    A number is canonical, so 100, 100.0, `Decimal("100.00")` and the string "100.00" that `to_jsonable` writes for a
    Decimal are one member, and a payload that arrives from `json.load` as a float still matches. Only a plain
    decimal spelling counts as a number, so a padded account or SKU string such as "0012" stays a string and is
    never folded together with "12". Two numbers are then compared exactly, so two distinct references stay apart.
    """
    token = _canonical_number(value)
    return token if token is not None else strict(value)


def _values(raw: Any) -> Set[str]:
    """A set's members: an allowlist of addresses, territories or data classes keeps every character that tells two
    of them apart."""
    items: Iterable[Any] = raw if isinstance(raw, (list, tuple, set, frozenset)) else [raw]
    return {_member(x) for x in items}


def _comparable(shape: str, a: Any, b: Any) -> Optional[tuple]:
    pa, pb = (number(a), number(b)) if shape == "number" else (parse_date(a), parse_date(b))
    return None if pa is None or pb is None else (pa, pb)


def limit_entry(dim: str, bound: Any, value: Any) -> Finding:
    """A bound against a restated bound, or against a delivered value (SPEC §1.2, the table of bounds)."""
    field = f"limit.{dim}"
    if _absent(value):
        return Finding(field, DROPPED, "present upstream, absent downstream", bound)
    if isinstance(bound, str):
        if isinstance(value, str) and _text_equal(bound, value):
            return Finding(field, PRESERVED, "same words under normalisation", bound, value)
        return escalate(field, "a prose limit differs; tier 2 decides scope", bound, value)
    kind = bound_kind(bound)
    shape = BOUND_KINDS[kind]
    agreed = bound[kind]
    if isinstance(value, Mapping):
        vkind = bound_kind(value)
        if vkind != kind:
            return escalate(field, "a different kind of bound", bound, value)
        extra = sorted(k for k in value if k not in BOUND_KINDS and k != "unit")
        if extra:  # a cap restated "per line item" is another obligation, and no procedure here covers it
            return escalate(field, f"the downstream bound carries {', '.join(extra)}, which no decision procedure "
                                   f"covers", bound, value)
        if _unit(bound) != _unit(value):
            return _other_unit(field, kind, shape, bound, value)
        return _bound_against_bound(field, kind, shape, bound, value)
    return _value_against_bound(field, kind, shape, bound, agreed, value)


def _unit(bound: Mapping) -> Optional[str]:
    """A bound's unit, or None when it names none. The two must agree on naming one at all: a currency dropped
    downstream, or named under another key, is not a bound this tier can compare."""
    u = bound.get("unit")
    return None if u is None or str(u).strip() == "" else strict(u)


def _shown_unit(bound: Mapping) -> str:
    u = _unit(bound)
    return "no unit" if u is None else str(bound.get("unit"))


# ---------- units (SPEC §1.2, draft 9) ----------
UNIT_MISMATCH = "unit_mismatch"  # the refusal code a verdict carries where the units differ and no table converts them


def _units(dimension: str, base: str, rows: Mapping[str, str]) -> Dict[str, tuple]:
    """Each spelling of a unit -> (dimension, the canonical name, its size in the base unit, exactly)."""
    out: Dict[str, tuple] = {}
    for names, size in rows.items():
        spellings = names.split()
        for name in spellings:
            out[name] = (dimension, spellings[0], Fraction(size))
    return out


# The conversions the library ships, each exact by definition (the pound is 0.45359237 kg and the inch 0.0254 m
# since 1959). Mass, length and time only. A currency is never converted: a rate is a fact of a day and a source,
# not of the record. "ton", "month" and "year" are left out on purpose: each names more than one size.
UNITS: Dict[str, tuple] = {}
UNITS.update(_units("mass", "kg", {
    "mg milligram milligrams milligramme milligrammes": "0.000001",
    "g gram grams gramme grammes": "0.001",
    "kg kilogram kilograms kilogramme kilogrammes kilo kilos kgs": "1",
    "t tonne tonnes": "1000",
    "lb lbs pound pounds": "0.45359237",
    "oz ounce ounces": "0.028349523125"}))
UNITS.update(_units("length", "m", {
    "mm millimetre millimetres millimeter millimeters": "0.001",
    "cm centimetre centimetres centimeter centimeters": "0.01",
    "m metre metres meter meters": "1",
    "km kilometre kilometres kilometer kilometers": "1000",
    "in inch inches": "0.0254",
    "ft foot feet": "0.3048",
    "yd yard yards": "0.9144",
    "mi mile miles": "1609.344"}))
UNITS.update(_units("time", "s", {
    "ms millisecond milliseconds": "0.001",
    "s sec secs second seconds": "1",
    "min mins minute minutes": "60",
    "h hr hrs hour hours": "3600",
    "d day days": "86400",
    "wk week weeks": "604800"}))


def _exact(value: Any) -> Optional[Fraction]:
    """A number as an exact fraction: an int as itself, a float by its shortest decimal spelling, a string by number()."""
    n = number(value)
    if n is None:
        return None
    return Fraction(n) if isinstance(n, int) else Fraction(repr(n))


def _plain(q: Fraction) -> Any:
    """A converted number as tier 1 compares it: an exact int where it is whole, else a float."""
    return int(q) if q.denominator == 1 else float(q)


def _other_unit(field: str, kind: str, shape: str, bound: Mapping, value: Mapping) -> Finding:
    """The two sides name different units, or one names none. Where both are in UNITS and measure the same thing, the
    link's value is converted to the agreed unit and classed by the same table; otherwise the field is refused by
    name, UNIT_MISMATCH, with both units on the verdict. Never a guess: a currency, an unknown spelling, a date bound
    and a member that is not a number are all refused."""
    ua, ur = bound.get("unit"), value.get("unit")
    shown_a, shown_r = _shown_unit(bound), _shown_unit(value)
    names = {"unit_agreed": None if _unit(bound) is None else str(ua)[:40],
             "unit_read": None if _unit(value) is None else str(ur)[:40]}

    def refuse(why: str) -> Finding:
        f = escalate(field, f"{UNIT_MISMATCH}: {shown_r} where {shown_a} was agreed: {why}", bound, value)
        f.refusal = UNIT_MISMATCH
        f.unit_agreed, f.unit_read = names["unit_agreed"], names["unit_read"]
        return f

    a, r = UNITS.get(_unit(bound) or ""), UNITS.get(_unit(value) or "")
    if _unit(bound) is None or _unit(value) is None:
        return refuse("one side names no unit")
    if a is None or r is None:
        unknown = shown_a if a is None else shown_r
        return refuse(f"no table the library ships converts {unknown} (a currency is never converted)")
    if a[0] != r[0]:
        return refuse(f"{r[1]} measures {r[0]} and {a[1]} measures {a[0]}")
    if shape == "date":
        return refuse("a date bound has no unit to convert")
    factor = r[2] / a[2]
    raw = value[kind]
    items = list(raw) if isinstance(raw, (list, tuple, set, frozenset)) else [raw]
    exact = [_exact(x) for x in items]
    if any(q is None for q in exact):
        bad = next(x for x, q in zip(items, exact) if q is None)
        return refuse(f"{_fmt(bad)} is not a number the table converts")
    try:
        moved = [_plain(q * factor) for q in exact]
    except OverflowError:
        return refuse("the converted value is beyond the range tier 1 compares")
    converted = {kind: moved if shape == "set" else moved[0], "unit": ua}
    f = _bound_against_bound(field, kind, shape, bound, converted)
    f.reason = f"{describe(value)} converted to {describe(converted)}: {f.reason}"
    f.declared = value
    f.unit_agreed, f.unit_read, f.unit_converted = names["unit_agreed"], names["unit_read"], True
    return f


def _bound_against_bound(field: str, kind: str, shape: str, bound: Mapping, value: Mapping) -> Finding:
    was, now = describe(bound), describe(value)
    if shape == "set":
        a, b = _values(bound[kind]), _values(value[kind])
        grew, shrank = b - a, a - b
        if kind == "not_in":
            grew, shrank = shrank, grew  # fewer exclusions is a wider scope
        if not grew and not shrank:
            return Finding(field, PRESERVED, "same set", bound, value)
        if grew:
            return Finding(field, WIDENED, f"{now} where {was} was agreed", bound, value)
        return Finding(field, NARROWED, f"{now} where {was} was agreed", bound, value)
    pair = _comparable(shape, bound[kind], value[kind])
    if pair is None:
        return escalate(field, f"{_fmt(value[kind])} is not a {shape} tier 1 compares", bound, value)
    a, b = pair
    if a == b:  # exact: at a bound of 1e12 a relative tolerance would let a thousand units through silently
        return Finding(field, PRESERVED, "same bound", bound, value)
    wider = b > a if kind in ("max", "until") else b < a
    return Finding(field, WIDENED if wider else NARROWED, f"{now} where {was} was agreed", bound, value)


def _value_against_bound(field: str, kind: str, shape: str, bound: Mapping, agreed: Any, value: Any) -> Finding:
    """A delivered value inside the bound conforms; a value outside it is an enlargement nobody agreed to."""
    was = describe(bound)
    if shape == "set":
        a, v = _values(agreed), _values(value)
        outside = v - a if kind == "in" else v & a
        if outside:
            return Finding(field, WIDENED, f"{', '.join(sorted(outside))} where {was} was agreed", bound, value)
        # a member of the allowlist is one of the agreed values; staying out of an exclusion list is only conformance
        return Finding(field, PRESERVED, f"inside the bound: {was}", bound, value, conforming=(kind != "in"))
    pair = _comparable(shape, agreed, value)
    if pair is None:
        return escalate(field, f"{_fmt(value)} is not a {shape} tier 1 compares", bound, value)
    a, v = pair
    over = v > a if kind in ("max", "until") else v < a
    if over:
        return Finding(field, WIDENED, f"{_fmt(value)} where {was} was agreed", bound, value)
    # the agreed value itself coming back is a restoration; any other value inside the bound is only conformance
    return Finding(field, PRESERVED, f"inside the bound: {was}", bound, value, conforming=(v != a))


def criterion_value(field: str, c: Criterion, value: Any) -> Finding:
    """A machine_checkable criterion: its decision procedure is the key it reads and the value that satisfies it."""
    if _absent(value) and not isinstance(value, bool):
        return Finding(field, DROPPED, f"{c.field} is not in this link", c.text)
    if isinstance(c.equals, bool):  # the default shape of a criterion: a flag, however the payload spells it
        got = as_bool(value)
        if got is None:
            return escalate(field, f"{c.field} = {value!r} says neither true nor false", c.text, value)
        if got is c.equals:
            return Finding(field, PRESERVED, f"{c.field} = {value}", c.text, value)
        return Finding(field, WEAKENED, f"{c.field} = {value} where {c.equals} was agreed", c.text, value)
    if _equal(c.equals, value):
        return Finding(field, PRESERVED, f"{c.field} = {value}", c.text, value)
    return Finding(field, WEAKENED, f"{c.field} = {value} where {c.equals} was agreed", c.text, value)


def criterion_flag(field: str, c: Criterion, flag: Any) -> Finding:
    """The criterion's own ladder: down to unverifiable is the WEAKENED of the class table."""
    if flag not in VERIFICATIONS:
        return escalate(field, f"{flag} is not a verification flag", c.text, flag)
    if flag == c.verification:
        return Finding(field, PRESERVED, "same criterion", c.text, flag)
    down = VERIFICATIONS.index(flag) > VERIFICATIONS.index(c.verification)
    return Finding(field, WEAKENED if down else STRENGTHENED, f"{flag} where {c.verification} was agreed", c.text, flag)


# ---------- what a link declares ----------
def get_path(data: Mapping, path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return MISSING
        node = node[part]
    return node


class Declared:
    """What one link declares: its `commitments` records, the keys of a structured output, or nothing (prose)."""

    def __init__(self, outputs: Any, *, delivered: bool = False, on_missing_key: str = "dropped"):
        if on_missing_key not in MISSING_KEY_MODES:
            raise ValueError(f"on_missing_key is one of {', '.join(MISSING_KEY_MODES)}, not {on_missing_key!r}")
        self.delivered = delivered
        self.on_missing_key = on_missing_key
        self.fields: Optional[Dict[str, Any]] = None
        self.records: Optional[List[Dict[str, Any]]] = None
        if isinstance(outputs, str):
            return
        data = to_jsonable(outputs)
        if isinstance(data, dict):
            self.fields = data
            if "commitments" in data:
                recs = data["commitments"]
                if not isinstance(recs, list) or not all(isinstance(r, dict) for r in recs):
                    raise ValueError("`commitments` in a step's outputs must be a list of records (mappings)")
                self.records = recs

    def parent(self, record: Mapping, ids: Set[str]) -> Optional[str]:
        """The as-agreed commitment a record derives from, or None: it resolves to nothing.
        A record that carries an as-agreed id with a null parent is that commitment carried on.
        An id of a shape no record can hold (a list, a mapping) resolves to nothing rather than raising."""
        p = record.get("parent_id")
        if isinstance(p, str) and p in ids:
            return p
        own = record.get("commitment_id")
        if p is None and isinstance(own, str) and own in ids:
            return own
        return None

    def unparented(self, ids: Set[str]) -> List[Dict[str, Any]]:
        return [r for r in (self.records or []) if self.parent(r, ids) is None]


def read(c: Commitment, state: Declared, ids: Set[str]) -> List[Finding]:
    """Every declared field of one as-agreed commitment at one link. The reference is the as-agreed value, always."""
    if state.records is not None:
        kids = [r for r in state.records if state.parent(r, ids) == c.commitment_id]
        if not kids:
            # a link that declares records for other commitments still carries this one's keys, and reading the
            # payload is worth more than the blanket DROPPED the absence of a record would give every field
            read_here = _payload_only(c, state)
            decided = {f.field for f in read_here}
            return read_here + [Finding(p, DROPPED, "no record at this link derives from this commitment",
                                        c.value_at(p)) for p in c.field_paths() if p not in decided]
        out: List[Finding] = []
        for r in kids:
            out.extend(compare_record(c, r, state))
        out.extend(_carried_present(c, state))  # a faithful record beside a changed payload must not pass
        return out
    if state.fields is None:
        return [undeclared(p, "a prose link declares no fields", c.value_at(p)) for p in c.field_paths()]
    return _carried(c, state)


def compare_record(c: Commitment, r: Mapping, state: Declared) -> List[Finding]:
    """A downstream record against the as-agreed commitment, field by field."""
    out = [party("obligor", c.obligor, r.get("obligor", MISSING)), party("obligee", c.obligee, r.get("obligee", MISSING)),
           text("act", c.act, r.get("act", MISSING)), modality(c.modality, r.get("modality", MISSING))]
    out.extend(_condition(c.condition, r.get("condition")))
    out.extend(_limits(c.limit, r.get("limit")))
    if c.deadline or not _absent(r.get("deadline", MISSING)):
        out.append(deadline(c.deadline, r.get("deadline", MISSING)))
    down = _criteria_list(r.get("acceptance_criteria"))
    if down is None:
        shape = type(r.get("acceptance_criteria")).__name__
        why = f"the record's acceptance_criteria is a {shape}, not a list of criteria"
        if c.acceptance_criteria:
            out.extend(escalate(f"acceptance_criteria.{crit.criterion_id}", why, crit.text, None)
                       for crit in c.acceptance_criteria)
        else:  # nothing upstream to compare, but a malformed key is still worth seeing in the report
            out.append(escalate("acceptance_criteria", why, None, None))
    else:
        out.extend(_criteria(c.acceptance_criteria, down, state))
    if c.purpose and not _absent(r.get("purpose", MISSING)) and not _text_equal(c.purpose, r["purpose"]):
        out.append(escalate("purpose", "only the purpose differs in evidence; a person decides", c.purpose, r["purpose"], 3))
    return out


def _condition(agreed: Any, value: Any) -> List[Finding]:
    if isinstance(agreed, str):
        return [condition_prose(agreed, value)]
    if isinstance(value, str) and value:
        if agreed:
            return [escalate("condition", "a prose condition where a typed one was agreed", agreed, value)]
        return [Finding("condition", RECONDITIONED, "a prose condition was added", None, value)]
    a = agreed or {}
    v = value if isinstance(value, Mapping) else {}
    return [condition_entry(f"condition.{safe_id(k)}", a.get(k), v.get(k, MISSING)) for k in list(a) + [k for k in v if k not in a]]


def _limits(agreed: Optional[Mapping], value: Any) -> List[Finding]:
    if not agreed and _absent(value if value is not None else MISSING):
        return []
    a = agreed or {}
    if not isinstance(value, Mapping) and not _absent(value if value is not None else MISSING):
        return [escalate("limit", "a limit that is not a mapping of bounds", a, value)]
    v = value if isinstance(value, Mapping) else {}
    out = [limit_entry(d, b, v.get(d, MISSING)) for d, b in a.items()]
    for d in v:
        if d not in a:
            out.append(Finding(f"limit.{safe_id(d)}", NARROWED, f"a bound nobody agreed to: {describe(v[d])}", None, v[d]))
    return out


def _down_criterion(entry: Any) -> Dict[str, Any]:
    return dict(entry) if isinstance(entry, Mapping) else {"text": str(entry)}


def _criteria_list(value: Any) -> Optional[List[Any]]:
    """A downstream `acceptance_criteria`, or None when it is not a list of criteria. A string would otherwise be
    read one character at a time, and a number would raise."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [e for e in value]
    return None


def _criteria(agreed: Iterable[Criterion], value: Iterable[Any], state: Declared) -> List[Finding]:
    down = [_down_criterion(e) for e in value]
    used: Set[int] = set()
    out: List[Finding] = []
    lost: List[Criterion] = []
    for c in agreed:
        field = f"acceptance_criteria.{c.criterion_id}"
        if c.verification == "machine_checkable" and state.fields is not None:
            seen = get_path(state.fields, c.field or "")
            if seen is not MISSING or state.delivered:
                out.append(criterion_value(field, c, seen))
                for i, d in enumerate(down):  # the restated copy of an evaluated criterion is not an addition
                    if _same_criterion(c, d):
                        used.add(i)
                continue
        match = next((i for i, d in enumerate(down) if i not in used and _same_criterion(c, d)), None)
        if match is None:
            lost.append(c)
            continue
        used.add(match)
        out.append(criterion_flag(field, c, down[match].get("verification", c.verification)))
    spare = [d for i, d in enumerate(down) if i not in used]
    for c in lost:  # a reworded criterion and a dropped one look alike: only a tier above can tell them apart
        field = f"acceptance_criteria.{c.criterion_id}"
        if spare:
            out.append(escalate(field, "a criterion no longer matches and another stands beside it", c.text, None))
        else:
            out.append(Finding(field, DROPPED, "present upstream, absent downstream", c.text))
    for d in spare:
        field = f"acceptance_criteria.{safe_id(d.get('criterion_id') or ledger.claim_id(str(d.get('text', ''))))}"
        if lost:
            out.append(escalate(field, "a criterion with no match upstream, beside one that lost its match", None, d.get("text")))
        else:
            out.append(Finding(field, ADDED_UNPARENTED, "a criterion with nothing upstream", None, d.get("text")))
    return out


def _same_criterion(c: Criterion, d: Mapping) -> bool:
    if d.get("criterion_id") == c.criterion_id:
        return True
    return _text_equal(d.get("text", ""), c.text)


def _one_carried(c: Commitment, path: str, value: Any) -> Finding:
    head, _, tail = path.partition(".")
    if head in ("obligor", "obligee"):
        return party(path, getattr(c, head), value)
    if head == "modality":
        return modality(c.modality, value)
    if head == "deadline":
        return deadline(c.deadline, value)
    if head == "limit":
        return limit_entry(tail, (c.limit or {})[tail], value)
    if head == "condition" and tail:
        return condition_entry(path, c.value_at(path), value)
    if head == "condition":
        return condition_prose(str(c.condition), value)
    return text(path, str(c.value_at(path)), value)


def _carried(c: Commitment, state: Declared) -> List[Finding]:
    """A structured output with no `commitments` key: only the fields `carried_by` names are declared."""
    fields = state.fields or {}
    carried = c.carried_by or {}
    out: List[Finding] = []
    for path in c.field_paths():
        head, _, tail = path.partition(".")
        crit = c.criterion(tail) if head == "acceptance_criteria" else None
        if crit is not None and crit.verification == "machine_checkable":
            seen = get_path(fields, crit.field or "")
            out.append(_not_addressed(c, path, crit.field or "", seen, state)
                       or criterion_value(path, crit, seen))
        elif path in carried:
            seen = get_path(fields, carried[path])
            out.append(_not_addressed(c, path, carried[path], seen, state) or _one_carried(c, path, seen))
        else:
            out.append(undeclared(path, "no output key carries this field", c.value_at(path)))
    return out


def _carried_present(c: Commitment, state: Declared) -> List[Finding]:
    """Beside a record, every key `carried_by` names is read: the payload is what takes effect.

    A key that is not there is `DROPPED`, exactly as on the no-record path. Reading only the keys that happen to be
    present would mean that moving a number to another key removes its check instead of failing it, and the
    reflective retry would teach a model which key is watched.
    """
    fields = state.fields or {}
    also = {}
    for path, key in (c.carried_by or {}).items():
        crit = c.criterion(path.partition(".")[2]) if path.startswith("acceptance_criteria.") else None
        if crit is None or crit.verification != "machine_checkable":  # a criterion is read by its own key
            also[path] = (key, get_path(fields, key))
    return [_not_addressed(c, path, key, value, state) or _one_carried(c, path, value)
            for path, (key, value) in also.items()]


def _payload_only(c: Commitment, state: Declared) -> List[Finding]:
    """What a payload decides without any record of its own: a machine_checkable criterion by the key it names,
    and every key `carried_by` names."""
    fields = state.fields or {}
    out = []
    for crit in c.acceptance_criteria:
        if crit.verification == "machine_checkable":
            path, seen = f"acceptance_criteria.{crit.criterion_id}", get_path(fields, crit.field or "")
            out.append(_not_addressed(c, path, crit.field or "", seen, state) or criterion_value(path, crit, seen))
    return out + _carried_present(c, state)


def _not_addressed(c: Commitment, path: str, key: str, value: Any, state: Declared) -> Optional[Finding]:
    """Under on_missing_key="undeclared" a carried key the link does not hold leaves its field UNDECLARED (SPEC §1.2,
    draft 8).
    A key present with no value is the link declaring the field dropped: that is decided by the field's procedure,
    which makes it DROPPED. Under the default every missing key is DROPPED, as before."""
    if value is not MISSING or state.on_missing_key != "undeclared":
        return None
    f = undeclared(path, f"{key} is not in this link, and this step reads a missing key as not addressed",
                   c.value_at(path))
    f.missing_key = safe_id(key)
    return f


def decide_value(c: Commitment, path: str, value: Any) -> Finding:
    """Tier 1's decision procedure for one field of `c` on one delivered value, wherever the value came from: an output
    key, or a value reader's read of prose (SPEC §4, draft 8). None is the field declared dropped. A path the
    commitment does not hold has no procedure and escalates."""
    if path not in c.field_paths():
        return escalate(path, "no decision procedure for this field path", c.value_at(path), value)
    head, _, tail = path.partition(".")
    crit = c.criterion(tail) if head == "acceptance_criteria" else None
    if crit is not None and crit.verification == "machine_checkable":
        return criterion_value(path, crit, MISSING if value is None else value)
    return _one_carried(c, path, MISSING if value is None else value)
