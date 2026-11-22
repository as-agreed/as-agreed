"""The band and the Board page: the run's flag above the prompt in Claude Code, and one local page for the task.

The plugin's hook writes each checked call's rows to `.as-agreed/runs/<session>/ledger.jsonl`, then rebuilds two
files beside it from the whole file: `band.json`, the labels the band module (`hooks/band/register.tsx`) shows, and
`board.html`, the page its "Board" button opens. The counts are `tally()` in `hold.py`, the same ones the flag line
states; nothing here counts again. The page is one file that loads nothing: no script, no image, no link out.

    python3 -m as_agreed band <ledger.jsonl> [--board <page.html>]

prints the band's state as JSON for any run's `ledger.jsonl` and, with `--board`, writes its page.
Stdlib only; nothing here opens a network connection or starts a program.
"""
from __future__ import annotations

import datetime
import errno
import html
import json
import os
import re
import stat
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .drift import MISSING, get_path
from .hold import flag, tally, term_name

FOLDER = ".as-agreed"


def read_rows(path: str, own: bool = False) -> List[Dict[str, Any]]:
    """The rows of a `ledger.jsonl`. `own`: only a regular file, never through a link (what the Board button reads)."""
    with (os.fdopen(_own_file(None, path, os.O_RDONLY), encoding="utf-8") if own else open(path, encoding="utf-8")) as f:
        return [json.loads(line) for line in f if line.strip()]


