"""The LangSmith importer (SPEC §8.3): LangSmith run objects into the stage folders `reconcile` reads.

    as-agreed import langsmith <export>... <out_folder> [--commitment <order.md>] [--prefix <name>] [--no-scrub]
    as-agreed reconcile <out_folder>/<run>

Reads run objects as the SDK and the API give them (SPEC §8.3; the fields of the run data format): a JSON list of runs,
an API page `{"runs": [...], ...}` from `/runs/query`, one run with its `child_runs` nested (the SDK's `read_run` with
child runs), JSON Lines with one run per line, and a CSV (`.csv`) with the same column names, its `inputs`, `outputs`
and `extra` cells written as JSON. Runs are grouped by `trace_id` (from the first segment of `dotted_order` when the
field is missing); a repeated run id keeps its first row. A run with no `id`, a run with neither `trace_id` nor
`dotted_order`, and a trace whose runs carry neither `inputs` nor `outputs` are refused with the field named.

The rule (SPEC §8.3; the shared part is `importers/common.py`, which is the Langfuse importer's rule):
  root      the run whose `id` equals `trace_id`; when the export does not hold it, the first run with no parent in the
            export.
  parent    `parent_run_id`; a link's parent is the nearest ancestor run that is a link, else the order (0).
  order     runs sorted by `dotted_order` (start time, then run id, from the root down: a parent before its children,
            siblings by start time); when a run of the trace has no `dotted_order`, by start time, and the meta says so.
  text      a run's `outputs`, serialised as §1.8 (`messages.0.role: ...`, `messages.0.content: ...`); a mapping is also
            the stage's same-stem `.json`, so the link declares fields. A run with status `error` and no outputs is a
            link too, its text `error: <first line of error>`. Every link carries the run's `status`, and `error` when
            there is one; `trace-meta.json` lists the errors.
  agent     declared in the run's metadata (`extra.metadata`, or a top-level `metadata`) under `gen_ai.agent.name`,
            `agent_name` or `gen_ai.agent.id`. The LangSmith pages of §8.3 name no agent field (`extra` is "Any extra
            information"), so these are the keys the Langfuse importer reads, and a run's `name`, its `run_type` and
            LangGraph's `langgraph_node` (a node is a step) never declare one.
  final     the root's outputs when it has them; a root with status `error` and no outputs (the run stopped there);
            otherwise the leaf run that ends last. `delivered` is true when that run has outputs.
Standard library only; nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .common import Node, add_arguments as common_arguments, assemble, convert_traces, summary
from .langfuse import Scrub, empty, parse_io, text_of, user_text, when

AGENT_KEYS = ("gen_ai.agent.name", "agent_name", "gen_ai.agent.id")
NULLABLE = ("parent_run_id", "dotted_order", "inputs", "outputs", "error", "extra", "metadata", "end_time", "status")


# ---------- reading ----------
def flatten(run: Dict[str, Any], parent: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """A run and its nested `child_runs`, each child keeping its own parent and trace pointers when it has them."""
    kids = run.get("child_runs") or []
    me = {k: v for k, v in run.items() if k != "child_runs"}
    if parent is not None:
        me.setdefault("parent_run_id", parent.get("id"))
        if empty(me.get("trace_id")):
            me["trace_id"] = parent.get("trace_id")
    out = [me]
    for k in kids if isinstance(kids, list) else []:
        if isinstance(k, dict):
            out.extend(flatten(k, me))
    return out


def read_csv(path: str) -> List[Dict[str, Any]]:
    """The CSV form: the run data format's column names; an empty cell is no value; JSON cells are parsed later."""
    csv.field_size_limit(2 ** 31 - 1)  # a run's inputs and outputs can be long
    with open(path, encoding="utf-8", newline="") as f:
        try:
            rows = list(csv.DictReader(f))
        except csv.Error as e:
            raise ValueError(f"{path}: not a CSV file ({e})") from None
    return [{k: (None if v == "" and k in NULLABLE else v) for k, v in r.items() if k is not None} for r in rows]


def load_file(path: str) -> Tuple[List[Dict[str, Any]], str]:
    """(runs, form) from one export file."""
    if path.lower().endswith(".csv"):
        runs, form = read_csv(path), "CSV"
    else:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        try:
            data: Any = json.loads(text)
            form = "JSON"
        except json.JSONDecodeError:
            data, form = [], "JSON Lines"
            for n, ln in enumerate(text.splitlines(), 1):
                if ln.strip():
                    try:
                        data.append(json.loads(ln))
                    except json.JSONDecodeError as e:
                        raise ValueError(f"{path}: line {n} is not JSON ({e.msg}); a LangSmith export is JSON, JSON Lines "
                                         f"or CSV") from None
        if isinstance(data, dict):
            if isinstance(data.get("runs"), list):
                data, form = data["runs"], "API page"
            else:
                data = [data]  # one run, its child runs nested
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise ValueError(f"{path}: not a list of runs")
        if any(r.get("child_runs") for r in data):
            form += ", child runs nested"
        runs = [x for r in data for x in flatten(r)]
    for i, r in enumerate(runs):
        if empty(r.get("id")):
            raise ValueError(f"{path}: run {i} has no `id`; every LangSmith run carries one (SPEC §8.3)")
        if empty(r.get("trace_id")):
            dotted = r.get("dotted_order")
            if isinstance(dotted, str) and "Z" in dotted:
                r["trace_id"] = dotted.split(".")[0].split("Z", 1)[1]  # <start time>Z<root run id>
            else:
                raise ValueError(f"{path}: run {r.get('id')} has no `trace_id` and no `dotted_order` to read it from "
                                 f"(SPEC §8.3)")
    return runs, form


