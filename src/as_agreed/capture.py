"""Capture in one step (0.1.3): the person's words for each term, `as-agreed show`, and `as-agreed confirm`.

A commitment in `record.json` may carry `said`: for each term, by its path (`act`, `obligee`, `limit.amount`,
`deadline`, `acceptance_criteria.<id>`, ...), the person's words for it, copied exactly from the record's `order`:

    "said": {"act": "Pay the October invoice", "limit.amount": "up to 18,400 dollars"}

The check never reads `said`; the hook drops it before the record goes to the engine (`engine_view`).

`as-agreed show [folder]` prints each term with its value and its words, and marks, before anyone confirms:
- a quote that is not in the order ("not in the order");
- a value that does not match its words: a number, a date, a currency or a name token the words do not give
  ("does not match its words");
- a number or a date with no words for it ("no words").
A name with no words (an obligor the person did not say) is shown as "(no words)" and not marked.

`as-agreed confirm [folder]` is the person's one step. It shows the same lines on the terminal, reads the name from
`/dev/tty`, never from stdin, and writes into `record.json`: `confirmed_by`, `confirmed_at`, `confirmed_sha256` (the
record without these four fields) and `binding_sha256` (the binding), each hash over the file's JSON with sorted keys.
With no terminal, an empty name, or a file that changed while the person read it, it writes nothing.

After a confirm, the hook (`hook.py`) stops every call while either file no longer matches its hash, and stops an
agent's call that runs confirm. What it does not stop: a program that writes the record again with fresh hashes, or
with none (it then reads as a record confirmed by hand, as in 0.1.0 to 0.1.2), or that runs confirm on a terminal it
made itself. Stdlib only; nothing here opens a network connection.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

from .quantity import figures

CONFIRM_FIELDS = ("confirmed_by", "confirmed_at", "confirmed_sha256", "binding_sha256")
CAPTURE_KEYS = ("said",)
TTY = "/dev/tty"
NOT_IN_ORDER, MISMATCH, NO_WORDS = "not in the order", "does not match its words", "no words"

MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})")
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MON})\b\.?(?:,?\s+(\d{{4}}))?", re.I)
_MDY = re.compile(rf"\b({_MON})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)
CURRENCIES = {"USD": ("$", "usd", "dollar", "dollars"), "EUR": ("€", "eur", "euro", "euros"),
              "GBP": ("£", "gbp", "pound", "pounds", "sterling"), "JPY": ("¥", "jpy", "yen"),
              "CHF": ("chf", "franc", "francs"), "RUB": ("₽", "rub", "ruble", "rubles", "rouble", "roubles")}
NAME_FIELDS = ("obligor", "obligee")
TEXT_FIELDS = ("act", "condition")


class Mark(NamedTuple):
    unit: str
    path: str
    kind: str
    text: str


def engine_view(commitment: Any) -> Any:
    """A commitment as the engine reads it: without the keys only capture reads."""
    return {k: v for k, v in commitment.items() if k not in CAPTURE_KEYS} if isinstance(commitment, dict) else commitment


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def record_sha256(rec: Dict[str, Any]) -> str:
    return canonical_sha256({k: v for k, v in rec.items() if k not in CONFIRM_FIELDS})


def binding_sha256(binding: Any) -> str:
    return canonical_sha256(binding)


def _load(folder: str, name: str) -> Any:
    with open(os.path.join(folder, name), encoding="utf-8") as f:
        return json.load(f)


def stale(folder: str, rec: Any) -> Optional[str]:
    """After a confirm that wrote hashes: the file that no longer matches its hash ("record.json", "binding.json" or
    both), or None. A record with neither hash (confirmed by hand, 0.1.0 to 0.1.2) is never stale."""
    if not isinstance(rec, dict) or not (rec.get("confirmed_sha256") or rec.get("binding_sha256")):
        return None
    changed = [] if rec.get("confirmed_sha256") == record_sha256(rec) else ["record.json"]
    try:
        if rec.get("binding_sha256") != binding_sha256(_load(folder, "binding.json")):
            changed.append("binding.json")
    except (OSError, ValueError):
        changed.append("binding.json")
    return " and ".join(changed) or None


# The agent's own call that runs confirm, by the command's text: `as-agreed confirm`, `python3 -m as_agreed confirm`,
# a path to either, or code that loads the confirm. shortcut: the text only; a command built at run time (a variable,
# an encoded string) is not seen; that residual is named in the module's docstring and the README's limits.
_RUNS_CONFIRM = re.compile(r"(?:^|[^\w.-])(?:as-agreed|as_agreed(?:\.__main__)?)(?:\s+-\S+)*\s+confirm\b"
                           r"|as_agreed\.capture|from\s+as_agreed\s+import\s+capture")


def runs_confirm(tool_input: Any) -> bool:
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return isinstance(command, str) and bool(_RUNS_CONFIRM.search(command))


# --- the person's words and the marks

def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text)).split())


def _tokens(text: str) -> Set[str]:
    return {t.casefold() for t in re.findall(r"[^\W_]+", str(text))}


def _numbers(text: str) -> List[float]:
    return [float(f) for f in figures(str(text)) if isinstance(f, (int, float)) and not isinstance(f, bool)]


def _dates(text: str) -> List[Tuple[Optional[int], int, int]]:
    out = [(int(y), int(m), int(d)) for y, m, d in _ISO.findall(text)]
    out += [(int(y) if y else None, MONTHS[mo.lower()], int(d)) for d, mo, y in _DMY.findall(text)]
    out += [(int(y) if y else None, MONTHS[mo.lower()], int(d)) for mo, d, y in _MDY.findall(text)]
    return out


def _currencies(text: str) -> Set[str]:
    toks = _tokens(text)
    return {code for code, names in CURRENCIES.items() if any(n in text if not n.isalpha() else n in toks for n in names)}


def terms(c: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """Each term of a commitment by its path, with its value."""
    out = [(p, c.get(p)) for p in ("act", "obligor", "obligee", "modality")]
    cond = c.get("condition")
    if isinstance(cond, dict):
        out += [(f"condition.{k}", v) for k, v in cond.items()]
    elif cond:
        out.append(("condition", cond))
    out += [(f"limit.{k}", v) for k, v in (c.get("limit") or {}).items()]
    if c.get("deadline"):
        out.append(("deadline", c["deadline"]))
    for a in c.get("acceptance_criteria") or []:
        if isinstance(a, dict):
            out.append((f"acceptance_criteria.{a.get('criterion_id') or '?'}", a))
    return out


def _value_parts(path: str, value: Any) -> Tuple[List[float], List[Tuple[int, int, int]], Optional[str], Set[str]]:
    """What a value states that its words must give: numbers, dates, a currency, name tokens."""
    nums: List[float] = []
    dates: List[Tuple[int, int, int]] = []
    unit, names = None, set()
    root = path.split(".")[0]
    if isinstance(value, dict) and root == "limit":
        for k in ("max", "min"):
            if isinstance(value.get(k), (int, float)) and not isinstance(value.get(k), bool):
                nums.append(float(value[k]))
        for k in ("from", "until"):
            dates += [d for d in _dates(str(value.get(k) or "")) if d[0] is not None]
        for k in ("in", "not_in"):
            for v in value.get(k) or []:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    nums.append(float(v))
                elif isinstance(v, str):
                    iso = [d for d in _dates(v) if d[0] is not None]
                    dates += iso
                    names |= set() if iso else _tokens(v)
        unit = str(value["unit"]).upper() if str(value.get("unit") or "").upper() in CURRENCIES else None
    elif path == "deadline":
        dates += [d for d in _dates(str(value)) if d[0] is not None]
    elif root in NAME_FIELDS and isinstance(value, str):
        names = _tokens(value)
    elif root in TEXT_FIELDS and isinstance(value, str):
        words = re.findall(r"[^\W_]+", value)
        names = {w.casefold() for w in words[1:] if w[:1].isupper() and not w.isdigit()}
        nums = _numbers(value)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        nums.append(float(value))
    return nums, dates, unit, names


def _show_number(n: float) -> str:
    """A number as a person reads it; no separator below 10,000, so a year reads 2026, not 2,026."""
    if n.is_integer():
        return f"{int(n):,}" if abs(n) >= 10000 else str(int(n))
    return f"{n:,}" if abs(n) >= 10000 else str(n)


def _term_marks(unit: str, path: str, value: Any, words: Optional[str]) -> List[Mark]:
    if path.startswith("acceptance_criteria."):
        return []
    nums, dates, cur, names = _value_parts(path, value)
    out: List[Mark] = []
    if words is None:
        if nums or dates:
            out.append(Mark(unit, path, NO_WORDS, "a number or a date with no words for it"))
        return out
    wnums, wdates, wcur, wtok = _numbers(words), _dates(words), _currencies(words), _tokens(words)
    for n in nums:
        if not wnums:
            out.append(Mark(unit, path, NO_WORDS, f"its words give no number for {_show_number(n)}"))
        elif n not in wnums:
            out.append(Mark(unit, path, MISMATCH, f"{_show_number(n)} is not in its words "
                                                  f"(they give {', '.join(_show_number(w) for w in wnums)})"))
    for y, m, d in dates:
        if not wdates:
            out.append(Mark(unit, path, NO_WORDS, f"its words give no date for {y:04d}-{m:02d}-{d:02d}"))
        elif not any(wm == m and wd == d and wy in (None, y) for wy, wm, wd in wdates):
            out.append(Mark(unit, path, MISMATCH, f"{y:04d}-{m:02d}-{d:02d} is not the date in its words"))
    if cur and wcur and cur not in wcur:
        out.append(Mark(unit, path, MISMATCH, f"{cur} is not the currency in its words ({', '.join(sorted(wcur))})"))
    missing = sorted(names - wtok)
    if missing:
        out.append(Mark(unit, path, MISMATCH, f"{', '.join(missing)} not in its words"))
    return out


def _said(c: Dict[str, Any]) -> Tuple[Dict[str, str], List[Mark]]:
    said = c.get("said")
    unit = str(c.get("commitment_id") or "?")
    if said is None:
        return {}, []
    if not isinstance(said, dict) or not all(isinstance(v, str) for v in said.values()):
        return {}, [Mark(unit, "said", NOT_IN_ORDER, "said must map each term's path to the person's words")]
    return said, []


def marks(rec: Dict[str, Any]) -> List[Mark]:
    """Every mark of a record, commitment by commitment, term by term."""
    order = _norm(rec.get("order") or "")
    out: List[Mark] = []
    for c in rec.get("commitments") or []:
        if not isinstance(c, dict):
            continue
        unit = str(c.get("commitment_id") or "?")
        said, bad = _said(c)
        out += bad
        for path, value in terms(c):
            words = said.get(path)
            if words is not None and _norm(words) not in order:
                out.append(Mark(unit, path, NOT_IN_ORDER, "these words are not in the order"))
            out += _term_marks(unit, path, value, words)
    return out


def _value_text(path: str, value: Any) -> str:
    if isinstance(value, dict) and path.startswith("limit."):
        unit = f" {value['unit']}" if value.get("unit") else ""
        parts = [f"{w} {_show_number(float(value[k])) if isinstance(value[k], (int, float)) else value[k]}{unit}"
                 for k, w in (("min", "at least"), ("max", "at most")) if k in value]
        parts += [f"{w} {value[k]}" for k, w in (("from", "from"), ("until", "until")) if k in value]
        parts += [f"{w} {', '.join(map(str, value[k]))}" for k, w in (("in", "one of"), ("not_in", "none of"))
                  if k in value]
        return ", ".join(parts) or json.dumps(value, ensure_ascii=False)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else str(value)


def show_lines(rec: Dict[str, Any], where: str = "record.json") -> List[str]:
    """What `as-agreed show` prints: each commitment, each term with its value and its words, each mark under its term."""
    all_marks = marks(rec)
    by_term: Dict[Tuple[str, str], List[Mark]] = {}
    for m in all_marks:
        by_term.setdefault((m.unit, m.path), []).append(m)
    cs = [c for c in rec.get("commitments") or [] if isinstance(c, dict)]
    n_terms = sum(len(terms(c)) for c in cs)
    lines = [f"as-agreed show: {where}, {len(cs)} commitments, {n_terms} terms; "
             f"{len(all_marks)} mark{'s' if len(all_marks) != 1 else ''}",
             f'order: "{_norm(rec.get("order") or "")}"']
    for c in cs:
        unit = str(c.get("commitment_id") or "?")
        said, _ = _said(c)
        lines += ["", f"{unit}  {c.get('modality') or ''}"]
        for m in by_term.get((unit, "said"), []):
            lines.append(f"    MARK {m.kind}: {m.text}")
        for path, value in terms(c):
            if path in ("modality",):
                continue
            words = said.get(path)
            shown = f'"{_norm(words)}"' if words is not None else "(no words)"
            if path.startswith("acceptance_criteria.") and isinstance(value, dict):
                how = "judged by a person" if value.get("verification") != "machine_checkable" else "checked by code"
                lines.append(f"  {how}: {value.get('text', '')}  {shown}")
            else:
                lines.append(f"  {path:<14} {_value_text(path, value)}  {shown}")
            for m in by_term.get((unit, path), []):
                lines.append(f"    MARK {m.kind}: {m.text}")
    lines.append("")
    if str(rec.get("confirmed_by") or "").strip():
        lines.append(f"Confirmed by {rec['confirmed_by']} at {rec.get('confirmed_at') or 'an unstated time'}.")
    else:
        lines.append("Not confirmed. The person reads each term and mark, then runs `as-agreed confirm` in their own "
                     "terminal.")
    return lines


# --- the folder, show and confirm

def folder_of(folder: Optional[str]) -> str:
    """The folder that holds record.json and binding.json: the one given (or its `.as-agreed/`), else the one the hook
    would find from here."""
    if folder:
        for f in (folder, os.path.join(folder, ".as-agreed")):
            if os.path.isfile(os.path.join(f, "record.json")):
                return os.path.abspath(f)
        raise ValueError(f"{folder} holds no record.json, and no .as-agreed/record.json")
    from .locate import locate
    found = locate(os.getcwd())
    if found.error:
        raise ValueError(found.error)
    if found.how == "none" or not os.path.isfile(os.path.join(str(found.folder), "record.json")):
        raise ValueError("no record found here: give the folder that holds record.json")
    return str(found.folder)


def show(folder: Optional[str] = None) -> Tuple[int, str]:
    try:
        f = folder_of(folder)
        rec = _load(f, "record.json")
    except (OSError, ValueError) as e:
        return 2, f"show: {' '.join(str(e).split())}"
    if not isinstance(rec, dict):
        return 2, "show: record.json is not a JSON object"
    where = os.path.relpath(os.path.join(f, "record.json"))
    lines = show_lines(rec, where)
    gone = stale(f, rec)
    if gone:
        lines.append(f"{gone} changed after the person confirmed it: the hook stops every call until they confirm "
                     f"again.")
    return 0, "\n".join(lines)


def _confirmed(rec: Dict[str, Any], binding: Any, name: str, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    out = {k: v for k, v in rec.items() if k not in CONFIRM_FIELDS}
    out["confirmed_by"] = name
    out["confirmed_at"] = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="seconds")
    out["confirmed_sha256"] = record_sha256(out)
    out["binding_sha256"] = binding_sha256(binding)
    return out


def _write(folder: str, rec: Dict[str, Any]) -> None:
    """record.json replaced in one step: a new file beside it, then a rename (never written through a link)."""
    path = os.path.join(folder, "record.json")
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".record.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_confirm(folder: str, name: str, expect: Optional[Tuple[bytes, bytes]] = None) -> Dict[str, Any]:
    """Write the four fields for `name`. With `expect`, the two files' bytes as the person read them: a change since
    is refused (ValueError). Used by `confirm` after the terminal gave the name, and by the demo."""
    raw = (_read_bytes(folder, "record.json"), _read_bytes(folder, "binding.json"))
    if expect is not None and raw != expect:
        raise ValueError("the record or the binding changed while you read it; nothing was written. Run "
                         "`as-agreed confirm` again")
    rec, binding = json.loads(raw[0].decode("utf-8")), json.loads(raw[1].decode("utf-8"))
    if not isinstance(rec, dict):
        raise ValueError("record.json is not a JSON object")
    out = _confirmed(rec, binding, name)
    _write(folder, out)
    return out


def _read_bytes(folder: str, name: str) -> bytes:
    with open(os.path.join(folder, name), "rb") as f:
        return f.read()


def _read_line(fd: int, limit: int = 1024) -> str:
    """One line typed on the terminal, at most `limit` bytes."""
    buf = b""
    while len(buf) < limit:
        chunk = os.read(fd, 1)
        if not chunk or chunk in (b"\n", b"\r"):
            break
        buf += chunk
    return buf.decode("utf-8", "replace")


def _valid_name(name: str) -> Optional[str]:
    name = " ".join(name.split())
    if not name or len(name) > 200 or any(unicodedata.category(ch).startswith("C") for ch in name):
        return None
    return name


def confirm(folder: Optional[str] = None, tty: str = TTY) -> Tuple[int, str]:
    """The person's one step, in their own terminal. Never reads stdin."""
    try:
        f = folder_of(folder)
        raw = (_read_bytes(f, "record.json"), _read_bytes(f, "binding.json"))
        rec = json.loads(raw[0].decode("utf-8"))
        json.loads(raw[1].decode("utf-8"))
    except (OSError, ValueError) as e:
        return 2, f"confirm: {' '.join(str(e).split())}; nothing was written"
    if not isinstance(rec, dict):
        return 2, "confirm: record.json is not a JSON object; nothing was written"
    try:
        fd = os.open(tty, os.O_RDWR | getattr(os, "O_NOCTTY", 0))
    except OSError as e:
        return 2, (f"confirm: no terminal ({tty} cannot be opened: {e.strerror}). The person runs `as-agreed confirm` "
                   f"in their own terminal; nothing was written")
    try:
        lines = show_lines(rec, os.path.relpath(os.path.join(f, "record.json")))
        prompt = "\nType your name to confirm this record and its binding, or press Enter to stop: "
        os.write(fd, ("\n".join(lines) + "\n" + prompt).encode("utf-8"))
        name = _valid_name(_read_line(fd))
        if name is None:
            return 2, "confirm: no name given; nothing was written"
        try:
            out = write_confirm(f, name, expect=raw)
        except ValueError as e:
            return 2, f"confirm: {e}"
    finally:
        os.close(fd)
    return 0, (f"confirmed by {out['confirmed_by']} at {out['confirmed_at']}: record {out['confirmed_sha256'][:12]}, "
               f"binding {out['binding_sha256'][:12]}. The hook now checks against this version only.")
