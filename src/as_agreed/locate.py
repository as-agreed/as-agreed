"""Where the agreed record is: the lookup the plugin's hook runs before each call (0.1.1).

An agent that starts in a throwaway folder (a Multica task folder, a CI checkout, a cloud workdir) has no
`.as-agreed/` of its own. The hook looks, in this order:

1. `<start>/.as-agreed/`, when it holds `binding.json`: 0.1.0's layout, unchanged;
2. `AS_AGREED_RECORD`, the path of a folder holding `record.json` and `binding.json` (relative to the start folder
   when not absolute);
3. `.as-agreed-record`, a pointer file in the start folder or the nearest parent that has one, whose one line is that
   folder's path (relative to the pointer's own folder when not absolute).

`<start>` is `$CLAUDE_PROJECT_DIR`, else the current folder. A folder that 2 or 3 names and that is missing, cannot be
read, or is not the person's own is an error the hook denies with: the person asked for a check, so no call passes
unchecked. A pointer that is not the person's own (another user's, in a shared parent such as /tmp) is not their word
and is skipped. Stdlib only; nothing here opens a network connection.
"""
from __future__ import annotations

import os
import stat
from typing import List, Mapping, NamedTuple, Optional

FOLDER = ".as-agreed"
ENV = "AS_AGREED_RECORD"
POINTER = ".as-agreed-record"
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # a FIFO planted at the pointer's name never holds the hook up


class Located(NamedTuple):
    """`how` is "project", "env", "pointer" or "none"; `folder` holds `record.json` and `binding.json`; `error`, when
    set, is why the named folder cannot be used, and the hook denies with it."""
    how: str
    start: str
    folder: Optional[str] = None
    pointer: Optional[str] = None
    error: Optional[str] = None


def parents(start: str) -> List[str]:
    """The start folder and each of its parents, nearest first."""
    out = [os.path.abspath(start)]
    while os.path.dirname(out[-1]) != out[-1]:
        out.append(os.path.dirname(out[-1]))
    return out


def _yours(st: os.stat_result) -> bool:
    """Owned by the person the hook runs as, or by root; any owner where the system has no user ids (Windows)."""
    getuid = getattr(os, "getuid", None)
    return getuid is None or st.st_uid in (getuid(), 0)


def _folder(how: str, start: str, path: str, where: str, pointer: Optional[str] = None) -> Located:
    """The folder `path` names (relative to `where`), checked: there, readable and the person's own."""
    named = os.path.normpath(os.path.join(where, os.path.expanduser(path)))
    source = ENV if how == "env" else f"the pointer {pointer}"
    error = None
    try:
        st = os.stat(named)
        if not stat.S_ISDIR(st.st_mode):
            error = f"{source} names {named}, which is not a folder"
        elif not os.access(named, os.R_OK | os.X_OK):
            error = f"{source} names {named}, which cannot be read"
        elif not _yours(st):
            error = f"{source} names {named}, which is not yours"
    except OSError as e:
        error = f"{source} names {named}, which is missing ({e.strerror or type(e).__name__})"
    return Located(how, start, named, pointer, error)


def _read_pointer(path: str) -> Optional[str]:
    """The pointer's one line; None when the pointer is not the person's own. Raises ValueError when it cannot be
    read or does not hold exactly one line."""
    try:
        if not _yours(os.lstat(path)):
            return None
        fd = os.open(path, os.O_RDONLY | _NONBLOCK)
    except OSError as e:
        raise ValueError(f"the pointer {path} cannot be read ({e.strerror or type(e).__name__})")
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or not _yours(st):
        os.close(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"the pointer {path} is not a regular file")
        return None
    with os.fdopen(fd, "rb") as f:
        raw = f.read(4097)
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ValueError(f"the pointer {path} is not UTF-8 text")
    if len(raw) > 4096 or not text or "\n" in text or "\r" in text:
        raise ValueError(f"the pointer {path} must hold one line: the path of the folder with record.json and "
                         f"binding.json")
    return text


def locate(start: str, env: Optional[Mapping[str, str]] = None) -> Located:
    """The record folder for an agent started in `start`, by the order above."""
    env = os.environ if env is None else env
    start = os.path.abspath(start)
    if os.path.isfile(os.path.join(start, FOLDER, "binding.json")):
        return Located("project", start, os.path.join(start, FOLDER))
    if env.get(ENV):
        return _folder("env", start, env[ENV], start)
    for d in parents(start):
        pointer = os.path.join(d, POINTER)
        if not os.path.lexists(pointer):
            continue
        try:
            line = _read_pointer(pointer)
        except ValueError as e:
            return Located("pointer", start, None, pointer, str(e))
        if line is None:
            continue  # another user's file in a shared parent: not the person's word
        return _folder("pointer", start, line, d, pointer)
    return Located("none", start)
