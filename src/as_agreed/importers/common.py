"""The rule both 0.0.3 importers share (SPEC §8.3, §8.4): one trace's records, read into `Node`s, become the order and
the links of one stage folder (SPEC §8.1), with the agents, the hand-offs and `delivered` in `trace-meta.json`.

The rule is the Langfuse importer's (SPEC §8.2, `importers/langfuse.py`), whose helpers it uses and which it leaves as
shipped in 0.0.2:
  order     with --commitment, your order file; else the root's input, and when that is empty the first user message
            of the earliest record that has one (`task_from` says which). A trace with neither is refused with the
            `--commitment` hint, never written with an empty order.
  links     every record but the root that produced an output (and, for a span whose content was not captured, an
            empty link, counted apart), in the importer's order; `parent` is the nearest ancestor that is a link, else
            the order (0).
  agent     the nearest record at or above a link that declares one, under the importer's keys; a name never does.
  hand-off  where the declared agent changes from one link to the next (both declared); `via` is a record named for a
            hand-off or a transfer that starts between the two links.
  final     the root's output when it has one (then it is the last link, its parent the link before it); otherwise the
            leaf step that ends last. `delivered` is true when the final record carries an output, false when it does
            not, and null when its content was not captured, so nobody can tell.
Standard library only; nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .langfuse import HANDOFF_NAME, Scrub, safe, write_stages


@dataclass
class Node:
    """One record of a trace (a LangSmith run, an OpenTelemetry span), read into the shape the rule takes."""
    id: str
    parent: Optional[str]
    name: str
    kind: str                      # the step type: a run's run_type, a span's gen_ai.operation.name
    start: Any                     # sortable within the trace (a time string, or nanoseconds)
    end: Any
    line: Any = None               # the start time as the export writes it
    order: Tuple[Any, ...] = ()    # the sort key the importer chose (dotted_order, or start time)
    task: str = ""                 # the order text this record would give as the root
    user: str = ""                 # its first user message, for the fallback
    has_input: bool = False
    text: str = ""                 # the link text (already serialised as §1.8)
    outputs: Optional[Dict[str, Any]] = None
    has_output: bool = False
    dotted: bool = False           # an output with a dotted key, kept as JSON text (§1.8)
    step: bool = True              # a record that can be a step at all (a plain span is only structure)
    captured: bool = True          # False when the export says nothing of its content (OTLP, opt-in)
    agent: Optional[Tuple[str, str]] = None   # (agent, the key that declared it)
    status: Optional[str] = None
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def assemble(nodes: Sequence[Node], scrub: Scrub, *, root_id: Optional[str] = None, siblings: str,
             trace_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(meta, links) for one trace, link 0 the order."""
    by_id: Dict[str, Node] = {}
    dupes = 0
    for n in nodes:
        if n.id in by_id:
            dupes += 1
            continue
        by_id[n.id] = n
    mine = sorted(by_id.values(), key=lambda n: n.order)
    index = {n.id: i for i, n in enumerate(mine)}
    parent = {n.id: (n.parent if n.parent else None) for n in mine}
    orphans = [n.id for n in mine if parent[n.id] and parent[n.id] not in by_id]
    for nid in orphans:
        parent[nid] = None  # its parent is not in the export: it is read as a root
    roots = [n for n in mine if parent[n.id] is None]
    if not roots:
        raise ValueError(f"trace {trace_id}: no root record (every parent pointer points inside the trace)")
    root = by_id[root_id] if root_id in by_id and parent[root_id] is None else roots[0]
    root_from = "id equals trace_id" if root_id in by_id and root is by_id[root_id] else "first record with no parent in the export"

    def ancestors(nid: Optional[str]) -> Iterable[str]:
        seen = set()
        while nid and nid not in seen:
            seen.add(nid)
            yield nid
            nid = parent.get(nid)

    agent_of: Dict[str, Optional[Tuple[str, str, str]]] = {}
    for n in mine:
        agent_of[n.id] = next((by_id[a].agent + (a,) for a in ancestors(n.id) if by_id[a].agent), None)

    task, task_from = root.task, "root input"
    if not task.strip():
        first = next((n for n in mine if n.user.strip()), None)
        task = first.user if first else ""
        task_from = f"first user message of {first.name!r} (root input empty)" if first else "none"
    links: List[Dict[str, Any]] = [{"n": 0, "from": "order", "kind": "order", "phase": root.name, "text": scrub(task),
                                    "agent": None, "observation_id": root.id}]
    steps = [n for n in mine if n is not root and n.step]
    body = [n for n in steps if n.has_output or not n.captured or n.status == "error"]
    above = {a for n in steps for a in list(ancestors(n.id))[1:]}  # records with a step below them
    if root.has_output or (root.status == "error" and root.captured):
        final = root  # a root that failed with no output is where the run stopped
    else:
        leaves = [n for n in steps if n.id not in above  # a step with no input, no output and no error is a marker
                  and (n.has_input or n.has_output or not n.captured or n.status == "error")]
        final = max(leaves, key=lambda n: (n.end or n.start, index[n.id])) if leaves else root
        if final in body and body[-1] is not final:
            body.remove(final)
            body.append(final)
    n_of = {root.id: 0}
    errors = []
    dotted = 0
    for n in body + ([root] if final is root else []):
        anc = next((a for a in list(ancestors(n.id))[1:] if a in n_of), None)
        ag = agent_of[n.id]
        link: Dict[str, Any] = {
            "n": len(links), "from": ag[0] if ag else n.name, "kind": "final" if n is root else n.kind,
            "phase": n.name, "line": n.line, "text": scrub(n.text), "agent": ag[0] if ag else None,
            "agent_declared_by": ag[1] if ag else None, "agent_observation_id": ag[2] if ag else None,
            "observation_id": n.id, "parent": (n_of[anc] if anc else 0) if n is not root else len(links) - 1}
        if n.outputs is not None:
            link["outputs"] = json.loads(scrub(json.dumps(n.outputs, ensure_ascii=False)))
        if not n.captured:
            link["content"] = "not captured"
        if n.status is not None:
            link["status"] = n.status
        if n.error:
            link["error"] = scrub(n.error)
            errors.append({"at_link": link["n"], "record": n.name, "status": n.status, "error": link["error"]})
        dotted += n.dotted
        n_of[n.id] = link["n"]
        links.append(link)
    handoffs = []
    prev: Optional[Dict[str, Any]] = None
    for link in links[1:]:
        if not link["agent"]:
            continue
        if prev and link["agent"] != prev["agent"]:
            lo, hi = by_id[prev["observation_id"]].start, by_id[link["observation_id"]].start
            via = [n.name for n in mine if HANDOFF_NAME.search(n.name or "") and lo <= n.start <= hi]
            handoffs.append({"at_link": link["n"], "from_agent": prev["agent"], "to_agent": link["agent"],
                             "via": via[0] if via else None, "to_observation_id": link["agent_observation_id"]})
            prev["to"] = link["agent"]
        prev = link
    meta = {"trace_id": trace_id, "observations": len(mine), "duplicates_dropped": dupes, "roots": len(roots),
            "root_from": root_from, "parents_not_in_export": len(orphans), "task_from": task_from,
            "links_after_order": len(links) - 1, "not_links": len(mine) - 1 - len(body),
            "agents": sorted({l["agent"] for l in links if l["agent"]}),
            "agents_declared_in_scopes": sorted({n.agent[0] for n in mine if n.agent}), "handoffs": handoffs,
            "delivered": (True if final.has_output else (None if not final.captured else False)),
            "final_observation": final.name, "siblings": siblings, "dotted_keys_kept_as_json": dotted}
    if errors:
        meta["errors"] = errors
    return meta, links