def state(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """What the band shows: its labels, the counts behind them, and one line under them."""
    t = tally(rows)
    n, kept, held, through, open_ = len(t.last), len(t.kept), len(t.held), len(t.through), len(t.open_)
    if not n:
        labels = ["No binding term checked yet"]
    else:
        labels = [f"Kept as agreed {kept} of {n}", f"held {held}", f"unchecked {open_}"]
        if through:  # a term lost that no hold stopped: shown only when there is one
            labels.append(f"changed and not held {through}")
    lost = t.held + t.through
    why = term_name(lost[0]) if lost else (f"{open_} term{'s' if open_ != 1 else ''} "
                                           f"wait{'' if open_ != 1 else 's'} for your read" if open_ else "")
    return {"labels": labels, "terms": n, "kept": kept, "held": held, "changed_not_held": through,
            "unchecked": open_, "why": why}


def labels(rows: Iterable[Mapping[str, Any]]) -> List[str]:
    return state(rows)["labels"]


def _number(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v)


def _agreed(value: Any) -> str:
    """A commitment's field as a person reads it: a bound in words, a text as it is."""
    if value is MISSING or value is None:
        return "not stated"
    if isinstance(value, Mapping) and any(k in value for k in ("max", "min", "in", "not_in", "until", "from")):
        unit = f" {value['unit']}" if value.get("unit") else ""
        parts = [f"{word} {_number(value[k])}{unit}" for k, word in (("min", "at least"), ("max", "at most"))
                 if k in value]
        parts += [f"{word} {value[k]}" for k, word in (("from", "from"), ("until", "until")) if k in value]
        parts += [f"{word} {', '.join(map(str, value[k]))}" for k, word in (("in", "one of"), ("not_in", "none of"))
                  if k in value]
        return ", ".join(parts)
    if isinstance(value, (Mapping, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _number(value)


def _arrived(r: Mapping[str, Any]) -> str:
    if "arrived" in r:
        v = r["arrived"]
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    if r.get("drift_class") == "DROPPED":
        return "nothing: the key is missing"
    return "not recorded"


CSS = """:root{--bg:#F4F2EC;--surface:#fff;--line:#E2DED5;--ink:#1A1A18;--muted:#6F6C64;--ok:#2D6A4D;--okbg:#E3EFE7;
--mv:#BF4A12;--mvbg:#FBE8DD;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#111110;--surface:#1B1B19;--line:#302F2C;--ink:#F0EEE8;--muted:#9A978F;
--ok:#6DBE93;--okbg:#17281E;--mv:#F08B57;--mvbg:#362218}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 var(--sans)}
main{max-width:1000px;margin:0 auto;padding:24px}h1{font-size:28px;letter-spacing:-.02em;margin:4px 0 12px}
.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0}
.seals{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 16px}
.seal{font-size:13px;padding:5px 10px;border-radius:99px;font-weight:600;background:var(--surface);
border:1px solid var(--line)}.seal.ok{color:var(--ok);background:var(--okbg)}.seal.lost{color:var(--mv);background:var(--mvbg)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:15px}th,td{text-align:left;padding:9px 10px;
border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;letter-spacing:.06em;text-transform:uppercase;
color:var(--muted)}td.m{font-family:var(--mono);font-size:13px}tr.lost td{background:var(--mvbg)}
tr.lost td.r{color:var(--mv);font-weight:600}.stake{font-size:17px;margin:16px 0 4px}
.small{font-size:13px;color:var(--muted);margin:10px 0 0}"""


def board_html(rows: Iterable[Mapping[str, Any]], title: str = "", now: Optional[str] = None) -> str:
    """The Board page for one task: each binding term, what was agreed, what arrived, the result; the lost marked."""
    rows = list(rows)
    t = tally(rows)
    commitments = {r.get("commitment_id"): r for r in rows if r.get("kind") == "commitment"}
    held = {id(r) for r in t.held}
    through = {id(r) for r in t.through}
    open_ = {id(r) for r in t.open_}
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    body = []
    for key, r in t.last.items():
        unit, field = key
        agreed = _agreed(get_path(commitments.get(unit) or {}, str(field)))
        cls, reason = r.get("drift_class") or r.get("undecided") or "", r.get("reason") or ""
        if id(r) in held:
            result = f"Lost, held: {cls}; the step was stopped. {reason}"
        elif id(r) in through:
            result = f"Lost, not held: {cls}; the step went on. {reason}"
        elif id(r) in open_:
            result = f"Unchecked: code could not decide; waits for your read. {reason}"
        elif key in t.saved:
            result = "Kept, after a hold stopped an earlier try"
        else:
            result = "Kept"
        mark = ' class="lost"' if id(r) in held or id(r) in through else ""
        body.append(f'<tr{mark}><td>{e(unit)} {e(field)}</td><td>{e(agreed)}</td>'
                    f'<td class="m">{e(_arrived(r))}</td><td class="r">{e(result.strip())}</td></tr>')
    s = state(rows)
    lost_n = s["held"] + s["changed_not_held"]
    if lost_n:
        names = ", ".join(f"{r['unit']} {r['field']}" for r in t.held + t.through)
        stake = f"{lost_n} term{'s' if lost_n != 1 else ''} lost in this task: {names}."
    elif s["unchecked"]:
        stake = f"No term was lost that code can show. {s['why'].capitalize()}."
    elif s["terms"]:
        stake = "Nothing was lost in this task."
    else:
        stake = "No binding term was checked in this task yet."
    seals = []
    for i, label in enumerate(s["labels"]):
        kind = "ok" if i == 0 else "lost" if (label.startswith("held") and s["held"]) or label.startswith("changed") \
            else ""
        seals.append(f'<span class="seal {kind}">{e(label)}</span>')
    when = now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    flag_lines = "".join(f"<br>{e(line)}" for line in flag(rows)) if s["terms"] else ""
    task = f"Task {e(title)}" if title else "This task"
    seal_row, table = "".join(seals), "\n".join(body)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>As-Agreed Board</title>
<style>
{CSS}
</style>
</head>
<body><main>
<p class="eyebrow">As-Agreed &middot; Board</p>
<h1>{task}: did what was agreed arrive?</h1>
<div class="seals">{seal_row}</div>
<div class="card">
<table><thead><tr><th>Term</th><th>Agreed</th><th>What arrived</th><th>Result</th></tr></thead>
<tbody>
{table}
</tbody></table>
<p class="stake">{e(stake)}</p>
</div>
<p class="small">The flag line:{flag_lines}</p>
<p class="small">Built {e(when)} from this task's ledger. This page is a file on your machine; it loads nothing and
sends nothing.</p>
</main></body>
</html>
"""


def run_folder(root: str, session: Any) -> str:
    """Where one task's ledger, band and page are kept: `.as-agreed/runs/<session>/` inside the project."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session or "")) or "session"
    return os.path.join(root, FOLDER, "runs", safe)


# The run folder sits in the project, where the agent can write, and the hook writes there as the person. So each
# folder of the run is opened without following a link and held open, and each file is made or opened relative to
# it, never through a link (security review of ASAG-23, findings 1 and 2). Where the system has no folder handles
# (Windows), each folder is checked with lstat instead. Folders are 0o700 and files 0o600: the carried values are the
# person's alone.
_AT = hasattr(os, "O_DIRECTORY") and {os.open, os.mkdir, os.rename, os.unlink} <= os.supports_dir_fd
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # a FIFO planted at a file's name never holds the hook up


def _in(where: Any, name: str) -> Tuple[str, Dict[str, int]]:
    """A name in a held folder (a handle) or in a checked one (a path), with the keyword the call needs."""
    return (name, {"dir_fd": where}) if _AT else (os.path.join(where, name), {})


def _own_dir(where: Any, name: str, make: bool) -> Any:
    """The folder `name` in `where`, made 0o700 when asked and not there yet; refused when it is a link or not a
    folder."""
    path, at = _in(where, name)
    if make:
        try:
            os.mkdir(path, 0o700, **at)
        except FileExistsError:
            pass
    if _AT:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, **at)
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        raise OSError(f"{path} is a link or not a folder")
    return path


def _own_file(where: Any, name: str, flags: int) -> int:
    """A file of the run, opened 0o600 without following a link, and only when it is a regular file."""
    path, at = _in(where, name) if where is not None else (name, {})
    fd = os.open(path, flags | _NOFOLLOW | _NONBLOCK, 0o600, **at)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{path} is not a regular file")
    return fd


class _Run:
    """The folders `<root>/.as-agreed/runs/<session>/`, each opened as the person's own, closed on leaving."""

    def __init__(self, root: str, session: Any, make: bool):
        self.root, self.session, self.make = root, session, make
        self.held: List[int] = []

    def __enter__(self) -> Any:
        root = self.root
        where: Any = root
        try:
            if _AT:
                where = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                self.held.append(where)
            for name, make in ((FOLDER, False), ("runs", self.make),
                               (os.path.basename(run_folder(root, self.session)), self.make)):
                where = _own_dir(where, name, make)
                if _AT:
                    self.held.append(where)
                if name == "runs" and self.make:
                    self._ignore(where)
        except BaseException:
            self.__exit__()
            raise
        return where

    def __exit__(self, *exc: Any) -> None:
        for fd in reversed(self.held):
            os.close(fd)

    @staticmethod
    def _ignore(runs: Any) -> None:
        """`runs/.gitignore`, made once, never through a link: the values of the calls stay out of commits."""
        path, at = _in(runs, ".gitignore")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, **at)
        except FileExistsError:
            return
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("*\n")


def calls(root: str, session: Any) -> int:
    """How many calls of this task were checked so far: one hold record each."""
    try:
        with _Run(root, session, make=False) as run, \
                os.fdopen(_own_file(run, "ledger.jsonl", os.O_RDONLY), encoding="utf-8") as f:
            return sum(1 for line in f if line.strip() and json.loads(line).get("kind") == "hold")
    except Exception:  # noqa: BLE001 (a ledger that cannot be read must not stop the check: the count starts over)
        return 0


def _replace(run: Any, name: str, text: str) -> None:
    """Written whole under a new name of its own, then moved in place: the band never reads half a file, and a link
    planted at either name is replaced, never written through."""
    tmp, at = _in(run, f".{name}.{os.urandom(8).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, **at)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        dst, _ = _in(run, name)
        os.replace(tmp, dst, **({"src_dir_fd": run, "dst_dir_fd": run} if _AT else {}))
    except BaseException:
        os.unlink(tmp, **at)
        raise


def write_run(root: str, session: Any, rows: Iterable[Mapping[str, Any]]) -> str:
    """Append one checked call's rows to the task's ledger, then rebuild `band.json` and `board.html` from it."""
    with _Run(root, session, make=True) as run:
        fd = _own_file(run, "ledger.jsonl", os.O_RDWR | os.O_APPEND | os.O_CREAT)
        with os.fdopen(fd, "r+", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            f.flush()
            f.seek(0)
            every = [json.loads(line) for line in f if line.strip()]
        _replace(run, "band.json", json.dumps(state(every), ensure_ascii=False) + "\n")
        _replace(run, "board.html", board_html(every, title=str(session or "")))
    return run_folder(root, session)


def board_temp(rows: Iterable[Mapping[str, Any]]) -> str:
    """The Board page in a new private file (0o600) of the system's temporary folder; its path. The Board button
    opens this, built at the click, never the run folder's `board.html`, which the agent can rewrite."""
    fd, path = tempfile.mkstemp(prefix="as-agreed-board-", suffix=".html")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(board_html(rows))
    return path


def main(path: str, board: Optional[str] = None, board_temp_: bool = False) -> int:
    """`board_temp_` (the button's own call): the ledger is read only as a regular file, never through a link, and the
    page goes to a new private file whose path the JSON gives as `board`."""
    try:
        rows = read_rows(path, own=board_temp_)
    except OSError as e:
        why = "a link, not a regular file" if e.errno == errno.ELOOP else (e.strerror or str(e))
        print(f"as-agreed band: {path} cannot be read: {why}", file=sys.stderr)
        return 2
    if board:
        with open(board, "w", encoding="utf-8") as f:
            f.write(board_html(rows))
    out = state(rows)
    if board_temp_:
        out["board"] = board_temp(rows)
    print(json.dumps(out, ensure_ascii=False))
    return 0
