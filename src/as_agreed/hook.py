"""`as-agreed hook`: the agent plugin's check before a step takes effect (0.1.0; the record's lookup 0.1.1).

Claude Code runs it before each tool call (PreToolUse) and passes the call as JSON on stdin. The record is two files
in one folder: `record.json`, the agreed record, and `binding.json`, which tool calls are steps and what each owes.
The folder is the project's `.as-agreed/`, or, for an agent started in a throwaway folder (a Multica task folder, CI,
a cloud workdir), the one `AS_AGREED_RECORD` or a `.as-agreed-record` pointer names (`locate.py`). A named folder that
is missing or cannot be read denies every call, with the reason. The two files:

    record.json   {"order": "...", "commitments": [...], "confirmed_by": "<who confirmed it>", "confirmed_at": "..."}
    binding.json  {"steps": [{"tool": "Write", "path": "*/outbox/*.json", "json": "content",
                              "binding": {"serves": ["pay"], "kind": "takes_effect", "author": "finance"}}],
                   "baseline_minutes": null}

`tool` matches the tool's name (shell-style patterns allowed); `path`, when given, matches the call's file path made
absolute and normalised; `json`, when given, names the input field that holds the step's output as a JSON string;
otherwise the tool's input is the output. A call no step names is not checked.

A step is denied when a term changed or a binding term is lost (decided by code), when the record is not confirmed,
or when the check cannot run; what code cannot decide passes. A file tool may not set `confirmed_by`: the person
writes it by hand. Once it is set, file tools may not change `record.json` or `binding.json`, and, for a record found
through the variable or a pointer, may not write a pointer in the start folder or a parent, or the start folder's
`.as-agreed/` files, which the lookup reads first. While either file is unreadable, file tools may still repair it.
Each checked call is logged to `hook.jsonl` in the record's folder by tool, decision and the unit, field and class of
what was held; never a value of the call. The check's rows, values included, go to `runs/<session>/ledger.jsonl`
beside the record, with the band's `band.json` and the Board page `board.html` (`band.py`); `runs/` holds a
`.gitignore`, so they stay out of commits, and nothing in it is sent anywhere. Nothing here opens a network connection.
"""
from __future__ import annotations

import datetime
import fnmatch
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from .locate import ENV, FOLDER, POINTER, Located, locate, parents

FILES = ("record.json", "binding.json")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _path(tool_input: Dict[str, Any], root: str) -> str:
    """The call's file path, absolute and normalised. shortcut: case is not folded, so on a case-insensitive disk a
    path in other case is not matched; fold it when a user meets that."""
    raw = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    return os.path.normpath(raw if os.path.isabs(raw) else os.path.join(root, raw)) if raw else ""


def _key(path: str) -> str:
    return os.path.realpath(path).lower()


def _same(path: str, target: str) -> bool:
    """One file: the same file on disk, or the same path once links and case are set aside (a file not made yet)."""
    if os.path.exists(path) and os.path.exists(target) and os.path.samefile(path, target):
        return True
    return os.path.normcase(path) == os.path.normcase(os.path.normpath(target)) or _key(path) == _key(target)


def _aims_at_folder(tool: str, path: str, folder: str) -> Optional[str]:
    """Which of the record's two files in `folder` a file tool aims at, or None."""
    if tool not in WRITE_TOOLS or not path:
        return None
    for name in FILES:
        if _same(path, os.path.join(folder, name)):
            return name
    return None


def _shadows(tool: str, path: str, found: Located) -> Optional[str]:
    """For a record found through the variable or a pointer: the lookup file a file tool would write ahead of it (the
    start folder's `.as-agreed/` files, or a pointer in the start folder or a parent, the found one among them)."""
    if tool not in WRITE_TOOLS or not path or found.how not in ("env", "pointer"):
        return None
    for name in FILES:
        if _same(path, os.path.join(found.start, FOLDER, name)):
            return f"{FOLDER}/{name} in the start folder"
    if (found.pointer and _same(path, found.pointer)) or (
            os.path.basename(path).lower() == POINTER and _key(os.path.dirname(path)) in map(_key, parents(found.start))):
        return f"the pointer {path}"
    return None