def convert_traces(traces: Dict[str, Callable[[Scrub], Tuple[Dict[str, Any], List[Dict[str, Any]]]]],
                   exports: Sequence[str], out: Optional[str], *, commitment: Optional[str], prefix: Optional[str],
                   scrub: bool, source: str, no_order_hint: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Read every trace, then write one stage folder per trace under `out`; nothing is written when one is refused.
    `traces` maps a trace id to a function that reads it under a given scrubber."""
    if commitment and not os.path.isfile(commitment):
        raise ValueError(f"--commitment {commitment}: no such file (an order file, with its commitments.json beside it)")
    if not traces:
        raise ValueError(f"no record in {', '.join(exports)}")
    pre = safe(prefix or os.path.basename(exports[0]).split(".")[0], 40)
    heads: Dict[str, int] = {}
    for tid in traces:
        heads[safe(tid, 8)] = heads.get(safe(tid, 8), 0) + 1
    read = []
    for tid in sorted(traces):  # two traces whose ids share the first 8 characters keep their whole ids
        meta, links = traces[tid](Scrub(scrub))
        if not commitment and not links[0]["text"].strip():
            raise ValueError(f"trace {tid}: no text to register as the order (the root has no input and no record has a "
                             f"user message){no_order_hint}; pass --commitment <order file>")
        meta["source"] = ", ".join(os.path.basename(p) for p in exports) + f" ({source})"
        read.append((f"{pre}-{safe(tid, 8 if heads[safe(tid, 8)] == 1 else 64)}", meta, links))
    if out:
        for name, meta, links in read:
            write_stages(out, name, meta, links, commitment)
    return [(name, meta) for name, meta, _ in read]


def summary(where: str, meta: Dict[str, Any], extra: Sequence[str] = ()) -> str:
    """One line per trace: where it went, its links, agents, hand-offs, whether it delivered, then any notes."""
    hs = "; ".join(f"{h['from_agent']} -> {h['to_agent']} at link {h['at_link']}" for h in meta["handoffs"]) or "none"
    d = meta["delivered"]
    line = (f"{where}\t{meta['links_after_order']} links\tagents {len(meta['agents'])}\thand-offs {hs}\t"
            f"delivered {'unknown' if d is None else str(d).lower()}")
    return "\t".join([line, *extra])


def add_arguments(p: argparse.ArgumentParser, what: str) -> None:
    """The options of `as-agreed import langfuse`, for another kind of export."""
    p.add_argument("exports", nargs="+", metavar="export", help=what)
    p.add_argument("out", metavar="out_folder", help="where the stage folders go, one per trace")
    p.add_argument("--commitment", default=None,
                   help="your order file (the as-agreed record); its commitments.json is copied beside it. Without it the "
                        "trace's root input is the order, marked proxy: true")
    p.add_argument("--prefix", default=None, help="the stage folder name's prefix (default: the first export's file stem)")
    p.add_argument("--no-scrub", action="store_true", help="keep home-path user names, e-mail addresses and key-shaped strings")
