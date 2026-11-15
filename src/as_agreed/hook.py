"""`as-agreed hook`: the agent plugin's check before a step takes effect (0.1.0).

Claude Code runs it before each tool call (PreToolUse) and passes the call as JSON on stdin. The project keeps two
files in `.as-agreed/`: `record.json`, the agreed record a person confirmed, and `binding.json`, which tool calls are
steps and what each owes:

    record.json   {"order": "...", "commitments": [...], "confirmed_by": "<who confirmed it>", "confirmed_at": "..."}
    binding.json  {"steps": [{"tool": "Write", "path": "*/outbox/*.json", "json": "content",
                              "binding": {"serves": ["pay"], "kind": "takes_effect", "author": "finance"}}],
                   "baseline_minutes": null}

`tool` matches the tool's name (shell-style patterns allowed); `path`, when given, matches the call's `file_path`;
`json`, when given, names the input field that holds the step's output as a JSON string; otherwise the tool's input
is the output. A call no step names is not checked. A step is denied when a binding term is lost or changed (decided
by code), when the record is not confirmed, or when the check cannot run; what code cannot decide passes and is
logged as unchecked. Every call is logged to `.as-agreed/hook.jsonl` with its tool name, decision and flag lines; the tool's input is never logged.
Nothing here opens a network connection.
"""
from __future__ import annotations

import datetime
import fnmatch
import json
import os
from typing import Any, Dict, List, Optional, Tuple

FOLDER = ".as-agreed"
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _step(steps: List[Dict[str, Any]], tool: str, tool_input: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for s in steps:
        if not fnmatch.fnmatchcase(tool, str(s.get("tool", ""))):
            continue
        if s.get("path") and not fnmatch.fnmatch(str(tool_input.get("file_path", "")), str(s["path"])):
            continue
        return s
    return None


def _edits_the_record(tool: str, tool_input: Dict[str, Any], root: str) -> bool:
    """A file tool aimed at record.json or binding.json. shortcut: a shell command that edits them is not seen; the
    README's limits say so."""
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if tool not in WRITE_TOOLS or not path:
        return False
    target = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    return target in {os.path.realpath(os.path.join(root, FOLDER, n)) for n in ("record.json", "binding.json")}


def _outputs(step: Dict[str, Any], tool_input: Dict[str, Any]) -> Any:
    if not step.get("json"):
        return tool_input
    raw = tool_input.get(step["json"])
    if not isinstance(raw, str):
        raise ValueError(f"the tool input has no text field {step['json']!r} to read as JSON")
    return json.loads(raw)


def decide(event: Dict[str, Any], root: str) -> Tuple[str, List[str]]:
    """('unbound' | 'pass' | 'hold' | 'refused', lines) for one PreToolUse event in project `root`."""
    tool = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    record_path = os.path.join(root, FOLDER, "record.json")
    rec = {}
    if os.path.isfile(record_path):
        with open(record_path, encoding="utf-8") as f:
            rec = json.load(f)
    confirmed = bool(str(rec.get("confirmed_by") or "").strip())
    if confirmed and _edits_the_record(tool, tool_input, root):
        return "refused", [f"as-agreed: the confirmed record and binding in {FOLDER}/ are frozen; a change needs the "
                           f"person: they clear confirmed_by by hand, then confirm the new version"]
    with open(os.path.join(root, FOLDER, "binding.json"), encoding="utf-8") as f:
        conf = json.load(f)
    step = _step(conf.get("steps") or [], tool, tool_input)
    if step is None:
        return "unbound", []
    if not confirmed:
        return "refused", [f"as-agreed: the agreed record in {FOLDER}/record.json is not confirmed by a person; show "
                           f"it to them, and write confirmed_by only on their word"]
    import as_agreed.sdk as ab
    from .hold import Policy, flag
    agreed = ab.register(str(rec.get("order") or ""), [ab.Commitment.from_mapping(c) for c in rec["commitments"]],
                         adapter=ab.memory_adapter(), source=f"{FOLDER}/record.json")
    binding = step.get("binding") or {}
    verdicts = ab.check(_outputs(step, tool_input), agreed, delivered=binding.get("kind") == "takes_effect",
                        link=f"{tool}#1@1", binding=binding)
    # a sure loss holds; what code cannot decide is recorded, not held (context.md, agreed behaviour)
    h = ab.hold(verdicts, agreed, policy=Policy("commit", "hold", on_undecided="record"), link=f"{tool}#1@1")
    rows = agreed.adapter.records(agreed.adapter.run_id) + [v.record() for v in verdicts] + [h.record()]
    lines = flag(rows, conf.get("baseline_minutes"))
    if h.passed:
        return "pass", lines
    # shortcut: each call is checked alone, so "kept because of the check" is 0 here; counting it across calls needs
    # the session's hook.jsonl, when a user asks for it
    return "hold", list(h.lines) + lines[:1] + lines[2:]


def main(stdin: str) -> int:
    """Claude Code's PreToolUse contract: JSON with a deny decision on stdout to stop the call, nothing to let it
    run. A project without `.as-agreed/binding.json` is not checked and nothing is written."""
    event = json.loads(stdin or "{}")
    root = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
    if not os.path.isfile(os.path.join(root, FOLDER, "binding.json")):
        return 0
    try:
        decision, lines = decide(event, root)
    except Exception as e:  # a declared step whose check cannot run does not take effect unchecked
        decision, lines = "refused", [f"as-agreed could not check this step: {type(e).__name__}: {' '.join(str(e).split())}"]
    with open(os.path.join(root, FOLDER, "hook.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                            "session": event.get("session_id"), "tool": event.get("tool_name"),
                            "decision": decision, "flag": [ln for ln in lines if not ln.startswith("DANGER")]},
                           ensure_ascii=False) + "\n")
    if decision in ("hold", "refused"):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": "\n".join(lines)}}))
    elif decision == "pass":
        print(json.dumps({"systemMessage": "as-agreed: " + lines[0]}))
    return 0