def _sets_confirmed(tool_input: Dict[str, Any]) -> bool:
    """A file tool's new text that names `confirmed_by` with a value: only the person writes that, by hand."""
    texts = [tool_input.get("content"), tool_input.get("new_string"), tool_input.get("new_source")]
    texts += [e.get("new_string") for e in tool_input.get("edits") or [] if isinstance(e, dict)]
    for t in (x for x in texts if isinstance(x, str) and "confirmed_by" in x):
        try:
            data = json.loads(t)
        except ValueError:  # a fragment: empty only when every value it gives the field is ""
            values = re.findall(r'"confirmed_by"\s*:\s*("(?:[^"\\]|\\.)*"|null)', t)
            if not values or any(v not in ('""', "null") for v in values):
                return True
            continue
        if not isinstance(data, dict) or str(data.get("confirmed_by") or "").strip():
            return True
    return False


def _load(folder: str, name: str) -> Any:
    with open(os.path.join(folder, name), encoding="utf-8") as f:
        return json.load(f)


def _step(steps: List[Dict[str, Any]], tool: str, path: str) -> Optional[Dict[str, Any]]:
    for s in steps:
        if fnmatch.fnmatchcase(tool, str(s.get("tool", ""))) and (not s.get("path") or fnmatch.fnmatchcase(path, str(s["path"]))):
            return s
    return None


def _outputs(step: Dict[str, Any], tool_input: Dict[str, Any]) -> Dict[str, Any]:
    out = tool_input
    if step.get("json"):
        raw = tool_input.get(step["json"])
        if not isinstance(raw, str):
            raise ValueError(f"the tool input has no text field {step['json']!r} to read as JSON")
        out = json.loads(raw)
    if not isinstance(out, dict):
        raise ValueError(f"the step's output is a {type(out).__name__}, not a mapping of keys")
    return out


def _with_arrived(rows: List[Dict[str, Any]], outputs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The rows, each commitment verdict with the value its carried key held in the step's output (for the Board
    page); a key the output does not hold gets nothing."""
    from .drift import MISSING, get_path
    keys = {(r.get("commitment_id"), c.get("field")): c.get("key") for r in rows if r.get("kind") == "commitment"
            for c in r.get("carried_by") or [] if isinstance(c, dict)}
    out = []
    for r in rows:
        key = keys.get((r.get("unit"), r.get("field"))) if r.get("kind") == "verdict" else None
        value = get_path(outputs, key) if key else MISSING
        out.append(dict(r, arrived=value) if value is not MISSING else r)
    return out


def decide(event: Dict[str, Any], root: str, call: int = 1, rows_out: Optional[List[Dict[str, Any]]] = None,
           found: Optional[Located] = None) -> Tuple[str, List[str], List[str]]:
    """('unbound' | 'pass' | 'hold' | 'refused', lines, held) for one PreToolUse event in project `root`.

    `call` numbers the checked calls of one session, so each is its own link of the task's run; `rows_out`, when
    given, receives the check's rows, each commitment verdict with the value that arrived at its carried key; `found`
    is where the record is (`locate.py`), the project's `.as-agreed/` when not given."""
    found = found or Located("project", os.path.abspath(root), os.path.join(root, FOLDER))
    folder = str(found.folder)
    where = f"{FOLDER}/" if found.how == "project" else os.path.join(folder, "")
    tool = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    path = _path(tool_input, root)
    aimed = _aims_at_folder(tool, path, folder)
    try:
        rec = _load(folder, "record.json")
        confirmed = isinstance(rec, dict) and bool(str(rec.get("confirmed_by") or "").strip())
    except (OSError, ValueError):
        rec, confirmed = None, False
    if aimed and _sets_confirmed(tool_input):
        return "refused", [f"as-agreed: only the person writes confirmed_by in {where}record.json, by hand, after "
                           f"reading the record and the binding"], []
    if aimed and confirmed:
        return "refused", [f"as-agreed: the confirmed record and binding in {where} are frozen; a change needs the "
                           f"person: they clear confirmed_by by hand, then confirm the new version"], []
    if aimed:
        return "unbound", [], []  # drafting or repairing the two files before the person confirms them
    shadow = _shadows(tool, path, found) if confirmed else None
    if shadow:
        return "refused", [f"as-agreed: the confirmed record in {where} is frozen, and so is the way to it: a file "
                           f"tool may not write {shadow}, which the lookup reads first; a change needs the person"], []
    try:
        conf = _load(folder, "binding.json")
        step = _step(conf.get("steps") or [], tool, path)
    except (OSError, ValueError, AttributeError) as e:
        return "refused", [f"as-agreed: {where}binding.json cannot be read ({type(e).__name__}); repair it "
                           f"with a file tool"], []
    if step is None:
        return "unbound", [], []
    if not confirmed:
        return "refused", [f"as-agreed: the agreed record in {where}record.json is not confirmed by a person; show "
                           f"them the record and the binding, and let them write confirmed_by by hand"], []
    import as_agreed.sdk as ab
    from .hold import Policy, flag
    agreed = ab.register(str(rec.get("order") or ""), [ab.Commitment.from_mapping(c) for c in rec["commitments"]],
                         adapter=ab.memory_adapter(), source=f"{where}record.json")
    binding = step.get("binding") or {}
    link = f"{tool}#{call}@1"
    outputs = _outputs(step, tool_input)
    verdicts = ab.check(outputs, agreed, delivered=binding.get("kind") == "takes_effect",
                        link=link, binding=binding)
    # a change or a loss decided by code holds; what code cannot decide is recorded, not held (context.md)
    h = ab.hold(verdicts, agreed, policy=Policy("commit", "hold", on_undecided="record"), link=link)
    rows = agreed.adapter.records(agreed.adapter.run_id) + [v.record() for v in verdicts] + [h.record()]
    lines = flag(rows, conf.get("baseline_minutes"))
    if rows_out is not None:
        rows_out.extend(_with_arrived(rows, outputs))
    if h.passed:
        return "pass", lines, []
    from .hold import holds
    held = [f"{v.unit} {v.field} {v.drift_class}" for v in verdicts if holds(v)]
    # shortcut: each call is checked alone, so "kept because of the check" is 0 here; counting it across calls needs
    # the session's hook.jsonl, when a user asks for it
    return "hold", list(h.lines) + lines[:1] + lines[2:], held


