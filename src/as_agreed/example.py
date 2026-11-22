"""`as-agreed example <name> <dir>`: copy an example the wheel carries into a new folder (0.1.2).

`plugin-hold` is the agent plugin's example: a confirmed record and its binding in `.as-agreed/`, and three planted
calls in `events/`. The hook run in the copy denies `events/dropped.json` and passes `events/faithful.json`.

The copy goes only into a folder that does not exist yet (its parent must) or that is empty, never into a link, a
folder others can write, or one that is not the person's own: the example holds a confirmed record, and it is never
written over a person's own `.as-agreed/`. Each folder and file of the copy is made new, so nothing is written through
a link planted in the folder while it copies. A folder that already holds exactly the example, and nothing else, is
left as it is. Stdlib only; nothing here opens a network connection.
"""
from __future__ import annotations

import glob
import os
import shlex
import stat
from typing import List, Optional, Tuple

from .locate import _shared, _yours

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = ("plugin-hold",)
PATTERNS = (".as-agreed/*.json", "events/*.json")  # the package-data patterns of pyproject.toml, under examples/<name>/
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # a FIFO planted at a file's name never holds the command up
_MODE = 0o755  # the copy's folders: never writable by the group or others, whatever the umask
_AT = os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd


def source(name: str) -> str:
    """The example's folder inside the installed package (never the clone's `examples/`)."""
    return os.path.join(HERE, "examples", name)


def files(name: str) -> List[str]:
    """The example's files, relative, with "/" between parts: its record and binding, then its events."""
    src = source(name)
    return sorted(os.path.relpath(p, src).replace(os.sep, "/") for pattern in PATTERNS
                  for p in glob.glob(os.path.join(src, *pattern.split("/"))) if os.path.isfile(p))


def _entries(folder: str) -> List[str]:
    return os.listdir(folder)


def _read(path: str) -> Optional[bytes]:
    """A regular file's bytes, never through a link or from a FIFO; None for anything else."""
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read() if stat.S_ISREG(os.fstat(f.fileno()).st_mode) else None


def _holds(dest: str, name: str, wanted: List[str]) -> bool:
    """`dest` holds the example exactly: its folders and files, byte for byte, no link and nothing else."""
    found, folders = [], {os.path.dirname(f) for f in wanted}
    for d, dirs, names in os.walk(dest):
        for n in dirs:
            rel = os.path.relpath(os.path.join(d, n), dest).replace(os.sep, "/")
            if os.path.islink(os.path.join(d, n)) or rel not in folders:
                return False
        found += [os.path.relpath(os.path.join(d, n), dest).replace(os.sep, "/") for n in names]
    if sorted(found) != wanted:
        return False
    return all(_read(os.path.join(dest, *f.split("/"))) == _read(os.path.join(source(name), *f.split("/")))
               for f in wanted)


def _write(dest: str, checked: os.stat_result, name: str, wanted: List[str]) -> None:
    """Make each folder and file of the copy new (mkdir, O_CREAT | O_EXCL), each one opened from the folder made
    before it, so an existing name, a link included, stops the copy instead of being written through. `checked` is
    the folder as it was checked: one swapped in since is refused."""
    root = os.open(dest, os.O_RDONLY | _DIRECTORY | _NOFOLLOW) if _AT else None
    opened = {"": root}
    try:
        now = os.fstat(root) if _AT else os.lstat(dest)
        if (now.st_dev, now.st_ino) != (checked.st_dev, checked.st_ino):
            raise OSError(0, "the folder changed while copying", dest)
        for f in wanted:
            folder, base = os.path.split(f)
            if folder not in opened:
                if _AT:
                    os.mkdir(folder, _MODE, dir_fd=root)
                    opened[folder] = os.open(folder, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=root)
                else:
                    os.mkdir(os.path.join(dest, folder), _MODE)
                    opened[folder] = None
            with open(os.path.join(source(name), *f.split("/")), "rb") as src:
                data = src.read()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | getattr(os, "O_BINARY", 0)
            fd = (os.open(base, flags, 0o644, dir_fd=opened[folder]) if _AT
                  else os.open(os.path.join(dest, folder, base), flags, 0o644))
            with os.fdopen(fd, "wb") as out:
                out.write(data)
    finally:
        for fd in opened.values():
            if fd is not None:
                os.close(fd)


def copy(name: str, dest: str) -> Tuple[int, str]:
    """Copy example `name` into `dest`. Returns (0, what was done) or (2, why it was refused), as SPEC §6 codes a bad
    argument."""
    wanted = files(name) if name in EXAMPLES else []
    if not wanted:
        return 2, f"no example {name!r}; the examples are {', '.join(EXAMPLES)}"
    dest = os.path.abspath(dest)
    try:
        st = os.lstat(dest)
    except FileNotFoundError:
        st = None
    except OSError as e:
        return 2, f"{dest} cannot be read ({e.strerror or type(e).__name__})"
    made = st is None
    if made:
        if not os.path.isdir(os.path.dirname(dest)):
            return 2, f"the folder above {dest} is missing; make it first"
        try:
            os.mkdir(dest, _MODE)
            st = os.lstat(dest)
        except OSError as e:
            return 2, f"{dest} cannot be made ({e.strerror or type(e).__name__})"
    if stat.S_ISLNK(st.st_mode):
        return 2, f"{dest} is a link; give a new folder, not a link"
    elif not stat.S_ISDIR(st.st_mode):
        return 2, f"{dest} is not a folder"
    elif not _yours(st):
        return 2, f"{dest} is not yours"
    elif _shared(st):
        return 2, f"{dest} is a folder others can write"
    elif not made:
        try:
            entries = _entries(dest)
        except OSError as e:
            return 2, f"{dest} cannot be read ({e.strerror or type(e).__name__})"
        if entries:
            if _holds(dest, name, wanted):
                return 0, f"{dest} already holds the {name} example; nothing written"
            return 2, (f"{dest} is not empty; the example holds a confirmed record, so it is copied only into a new or "
                       f"empty folder")
    try:
        _write(dest, st, name, wanted)
    except OSError as e:
        return 2, f"the copy into {dest} stopped, part written: {e.strerror or type(e).__name__} ({e.filename or ''})"
    events = sum(1 for f in wanted if f.startswith("events/"))
    return 0, (f"copied the {name} example into {dest}: its confirmed record and binding in .as-agreed/, {events} "
               f"planted calls in events/\nnext: cd {shlex.quote(dest)} && CLAUDE_PROJECT_DIR=$PWD as-agreed hook "
               f"< events/dropped.json")
