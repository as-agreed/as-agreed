"""The as-agreed record of SPEC §1: the commitment, its typed fields, the claims, the record registered once per run."""
from __future__ import annotations

import dataclasses
import datetime as _dt
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from . import ledger
from .errors import EmptyAsAgreed
from .serialise import canonical, digest, short

FORMAT = "as-agreed/1"
MODALITIES = ("MUST", "MUST_NOT", "SHOULD", "MAY")  # the ladder, highest first; the order is the decision procedure
BINDING = ("MUST", "MUST_NOT")
VERIFICATIONS = ("machine_checkable", "human_judged", "unverifiable")  # a criterion's ladder, highest first
BOUND_KINDS = {"max": "number", "min": "number", "in": "set", "not_in": "set", "until": "date", "from": "date"}
OPTIONAL_FIELDS = ("condition", "limit", "deadline", "acceptance_criteria")
NUMBER = re.compile(r"^[-+]?\d{1,3}(?:[ ,']\d{3})+(?:\.\d+)?$|^[-+]?\d+(?:\.\d+)?$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
ID_SHAPE = re.compile(r"^[\w.-]+$")
MARK_END = re.compile(r"^(.*?)\s*\[(?:!|must)\]\s*([.!?;:…]?)\s*$", re.I | re.S)
MARK_ANY = re.compile(r"\[(?:!|must)\]", re.I)


# An integer is compared exactly, as itself, never through a float: a task can state a bound such as 2^2017 (t08 of
# the trace bench, 2026-11-04), and float() raises OverflowError on it. Past MAX_INT_BITS an integer is not a number
# tier 1 compares (its decimal text alone nears the interpreter's limit on integer strings): a bound is refused by
# name at registration (R9) and a delivered value escalates (SPEC §1.2, draft 8).
MAX_INT_BITS = 4096
_MAX_INT_DIGITS = 1234  # the decimal digits of a 4096-bit integer, plus one: a longer string is refused unread


def number(value: Any) -> Optional[Union[int, float]]:
    """A finite number, or None. An integer stays an exact int (up to MAX_INT_BITS); a thousands separator is
    stripped; a bool, NaN, infinity, a decimal string beyond float range and a larger integer are not numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value) if value.bit_length() <= MAX_INT_BITS else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str) and NUMBER.match(value.strip()):
        text = re.sub(r"[ ,']", "", value.strip())
        if "." not in text:
            return number(int(text)) if len(text.lstrip("+-")) <= _MAX_INT_DIGITS else None
        f = float(text)
        return f if math.isfinite(f) else None
    return None


def too_large(value: Any) -> bool:
    """An integer, or an integer string, beyond the exact range tier 1 compares (MAX_INT_BITS)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value.bit_length() > MAX_INT_BITS
    if isinstance(value, str) and NUMBER.match(value.strip()):
        text = re.sub(r"[ ,']", "", value.strip()).lstrip("+-")
        return "." not in text and (len(text) > _MAX_INT_DIGITS or int(text).bit_length() > MAX_INT_BITS)
    return False


def parse_date(value: Any) -> Optional[_dt.date]:
    """An ISO date (a timestamp counts by its date), or None."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str) and ISO_DATE.match(value.strip()):
        try:
            return _dt.date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def bound_kind(bound: Mapping) -> Optional[str]:
    kinds = [k for k in bound if k in BOUND_KINDS]
    return kinds[0] if len(kinds) == 1 else None


def _bound(dim: str, value: Any) -> Any:
    """A limit entry: a typed bound with a decision procedure, or prose, which escalates (SPEC §1.2)."""
    if isinstance(value, str):
        text = " ".join(value.split())
        if not text:
            raise ValueError(f"limit {dim!r} is empty")
        return text
    if isinstance(value, (list, tuple, set, frozenset)):
        return {"in": sorted(str(x) for x in value)}
    if isinstance(value, Mapping):
        kind = bound_kind(value)
        extra = [k for k in value if k not in BOUND_KINDS and k != "unit"]
        if kind is None or extra:
            raise ValueError(f"limit {dim!r}: give one of {', '.join(BOUND_KINDS)}, with an optional unit")
        shape = BOUND_KINDS[kind]
        raw = value[kind]
        ok = (number(raw) is not None if shape == "number" else parse_date(raw) is not None if shape == "date"
              else isinstance(raw, (list, tuple, set, frozenset)))
        if not ok and shape == "number" and too_large(raw):
            bits = raw.bit_length() if isinstance(raw, int) else "more than " + str(MAX_INT_BITS)
            raise ValueError(f"limit {dim!r}: {kind} is an integer of {bits} bits, beyond the {MAX_INT_BITS} bits "
                             f"tier 1 compares exactly; state the bound in prose, or as a smaller number")
        if not ok:
            raise ValueError(f"limit {dim!r}: {kind} takes a {shape}, got {raw!r}")
        out = dict(value)
        if shape == "set":
            out[kind] = sorted(str(x) for x in raw)
        return out
    raise ValueError(f"limit {dim!r}: a bare value has no direction, so no decision procedure; "
                     f"give {{'max': {value!r}}}, a set of allowed values, or prose")


@dataclass
class Criterion:
    """How satisfaction is determined (SPEC §1.2). A machine_checkable criterion names the output key it reads."""
    text: str
    verification: str = "human_judged"
    field: Optional[str] = None
    equals: Any = True
    criterion_id: str = ""

    def __post_init__(self) -> None:
        self.text = " ".join(str(self.text).split())
        if not self.text:
            raise ValueError("an acceptance criterion needs its text")
        if self.verification not in VERIFICATIONS:
            raise ValueError(f"verification {self.verification!r} is not one of {', '.join(VERIFICATIONS)}")
        if self.verification == "machine_checkable" and not self.field:
            raise ValueError(f"criterion {self.text!r} is machine_checkable and names no output key: "
                             f"give field=, the key it reads, or flag it human_judged")
        self.criterion_id = str(self.criterion_id or short(digest("criterion", ledger.normalise(self.text))))
        if not ID_SHAPE.match(self.criterion_id):
            raise ValueError(f"criterion id {self.criterion_id!r}: letters, digits, dot, dash and underscore only")

    def record(self) -> dict:
        return {"criterion_id": self.criterion_id, "text": self.text, "verification": self.verification,
                "field": self.field, "equals": self.equals}


def _criterion(value: Any) -> Criterion:
    if isinstance(value, Criterion):
        return value
    if isinstance(value, Mapping):
        known = {f.name for f in dataclasses.fields(Criterion)}
        unknown = sorted(k for k in value if k not in known)
        if unknown:
            raise ValueError(f"criterion fields not known: {', '.join(unknown)}")
        return Criterion(**dict(value))
    return Criterion(str(value))


def _carried(value: Any) -> Optional[Dict[str, str]]:
    """`carried_by` as a mapping of field path to output key, or as the list a record holds."""
    if not value:
        return None
    if isinstance(value, Mapping):
        return {str(k): str(v) for k, v in value.items()}
    return {str(e["field"]): str(e["key"]) for e in value}


@dataclass
class Commitment:
    """One typed record of what was agreed (SPEC §1.2): the unit of comparison, in every one of the three states."""
    act: str
    obligor: str
    obligee: str
    modality: str
    condition: Any = None
    limit: Optional[Dict[str, Any]] = None
    deadline: Optional[str] = None
    acceptance_criteria: Sequence[Any] = ()
    purpose: Optional[str] = None
    commitment_id: Optional[str] = None
    parent_id: Optional[str] = None
    source: Any = None
    captured_at: Optional[str] = None
    version: int = 1
    carried_by: Optional[Dict[str, str]] = None

    def __post_init__(self) -> None:
        for name in ("act", "obligor", "obligee"):
            text = " ".join(str(getattr(self, name) or "").split())
            if not text:
                raise ValueError(f"a commitment needs {name}")
            setattr(self, name, text)
        if self.modality not in MODALITIES:
            raise ValueError(f"modality {self.modality!r} is not on the ladder {', '.join(MODALITIES)}")
        if self.condition is not None and not isinstance(self.condition, (str, Mapping)):
            raise ValueError("condition is a mapping of typed preconditions, or prose")
        self.condition = dict(self.condition) if isinstance(self.condition, Mapping) else self.condition
        self.limit = {str(k): _bound(str(k), v) for k, v in self.limit.items()} if self.limit else None
        if self.deadline is not None:
            d = parse_date(self.deadline)
            if d is None:
                raise ValueError(f"deadline {self.deadline!r} is not an ISO date")
            self.deadline = d.isoformat()
        self.acceptance_criteria = [_criterion(c) for c in self.acceptance_criteria]
        if isinstance(self.source, str):
            self.source = {"artifact": self.source, "offset": None}
        self.commitment_id = str(self.commitment_id or short(digest(
            "commitment", ledger.normalise(self.obligor), ledger.normalise(self.obligee),
            ledger.normalise(self.act), self.modality)))
        if not ID_SHAPE.match(self.commitment_id):
            raise ValueError(f"commitment id {self.commitment_id!r}: letters, digits, dot, dash and underscore only")
        self.version = int(self.version)
        self.carried_by = _carried(self.carried_by)
        unknown = [k for k in (self.carried_by or {}) if k not in self.field_paths()]
        if unknown:
            raise ValueError(f"commitment {self.commitment_id}: carried_by names fields it does not hold: "
                             f"{', '.join(unknown)}; it holds {', '.join(self.field_paths())}")

    @classmethod
    def from_mapping(cls, data: Mapping) -> "Commitment":
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = sorted(k for k in data if k not in known)
        if unknown:
            raise ValueError(f"commitment fields not known: {', '.join(unknown)}")
        return cls(**dict(data))

    @property
    def binding(self) -> bool:
        return self.modality in BINDING

    @property
    def subject(self) -> str:
        """Who owes what, in one line: every finding names it."""
        return f"{self.obligor} to {self.obligee}, {self.modality}: {self.act}"

    def criterion(self, criterion_id: str) -> Optional[Criterion]:
        return next((c for c in self.acceptance_criteria if c.criterion_id == criterion_id), None)

    def field_paths(self) -> List[str]:
        """Every declared field by its path; a mapping field has one path per entry."""
        out = ["obligor", "obligee", "act", "modality"]
        if isinstance(self.condition, dict):
            out += [f"condition.{k}" for k in self.condition]
        elif self.condition:
            out.append("condition")
        out += [f"limit.{d}" for d in (self.limit or {})]
        if self.deadline:
            out.append("deadline")
        out += [f"acceptance_criteria.{c.criterion_id}" for c in self.acceptance_criteria]
        return out

    def value_at(self, path: str) -> Any:
        head, _, tail = path.partition(".")
        if head == "limit":
            return (self.limit or {}).get(tail)
        if head == "condition":
            return self.condition.get(tail) if tail and isinstance(self.condition, dict) else self.condition
        if head == "acceptance_criteria":
            c = self.criterion(tail)
            return c.text if c else None
        return getattr(self, head, None)

    def undeclared(self) -> List[str]:
        """Optional fields declared at no point: uncheckable everywhere, which is itself the finding."""
        return [f for f in OPTIONAL_FIELDS if not getattr(self, f)]

    def fingerprint(self) -> str:
        return canonical({"commitment_id": self.commitment_id, "obligor": self.obligor, "obligee": self.obligee,
                          "act": self.act, "modality": self.modality, "condition": self.condition,
                          "limit": self.limit, "deadline": self.deadline, "purpose": self.purpose,
                          "acceptance_criteria": [c.record() for c in self.acceptance_criteria]})

    def record(self, as_agreed_id: str) -> dict:
        return {"kind": "commitment", "commitment_id": self.commitment_id, "parent_id": self.parent_id,
                "source": self.source, "captured_at": self.captured_at, "obligor": self.obligor,
                "obligee": self.obligee, "act": self.act, "modality": self.modality, "condition": self.condition,
                "limit": self.limit, "deadline": self.deadline,
                "acceptance_criteria": [c.record() for c in self.acceptance_criteria], "purpose": self.purpose,
                "version": self.version, "as_agreed": as_agreed_id,
                # a list, because a field path holds a dot and a record may travel in a step's outputs
                "carried_by": [{"field": f, "key": k} for f, k in (self.carried_by or {}).items()]}


@dataclass
class Claim:
    """A sentence a later step could rely on, with its four markers (SPEC §1.3)."""
    id: str
    text: str
    markers: Dict[str, Optional[str]]
    origin: str
    must: bool
    found_at: str
    version: str

    def record(self, as_agreed_id: str) -> dict:
        return {"kind": "claim", "id": self.id, "text": self.text, "markers": self.markers, "origin": self.origin,
                "must": self.must, "found_at": self.found_at, "version": self.version, "as_agreed": as_agreed_id}


@dataclass
class Cut:
    claims: List[Claim]
    warnings: List[dict]


def _marks_kept(line: str, min_words: int) -> int:
    """Marks on one line that sit in a sentence with at least `min_words` content words once the mark is taken out
    (`cut` counts them that way): a claim can carry them. A mark that opens its sentence follows the previous
    sentence's punctuation and is warned about as misplaced."""
    kept = 0
    for s in ledger.sentences(line):
        if len(ledger.content_words(MARK_ANY.sub(" ", s))) >= min_words:
            opening = MARK_ANY.match(s)
            kept += len(MARK_ANY.findall(s[opening.end():] if opening else s))
    return kept


def cut(text: str, *, found_at: str, version: str, min_words: int = 3) -> Cut:
    """Sentences into claims, with the must-travel mark rule of §1.3."""
    lines = text.splitlines()

    def line_of(fragment: str) -> int:
        piece = fragment.strip()[:30]
        for n, ln in enumerate(lines, 1):
            if piece and piece in ln:
                return n
        return 0

    out = Cut([], [])
    seen = set()
    # a mark that follows a sentence's final punctuation (or opens a line) belongs to no sentence: the splitter
    # would drop it or hand it to the next sentence, so it is caught here, line by line, before splitting
    for n, ln in enumerate(lines, 1):
        marks = 0
        for mk in MARK_ANY.finditer(ln):
            before = ln[:mk.start()].rstrip()
            if not before or before[-1] in ".!?;:…":
                out.warnings.append({"kind": "warning", "format": FORMAT, "line": n,
                                     "text": "must-travel mark not immediately before the final punctuation; nothing marked"})
            else:
                marks += 1
        # a marked sentence under min_words registers no claim, and the splitter drops a very short one before it
        # is seen below, so the marks are counted per line, as ledger.mark_warnings counts them
        for _ in range(marks - _marks_kept(ln, min_words)):
            out.warnings.append({"kind": "warning", "format": FORMAT, "line": n,
                                 "text": "must-travel mark registered nothing: the sentence has too few content words "
                                         f"to be a claim (min_words={min_words})"})
    for s in ledger.sentences(text):
        body = s
        must = False
        m = MARK_END.match(body)
        if m:
            must = True
            body = m.group(1) + m.group(2)
        elif MARK_ANY.search(body):
            if not MARK_ANY.match(body.lstrip()):  # a mark that opens the sentence was warned about above
                out.warnings.append({"kind": "warning", "format": FORMAT, "line": line_of(s),
                                     "text": "must-travel mark not immediately before the final punctuation; nothing marked"})
            body = MARK_ANY.sub(" ", body)
        tag = ledger.TAG.search(body)
        clean = " ".join(ledger.TAG.sub("", body).split())
        if not clean:
            continue
        cid = tag.group(1) if tag else ledger.claim_id(clean)
        if len(ledger.content_words(clean)) >= min_words and cid not in seen:
            seen.add(cid)
            mk = ledger.markers(clean)
            out.claims.append(Claim(cid, clean[:500], mk, ledger.origin_class(mk), must, found_at, version))
    return out


@dataclass
class AsAgreed:
    """The record registered once per run: the captured artifact, its commitments, and the claims found (SPEC §1.1)."""
    id: str
    digest: str
    major: int
    minor: int
    run_id: str
    pipeline: str
    intent_record: Dict[str, Any]
    proxy: bool
    commitments: List[Commitment]
    claims: List[Claim]
    ts: str
    min_words: int = 3
    drafter: Optional[Dict[str, str]] = None  # who drafted a proxy record: model, version, date, prompt_id (§1.1)
    adapter: Any = dataclasses.field(default=None, repr=False, compare=False)

    @property
    def version(self) -> str:
        return str(self.major) if self.minor == 0 else f"{self.major}.{self.minor}"

    @property
    def order(self) -> str:
        return self.intent_record["text"]

    def commitment(self, commitment_id: str) -> Optional[Commitment]:
        return next((c for c in self.commitments if c.commitment_id == commitment_id), None)

    def claim(self, claim_id: str) -> Optional[Claim]:
        return next((c for c in self.claims if c.id == claim_id), None)

    def add_found(self, claims: Iterable[Claim]) -> List[Claim]:
        """Claims introduced by a link that is not discarded; a new minor version when any is new."""
        known = {c.id for c in self.claims}
        new = [c for c in claims if c.id not in known]
        if new:
            self.minor += 1
            for c in new:
                c.version = self.version
                known.add(c.id)
            self.claims.extend(new)
        return new

    def record(self) -> dict:
        return {"kind": "as_agreed", "format": FORMAT, "pipeline": self.pipeline, "run_id": self.run_id,
                "id": self.id, "digest": self.digest, "version": self.version, "intent_record": self.intent_record,
                "proxy": self.proxy, "commitments": [c.commitment_id for c in self.commitments],
                "claims": [c.id for c in self.claims], "min_words": self.min_words, "ts": self.ts,
                **({"drafter": dict(self.drafter)} if self.drafter else {})}


DRAFTER_KEYS = ("model", "version", "date", "prompt_id")


def check_drafter(drafter: Any, proxy: bool) -> Optional[Dict[str, str]]:
    """The provenance of a record a model drafted (SPEC §1.1, draft 8): the four keys, each a non-empty string, the
    date an ISO date, and only on a proxy record, because a drafted record is never an order as agreed."""
    if drafter is None:
        return None
    if not isinstance(drafter, Mapping):
        raise ValueError(f"drafter is a mapping of {', '.join(DRAFTER_KEYS)}, not {type(drafter).__name__}")
    unknown = sorted(str(k) for k in drafter if k not in DRAFTER_KEYS)
    missing = [k for k in DRAFTER_KEYS if k not in drafter]
    if unknown or missing:
        raise ValueError("drafter takes exactly " + ", ".join(DRAFTER_KEYS)
                         + (f"; not known: {', '.join(unknown)}" if unknown else "")
                         + (f"; missing: {', '.join(missing)}" if missing else ""))
    out = {k: " ".join(str(drafter[k]).split()) if isinstance(drafter[k], str) else drafter[k] for k in DRAFTER_KEYS}
    bad = [k for k, v in out.items() if not isinstance(v, str) or not v]
    if bad:
        raise ValueError(f"drafter {', '.join(bad)}: a non-empty string each")
    if parse_date(out["date"]) is None:
        raise ValueError(f"drafter date {out['date']!r} is not an ISO date")
    if not proxy:
        raise ValueError("a drafted record is a proxy: pass proxy=True with drafter=, because a model's draft is not "
                         "the order as agreed")
    return out


def _offset(order: str, act: str) -> Optional[int]:
    at = order.lower().find(act.lower())
    return at if at >= 0 else None


def build(order: str, commitments: Iterable[Any], *, claims: Iterable[str] = (), source: Optional[str] = None,
          version: int = 1, proxy: bool = False, pipeline: Optional[str] = None, run_id: str, ts: str,
          min_words: int = 3, drafter: Optional[Mapping[str, Any]] = None) -> Tuple[AsAgreed, List[dict]]:
    """Build the as-agreed record; the caller writes it through an adapter (SPEC §2.1)."""
    artifact = source or "order"
    drafted = check_drafter(drafter, bool(proxy))
    cs: List[Commitment] = []
    for c in commitments:
        c = c if isinstance(c, Commitment) else Commitment.from_mapping(c)
        cs.append(dataclasses.replace(c, source=c.source or {"artifact": artifact, "offset": _offset(order, c.act)},
                                      captured_at=c.captured_at or ts))
    ids = [c.commitment_id for c in cs]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate commitment ids: {', '.join(dup)}")
    found = cut(order, found_at="order#1@1", version=str(version), min_words=min_words)
    order_claims = list(found.claims)
    for extra in claims:
        order_claims.extend(cut(str(extra), found_at="order#1@1", version=str(version), min_words=min_words).claims)
    if not cs and not order_claims:
        raise EmptyAsAgreed("an as-agreed record needs at least one commitment or one claim; the order yielded none")
    d = digest("as_agreed", ledger.normalise(order), *[c.fingerprint() for c in cs], str(int(version)))
    record = {"text": order, "source": {"artifact": artifact, "offset": 0}, "captured_at": ts}
    agreed = AsAgreed(short(d), d, int(version), 0, run_id, pipeline or "default", record, bool(proxy), cs, order_claims,
                      ts, min_words, drafted)
    return agreed, found.warnings