def _deny(lines: List[str]) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "\n".join(lines)}}))


def main(stdin: str) -> int:
    """Claude Code's PreToolUse contract: JSON with a deny decision on stdout stops the call; nothing lets it run.
    The decision is printed before anything is logged, so a log that cannot be written never lets a step through."""
    try:
        found = locate(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    except Exception as e:  # noqa: BLE001 (a lookup that fails must stop the call, never let it through)
        print(f"as-agreed could not find the record: {type(e).__name__}: {' '.join(str(e).split())}", file=sys.stderr)
        return 2
    if found.how == "none":
        return 0
    if found.error:  # the person named a record that cannot be used: no call runs unchecked
        _deny([f"as-agreed: {found.error}. No call runs until it names the folder that holds the confirmed "
               f"record.json and binding.json, or {ENV} is unset and the pointer removed."])
        sys.stdout.flush()
        return 0
    root, folder = found.start, str(found.folder)
    record = None if found.how == "project" else folder  # the band's run folder: beside the record
    try:
        event = json.loads(stdin or "{}")
        from . import band
        rows: List[Dict[str, Any]] = []
        decision, lines, held = decide(event, root, band.calls(root, event.get("session_id"), record=record) + 1,
                                       rows, found=found)
    except Exception as e:  # a call in a project that declared steps, and that cannot be checked, does not run
        print(f"as-agreed could not check this call: {type(e).__name__}: {' '.join(str(e).split())}", file=sys.stderr)
        return 2
    if decision in ("hold", "refused"):
        _deny(lines)
    elif decision == "pass":
        print(json.dumps({"systemMessage": "as-agreed: " + lines[0]}))
    sys.stdout.flush()
    if decision == "unbound":
        return 0
    try:
        with open(os.path.join(folder, "hook.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                                "session": event.get("session_id"), "tool": event.get("tool_name"),
                                "decision": decision, "held": held}) + "\n")
    except OSError:
        pass  # the decision is already given; a log that cannot be written changes nothing
    if rows:
        try:
            band.write_run(root, event.get("session_id"), rows, record=record)
        except Exception:  # noqa: BLE001 (any failure here must leave the exit code 0, or the deny above is dropped)
            pass
    return 0
