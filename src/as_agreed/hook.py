"""`as-agreed hook`: the agent plugin's check before a step takes effect (0.1.0).

Claude Code runs it before each tool call (PreToolUse) and passes the call as JSON on stdin. The project keeps two
files in `.as-agreed/`: `record.json`, the agreed record, and `binding.json`, which tool calls are steps and what each
owes:

    record.json   {"order": "...", "commitments": [...], "confirmed_by": "<who confirmed it>", "confirmed_at": "..."}
    binding.json  {"steps": [{"tool": "Write", "path": "*/outbox/*.json", "json": "content",
                              "binding": {"serves": ["pay"], "kind": "takes_effect", "author": "finance"}}],
                   "baseline_minutes": null}

`tool` matches the tool's name (shell-style patterns allowed); `path`, when given, matches the call's file path made
absolute and normalised; `json`, when given, names the input field that holds the step's output as a JSON string;
otherwise the tool's input is the output. A call no step names is not checked.

A step is denied when a term changed or a binding term is lost (decided by code), when the record is not confirmed,
or when the check cannot run; what code cannot decide passes. A file tool may not set `confirmed_by`: the person
writes it by hand. Once it is set, file tools may not change `record.json` or `binding.json`. While either file is
unreadable, file tools may still repair it. Each checked call is logged to `.as-agreed/hook.jsonl` by tool, decision
and the unit, field and class of what was held; never a value of the call. Nothing here opens a network connection.
"""
from __future__ import annotations

import datetime
import fnmatch
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

FOLDER = ".as-agreed"
FILES = ("record.json", "binding.json")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _path(tool_input: Dict[str, Any], root: str) -> str:
    """The call's file path, absolute and normalised. shortcut: case is not folded, so on a case-insensitive disk a
    path in other case is not matched; fold it when a user meets that."""
    raw = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    return os.path.normpath(raw if os.path.isabs(raw) else os.path.join(root, raw)) if raw else ""


def _aims_at_folder(tool: str, path: str, root: str) -> Optional[str]:
    """Which of the two files a file tool aims at, or None."""
    if tool not in WRITE_TOOLS or not path:
        return None
    for name in FILES:
        target = os.path.join(root, FOLDER, name)
        same = os.path.exists(path) and os.path.exists(target) and os.path.samefile(path, target)
        if same or os.path.normcase(path) == os.path.normcase(os.path.normpath(target)):
            return name
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


def _load(root: str, name: str) -> Any:
    with open(os.path.join(root, FOLDER, name), encoding="utf-8") as f:
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


def decide(event: Dict[str, Any], root: str) -> Tuple[str, List[str], List[str]]:
    """('unbound' | 'pass' | 'hold' | 'refused', lines, held) for one PreToolUse event in project `root`."""
    tool = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    path = _path(tool_input, root)
    aimed = _aims_at_folder(tool, path, root)
    try:
        rec = _load(root, "record.json")
        confirmed = isinstance(rec, dict) and bool(str(rec.get("confirmed_by") or "").strip())
    except (OSError, ValueError):
        rec, confirmed = None, False
    if aimed and _sets_confirmed(tool_input):
        return "refused", [f"as-agreed: only the person writes confirmed_by in {FOLDER}/record.json, by hand, after "
                           f"reading the record and the binding"], []
    if aimed and confirmed:
        return "refused", [f"as-agreed: the confirmed record and binding in {FOLDER}/ are frozen; a change needs the "
                           f"person: they clear confirmed_by by hand, then confirm the new version"], []
    if aimed:
        return "unbound", [], []  # drafting or repairing the two files before the person confirms them
    try:
        conf = _load(root, "binding.json")
        step = _step(conf.get("steps") or [], tool, path)
    except (OSError, ValueError, AttributeError) as e:
        return "refused", [f"as-agreed: {FOLDER}/binding.json cannot be read ({type(e).__name__}); repair it "
                           f"with a file tool"], []
    if step is None:
        return "unbound", [], []
    if not confirmed:
        return "refused", [f"as-agreed: the agreed record in {FOLDER}/record.json is not confirmed by a person; show "
                           f"them the record and the binding, and let them write confirmed_by by hand"], []
    import as_agreed.sdk as ab
    from .hold import Policy, flag
    agreed = ab.register(str(rec.get("order") or ""), [ab.Commitment.from_mapping(c) for c in rec["commitments"]],
                         adapter=ab.memory_adapter(), source=f"{FOLDER}/record.json")
    binding = step.get("binding") or {}
    link = f"{tool}#1@1"
    verdicts = ab.check(_outputs(step, tool_input), agreed, delivered=binding.get("kind") == "takes_effect",
                        link=link, binding=binding)
    # a change or a loss decided by code holds; what code cannot decide is recorded, not held (context.md)
    h = ab.hold(verdicts, agreed, policy=Policy("commit", "hold", on_undecided="record"), link=link)
    rows = agreed.adapter.records(agreed.adapter.run_id) + [v.record() for v in verdicts] + [h.record()]
    lines = flag(rows, conf.get("baseline_minutes"))
    if h.passed:
        return "pass", lines, []
    from .hold import holds
    held = [f"{v.unit} {v.field} {v.drift_class}" for v in verdicts if holds(v)]
    # shortcut: each call is checked alone, so "kept because of the check" is 0 here; counting it across calls needs
    # the session's hook.jsonl, when a user asks for it
    return "hold", list(h.lines) + lines[:1] + lines[2:], held


def main(stdin: str) -> int:
    """Claude Code's PreToolUse contract: JSON with a deny decision on stdout stops the call; nothing lets it run.
    The decision is printed before anything is logged, so a log that cannot be written never lets a step through."""
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if not os.path.isfile(os.path.join(root, FOLDER, "binding.json")):
        return 0
    try:
        event = json.loads(stdin or "{}")
        decision, lines, held = decide(event, root)
    except Exception as e:  # a call in a project that declared steps, and that cannot be checked, does not run
        print(f"as-agreed could not check this call: {type(e).__name__}: {' '.join(str(e).split())}", file=sys.stderr)
        return 2
    if decision in ("hold", "refused"):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": "\n".join(lines)}}))
    elif decision == "pass":
        print(json.dumps({"systemMessage": "as-agreed: " + lines[0]}))
    sys.stdout.flush()
    if decision == "unbound":
        return 0
    try:
        with open(os.path.join(root, FOLDER, "hook.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                                "session": event.get("session_id"), "tool": event.get("tool_name"),
                                "decision": decision, "held": held}) + "\n")
    except OSError:
        pass  # the decision is already given; a log that cannot be written changes nothing
    return 0