def metadata(r: Dict[str, Any]) -> Dict[str, Any]:
    """`extra.metadata` and a top-level `metadata`, as one mapping; values stored as JSON strings are parsed."""
    out: Dict[str, Any] = {}
    extra = parse_io(r.get("extra"))
    for md in (extra.get("metadata") if isinstance(extra, dict) else None, r.get("metadata")):
        md = parse_io(md)
        if isinstance(md, dict):
            for k, v in md.items():
                out.setdefault(k, parse_io(v))
    return out


def declared_agent(r: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    md = metadata(r)
    for k in AGENT_KEYS:
        v = md.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip(), k
    return None


def first_user(v: Any) -> str:
    """The first user message of a chat input: `{messages: [...]}`, a batch of message lists, a message with its
    fields under `kwargs`, or `type: human` for the role."""
    v = parse_io(v)
    if isinstance(v, dict) and isinstance(v.get("messages"), list):
        v = v["messages"]
    if isinstance(v, list) and v and all(isinstance(x, list) for x in v):
        v = [m for x in v for m in x]
    if not isinstance(v, list):
        return ""
    msgs = []
    for m in v:
        if isinstance(m, dict) and isinstance(m.get("kwargs"), dict):
            m = m["kwargs"]
        if isinstance(m, dict) and "role" not in m and m.get("type") == "human":
            m = dict(m, role="user")
        msgs.append(m)
    return user_text(msgs)


def to_node(r: Dict[str, Any], key: Tuple[Any, ...]) -> Node:
    inputs, outputs = parse_io(r.get("inputs")), parse_io(r.get("outputs"))
    status = r.get("status") if isinstance(r.get("status"), str) else None
    error = r.get("error") if isinstance(r.get("error"), str) and r.get("error").strip() else None
    text, mapping, dotted = text_of(outputs) if not empty(outputs) else ("", None, False)
    if empty(outputs) and status == "error" and error:
        text = "error: " + next(ln.strip() for ln in error.splitlines() if ln.strip())
    return Node(id=str(r["id"]), parent=str(r["parent_run_id"]) if not empty(r.get("parent_run_id")) else None,
                name=str(r.get("name") or ""), kind=str(r.get("run_type") or "run").lower(),
                start=when(r.get("start_time")), end=when(r.get("end_time")), line=r.get("start_time"), order=key,
                task=text_of(inputs)[0] if not empty(inputs) else "", user=first_user(inputs),
                has_input=not empty(inputs), text=text, outputs=mapping, has_output=not empty(outputs), dotted=dotted,
                agent=declared_agent(r), status=status, error=error)


def read_trace(runs: List[Dict[str, Any]], scrub: Scrub) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(meta, links) for one trace's runs, link 0 the order."""
    tid = str(runs[0].get("trace_id"))
    if not any("inputs" in r or "outputs" in r for r in runs):
        raise ValueError(f"trace {tid}: no run has an `inputs` or an `outputs` field, so there is no text to read; "
                         f"export the runs with their inputs and outputs (SPEC §8.3)")
    dotted = all(isinstance(r.get("dotted_order"), str) and r["dotted_order"] for r in runs)
    nodes = [to_node(r, (r["dotted_order"], i) if dotted else (when(r.get("start_time")), when(r.get("end_time")), i))
             for i, r in enumerate(runs)]
    meta, links = assemble(nodes, scrub, root_id=tid, trace_id=tid,
                           siblings="ordered by dotted_order" if dotted else "ordered by start time (a run has no dotted_order)")
    statuses: Dict[str, int] = {}
    for r in runs:
        statuses[str(r.get("status"))] = statuses.get(str(r.get("status")), 0) + 1
    meta["status"] = dict(sorted(statuses.items()))
    return meta, links


def read_exports(paths: List[str]) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    traces: Dict[str, List[Dict[str, Any]]] = {}
    forms = []
    for p in paths:
        runs, form = load_file(p)
        forms.append(form)
        for r in runs:
            traces.setdefault(str(r["trace_id"]), []).append(r)
    return traces, forms


def convert(exports: List[str], out: Optional[str] = None, *, commitment: Optional[str] = None,
            prefix: Optional[str] = None, scrub: bool = True) -> List[Tuple[str, Dict[str, Any]]]:
    """Read the export files, then write one stage folder per trace under `out`; every trace is read before anything
    is written. Returns (run name, meta) per trace, by trace id."""
    traces, forms = read_exports(exports)
    return convert_traces({t: (lambda sc, runs=runs: read_trace(runs, sc)) for t, runs in traces.items()}, exports, out,
                          commitment=commitment, prefix=prefix, scrub=scrub, source="/".join(sorted(set(forms))),
                          no_order_hint="")


def notes(meta: Dict[str, Any]) -> List[str]:
    errs = meta.get("errors") or []
    return [f"errors at link {', '.join(str(e['at_link']) for e in errs)}"] if errs else []


# ---------- command line: as-agreed import langsmith ----------
def add_arguments(p: argparse.ArgumentParser) -> None:
    common_arguments(p, "LangSmith runs: JSON (a list, an API page, a run with its child runs), JSON Lines, or CSV")


def run_cli(a: argparse.Namespace) -> int:
    for name, meta in convert(a.exports, a.out, commitment=a.commitment, prefix=a.prefix, scrub=not a.no_scrub):
        print(summary(os.path.join(a.out, name), meta, notes(meta)))
    return 0
