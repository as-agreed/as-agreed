"""The Langfuse importer (SPEC §8.2): a Langfuse observation export into the stage folders `reconcile` reads.

    as-agreed import langfuse <export>... <out_folder> [--commitment <order.md>] [--prefix <name>] [--no-scrub]
    as-agreed reconcile <out_folder>/<run>

Reads both key spellings of §8.2: an API page `{"data": [...], "meta": ...}` from GET /api/public/v2/observations
(camelCase, `input` and `output` as raw strings; ask for `fields=core,basic,io`, or the page has no text), a
blob-storage export file from `observations_v2/` in JSON Lines or a JSON list (snake_case), and a
`{"trace": ..., "observations": [...]}` file. Several files may be given (API pages); observations are grouped by trace
id, and a repeated (trace id, observation id) keeps its first row. An observation with no `id`, and a trace whose
observations carry neither `input` nor `output`, are refused with the field named, never written as empty links.

Per trace, one stage folder (SPEC §8.1) `<out_folder>/<prefix>-<first 8 characters of the trace id>/`:
  01-order.md       the order: with --commitment, your order file, and its `commitments.json` is copied beside it;
                    without, the root observation's input (the row with an empty parent; a row whose parent is not in
                    the export counts as a root too), serialised as §1.8, and when that is empty the first user message
                    of the earliest observation that has one (`task_from` in the meta says which).
  commitments.json  without --commitment: `{"commitments": [], "proxy": true}`, because the task as stated is a proxy
                    for what was agreed, not an agreement (§1.1, §8); `reconcile` then reads claims only and says so.
  NN-<agent>-<name>.md, .json
                    one link per other observation with a non-empty output, by start time (siblings ordered by start
                    time); the text is the output parsed as JSON when it is JSON and serialised as §1.8, else the raw
                    string; a same-stem `.json` holds the parsed output when it is a mapping, so the link declares fields.
  trace-meta.json   the meta: agents, hand-offs, `delivered`, where the order came from, what was scrubbed.

The rules the meta records:
  parent    per link, the nearest ancestor observation that is a link, else the order (0).
  agent     per link, the nearest observation at or above it that declares an agent: `gen_ai.agent.name`,
            `agent_name`, `graph.node.id` on an agent span, `gen_ai.agent.id`, or, on an observation of type AGENT with
            none of these, its name. A link with no such ancestor has `agent: null`; a name alone declares nothing.
  hand-off  where the declared agent changes from one link to the next (both declared); the meta lists each one with
            the link it lands on, and, when an observation named for a hand-off or transfer starts between the two
            links, its name (`via`), and the observation that declares the receiving agent (`to_observation_id`,
            where a verdict on the hand-off goes back as a Langfuse score or comment). The link before a hand-off gets
            `to`. No declared agent, no hand-off.
  final     the root's output when it has one (then it is the last link); otherwise the leaf observation that ends
            last among those with an input or an output (a marker span with neither is not a step). `delivered` is
            true when that observation carries an output; false means the run stops before a delivery.

Text is scrubbed of home-path user names, e-mail addresses and key-shaped strings unless --no-scrub. `write_run`
writes the project's trace-bench format (`links.jsonl`, one run folder per trace), which is not a stage folder and has
no command here. Standard library only; nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..serialise import to_lines

ALIASES = {"traceId": "trace_id", "parentObservationId": "parent_observation_id", "startTime": "start_time",
           "endTime": "end_time", "isRootObservation": "is_root_observation"}
AGENT_KEYS = ("gen_ai.agent.name", "agent_name", "graph.node.id", "gen_ai.agent.id")
HANDOFF_NAME = re.compile(r"hand[\s_-]?off|transfer[\s_]to", re.I)
HOME = re.compile(r"(/(?:Users|home)/)([^/\s\"']+)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECRET = re.compile(r"\b(?:sk-lf-|pk-lf-|sk-ant-|sk-|ghp_|gho_|xox[bp]-)[A-Za-z0-9_-]{8,}")
GENERIC_USERS = {"user", "username", "you", "me", "runner", "ubuntu", "root", "shared", "..."}
FRACTION = re.compile(r"^(.*T\d{2}:\d{2}:\d{2})\.(\d+)$")
PROXY_RECORD = {"commitments": [], "proxy": True}


# ---------- reading ----------
def load_file(path: str) -> Tuple[List[Dict[str, Any]], str]:
    """(observations, spelling) from one export file."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        data = []
        for n, ln in enumerate(text.splitlines(), 1):  # JSON Lines
            if ln.strip():
                try:
                    data.append(json.loads(ln))
                except json.JSONDecodeError as e:
                    raise ValueError(f"{path}: line {n} is not JSON ({e.msg}); a Langfuse export is JSON or JSON Lines")
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            data = data["data"]  # an API page
        elif isinstance(data.get("observations"), list):
            data = data["observations"]  # the {trace, observations} file
        else:
            data = [data]
    if not isinstance(data, list) or not all(isinstance(o, dict) for o in data):
        raise ValueError(f"{path}: not a list of observations")
    for i, o in enumerate(data):
        if empty(o.get("id")):
            raise ValueError(f"{path}: observation {i} has no `id`; every Langfuse observation carries one (SPEC §8.2)")
    spelling = "camelCase" if any("traceId" in o or "parentObservationId" in o for o in data) else "snake_case"
    return [normalise(o) for o in data], spelling


def normalise(o: Dict[str, Any]) -> Dict[str, Any]:
    """One observation with snake_case keys (the API's camelCase renamed)."""
    return {ALIASES.get(k, k): v for k, v in o.items()}


def parse_io(v: Any) -> Any:
    """A raw string is parsed as JSON when it is JSON (twice at most: exports double-encode), else kept as text."""
    for _ in range(2):
        if not isinstance(v, str):
            return v
        s = v.strip()
        if not s or s[0] not in "[{\"" and s not in ("null", "true", "false"):
            return v
        try:
            v = json.loads(s)
        except ValueError:
            return v
    return v


def empty(v: Any) -> bool:
    return v is None or (isinstance(v, (str, list, dict)) and len(v) == 0)


def when(v: Any) -> str:
    """A sortable UTC time from either spelling ('2025-12-04T14:51:22.049Z' or '2025-12-04 14:51:22.049000'); the
    fraction is cut or padded to six digits first, so Python 3.9 reads what 3.11 and later read."""
    if not isinstance(v, str) or not v:
        return ""
    s = v.strip().replace(" ", "T", 1).rstrip("Z").split("+")[0]
    m = FRACTION.match(s)
    if m:
        s = f"{m.group(1)}.{(m.group(2) + '000000')[:6]}"
    try:
        return dt.datetime.fromisoformat(s).strftime("%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        return v


def attributes(o: Dict[str, Any]) -> Dict[str, Any]:
    """The observation's metadata as one flat mapping: the metadata keys, the keys of its `attributes` mapping, and
    keys written flat as `attributes.<key>`; values stored as JSON strings are parsed."""
    md = parse_io(o.get("metadata"))
    if not isinstance(md, dict):
        return {}
    flat: Dict[str, Any] = {}
    for k, v in md.items():
        v = parse_io(v)
        if k == "attributes" and isinstance(v, dict):
            for ak, av in v.items():
                flat.setdefault(ak, parse_io(av))
        elif k.startswith("attributes."):
            flat.setdefault(k[len("attributes."):], v)
        else:
            flat.setdefault(k, v)
    return flat


def declared_agent(o: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(agent, the key that declared it) or None."""
    at = attributes(o)
    is_agent_span = str(o.get("type", "")).upper() == "AGENT" or str(at.get("openinference.span.kind", "")).upper() == "AGENT"
    for k in AGENT_KEYS:
        v = at.get(k)
        if k == "graph.node.id" and not is_agent_span:
            continue
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip(), k
    if str(o.get("type", "")).upper() == "AGENT" and o.get("name"):
        return str(o["name"]), "type AGENT, name"
    return None


def text_of(v: Any) -> Tuple[str, Optional[Dict[str, Any]], bool]:
    """(link text, outputs when a mapping, whether the library's serialiser refused the value). A refused value (a key
    with a dot, which §1.8 cannot join) keeps its JSON text and declares nothing."""
    try:
        return to_lines(v), v if isinstance(v, dict) else None, False
    except ValueError:
        return json.dumps(v, ensure_ascii=False, sort_keys=True), None, True


def user_text(v: Any) -> str:
    """The first user message's text in a chat input (OpenAI, pydantic-ai parts, Responses API, {messages: [...]})."""
    v = parse_io(v)
    if isinstance(v, dict) and isinstance(v.get("messages"), list):
        v = v["messages"]
    if not isinstance(v, list):
        return ""
    for m in v:
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content", m.get("parts"))
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return "\n".join(str(p.get("content", p.get("text", ""))) for p in c
                                 if isinstance(p, dict) and p.get("type", "text") in ("text", "input_text"))
    return ""


# ---------- one trace ----------
class Scrub:
    def __init__(self, on: bool):
        self.on, self.counts = on, {"home_path": 0, "email": 0, "secret": 0}

    def __call__(self, s: str) -> str:
        if not self.on or not s:
            return s

        def home(m: "re.Match[str]") -> str:
            if m.group(2) in GENERIC_USERS:
                return m.group(0)
            self.counts["home_path"] += 1
            return m.group(1) + "<user>"
        s = HOME.sub(home, s)
        s, n = EMAIL.subn("<email>", s)
        self.counts["email"] += n
        s, n = SECRET.subn("<secret>", s)
        self.counts["secret"] += n
        return s


def read_trace(obs: List[Dict[str, Any]], scrub: Scrub) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(meta, links) for one trace's observations, link 0 the order."""
    if not any("input" in o or "output" in o for o in obs):
        tid = obs[0].get("trace_id") if obs else None
        raise ValueError(f"trace {tid}: no observation has an `input` or an `output` field, so there is no text to read; "
                         f"the API returns them only with `fields=core,basic,io`, and a blob export only with the io group")
    by_id: Dict[str, Dict[str, Any]] = {}
    dupes = 0
    for i, o in enumerate(obs):
        if o.get("id") in by_id:
            dupes += 1
            continue
        o["_i"] = i
        by_id[o["id"]] = o
    mine = sorted(by_id.values(), key=lambda o: (when(o.get("start_time")), when(o.get("end_time")), o["_i"]))
    parent = {o["id"]: o.get("parent_observation_id") or None for o in mine}
    orphans = [o["id"] for o in mine if parent[o["id"]] and parent[o["id"]] not in by_id]
    for oid in orphans:
        parent[oid] = None  # its parent is not in the export: it is read as a root
    roots = [o for o in mine if parent[o["id"]] is None]
    if not roots:
        raise ValueError(f"trace {mine[0].get('trace_id')}: no root observation (every `parent_observation_id` points "
                         f"inside the trace)")
    root = roots[0]
    children: Dict[str, List[str]] = {}
    for o in mine:
        if parent[o["id"]]:
            children.setdefault(parent[o["id"]], []).append(o["id"])

    def ancestors(oid: str) -> Iterable[str]:
        seen = set()
        while oid and oid not in seen:
            seen.add(oid)
            yield oid
            oid = parent.get(oid)

    agent_of: Dict[str, Optional[Tuple[str, str, str]]] = {}  # (agent, the key that declared it, the declaring observation)
    for o in mine:
        agent_of[o["id"]] = next((d + (a,) for a in ancestors(o["id"]) for d in [declared_agent(by_id[a])] if d), None)

    task = text_of(parse_io(root.get("input")))[0] if not empty(parse_io(root.get("input"))) else ""
    task_from = "root input"
    if not task.strip():
        first = next((o for o in mine if user_text(o.get("input"))), None)
        task = user_text(first.get("input")) if first else ""
        task_from = f"first user message of observation {first.get('name')!r} (root input empty)" if first else "none"
    links: List[Dict[str, Any]] = [{"n": 0, "from": "order", "kind": "order", "phase": root.get("name"),
                                    "text": scrub(task), "agent": None, "observation_id": root["id"]}]
    n_of = {root["id"]: 0}
    dotted = 0
    body = [o for o in mine if o is not root and not empty(parse_io(o.get("output")))]
    root_out = parse_io(root.get("output"))
    if not empty(root_out):
        final = root
    else:
        leaves_ = [o for o in mine if o["id"] not in children and o is not root  # a marker span with no input and
                   and not (empty(parse_io(o.get("input"))) and empty(parse_io(o.get("output"))))]  # no output is not a step
        final = max(leaves_, key=lambda o: (when(o.get("end_time")) or when(o.get("start_time")), o["_i"])) if leaves_ else root
        if final in body and body[-1] is not final:
            body.remove(final)
            body.append(final)
    for o in body + ([root] if final is root and not empty(root_out) else []):
        text, outputs, forced = text_of(parse_io(o.get("output")))
        dotted += forced
        anc = next((a for a in list(ancestors(o["id"]))[1:] if a in n_of), None)
        ag = agent_of[o["id"]]
        link = {"n": len(links), "from": ag[0] if ag else o.get("name"), "kind": "final" if o is root else str(o.get("type", "")).lower(),
                "phase": o.get("name"), "line": o.get("start_time"), "text": scrub(text), "agent": ag[0] if ag else None,
                "agent_declared_by": ag[1] if ag else None, "agent_observation_id": ag[2] if ag else None,
                "observation_id": o["id"],
                "parent": (n_of[anc] if anc else 0) if o is not root else len(links) - 1}
        if outputs is not None:
            link["outputs"] = json.loads(scrub(json.dumps(outputs, ensure_ascii=False)))
        n_of[o["id"]] = link["n"]
        links.append(link)
    handoffs = []
    prev: Optional[Dict[str, Any]] = None
    for link in links[1:]:
        if not link["agent"]:
            continue
        if prev and link["agent"] != prev["agent"]:
            lo, hi = when(prev["line"]), when(link["line"])
            via = [o.get("name") for o in mine if HANDOFF_NAME.search(str(o.get("name", "")))
                   and lo <= when(o.get("start_time")) <= hi]
            handoffs.append({"at_link": link["n"], "from_agent": prev["agent"], "to_agent": link["agent"],
                             "via": via[0] if via else None, "to_observation_id": link["agent_observation_id"]})
            prev["to"] = link["agent"]
        prev = link
    agents = sorted({l["agent"] for l in links if l["agent"]})
    declared_anywhere = sorted({d[0] for d in (declared_agent(o) for o in mine) if d})
    meta = {"trace_id": root.get("trace_id"), "observations": len(mine), "duplicates_dropped": dupes,
            "roots": len(roots), "parents_not_in_export": len(orphans), "task_from": task_from,
            "links_after_order": len(links) - 1, "not_links_empty_output": len(mine) - len(body) - (1 if final is root and not empty(root_out) else 0),
            "agents": agents, "agents_declared_in_scopes": declared_anywhere, "handoffs": handoffs,
            "delivered": not empty(parse_io(final.get("output"))), "final_observation": final.get("name"),
            "siblings": "ordered by start time", "dotted_keys_kept_as_json": dotted}
    return meta, links


# ---------- writing ----------
def safe(s: Any, n: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(s or "")).strip("-")[:n] or "x"


def write_run(runs: str, name: str, meta: Dict[str, Any], links: List[Dict[str, Any]], scrub: Scrub) -> str:
    """The project's trace-bench run folder: `links.jsonl`, line 1 the meta, then one link per line, link 0 the order.
    Not a stage folder; `reconcile` does not read it."""
    d = os.path.join(runs, name)
    os.makedirs(d, exist_ok=True)
    meta = dict(meta, type="meta", links=len(links), roles=sorted({str(l["from"]) for l in links}),
                links_by_kind={k: sum(1 for l in links if l["kind"] == k) for k in sorted({l["kind"] for l in links})},
                scrubbed=dict(scrub.counts))
    with open(os.path.join(d, "links.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n")
        for l in links:
            rec = {"type": "link", "chars": len(l["text"]), "to": l.get("to"), **{k: v for k, v in l.items() if v is not None or k == "agent"}}
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return d


def write_stages(stages: str, name: str, meta: Dict[str, Any], links: List[Dict[str, Any]],
                 commitment: Optional[str]) -> str:
    """One stage folder (SPEC §8.1) for one trace; an earlier folder of the same name is replaced whole."""
    d = os.path.join(stages, name)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    width = max(2, len(str(len(links))))
    if commitment:
        with open(commitment, encoding="utf-8") as f:
            order = f.read()
        cj = os.path.join(os.path.dirname(os.path.abspath(commitment)), "commitments.json")
        if os.path.exists(cj):
            shutil.copyfile(cj, os.path.join(d, "commitments.json"))
    else:
        order = links[0]["text"]
        with open(os.path.join(d, "commitments.json"), "w", encoding="utf-8") as f:
            json.dump(PROXY_RECORD, f, indent=1)  # the task as stated: a proxy for the order, not the order
            f.write("\n")
    with open(os.path.join(d, f"{1:0{width}d}-order.md"), "w", encoding="utf-8") as f:
        f.write(order.rstrip("\n") + "\n")
    for l in links[1:]:
        stem = f"{l['n'] + 1:0{width}d}-{safe(l['agent'] or 'no-agent', 24)}-{safe(l['phase'], 32)}"
        with open(os.path.join(d, stem + ".md"), "w", encoding="utf-8") as f:
            f.write((l["text"] or "").rstrip("\n") + "\n")
        if l.get("outputs") is not None:
            with open(os.path.join(d, stem + ".json"), "w", encoding="utf-8") as f:
                json.dump(l["outputs"], f, ensure_ascii=False, indent=1, sort_keys=True)
    with open(os.path.join(d, "trace-meta.json"), "w", encoding="utf-8") as f:
        json.dump(dict(meta, order_from=("--commitment " + os.path.basename(commitment)) if commitment else meta["task_from"],
                       proxy=not commitment), f, ensure_ascii=False, indent=1, sort_keys=True)
    return d


def read_exports(paths: Sequence[str]) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    traces: Dict[str, List[Dict[str, Any]]] = {}
    spellings = []
    for p in paths:
        obs, spelling = load_file(p)
        spellings.append(spelling)
        for o in obs:
            traces.setdefault(str(o.get("trace_id")), []).append(o)
    if not traces:
        raise ValueError(f"no observation in {', '.join(paths)}")
    return traces, spellings


def convert(exports: Sequence[str], out: Optional[str] = None, *, runs: Optional[str] = None,
            commitment: Optional[str] = None, prefix: Optional[str] = None,
            scrub: bool = True) -> List[Tuple[str, Dict[str, Any]]]:
    """Read the export files, then write one stage folder per trace under `out` (and, with `runs`, the trace-bench run
    folder); every trace is read before anything is written. Returns (run name, meta) per trace, by trace id."""
    if commitment and not os.path.isfile(commitment):
        raise ValueError(f"--commitment {commitment}: no such file (an order file, with its commitments.json beside it)")
    traces, spellings = read_exports(exports)
    pre = safe(prefix or os.path.basename(exports[0]).split(".")[0], 40)
    read = []
    for tid in sorted(traces):
        sc = Scrub(scrub)
        meta, links = read_trace(traces[tid], sc)
        meta["source"] = ", ".join(os.path.basename(p) for p in exports) + f" ({'/'.join(sorted(set(spellings)))})"
        read.append((f"{pre}-{safe(tid, 8)}", meta, links, sc))
    for name, meta, links, sc in read:
        if runs:
            write_run(runs, name, meta, links, sc)
        if out:
            write_stages(out, name, meta, links, commitment)
    return [(name, meta) for name, meta, _, _ in read]


def summary(where: str, meta: Dict[str, Any]) -> str:
    """One line per trace: where it went, its links, agents, hand-offs and whether it delivered."""
    hs = "; ".join(f"{h['from_agent']} -> {h['to_agent']} at link {h['at_link']}" for h in meta["handoffs"]) or "none"
    return (f"{where}\t{meta['links_after_order']} links\tagents {len(meta['agents'])}\thand-offs {hs}\t"
            f"delivered {str(meta['delivered']).lower()}")


# ---------- command line: as-agreed import langfuse ----------
def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("exports", nargs="+", metavar="export",
                   help="a Langfuse export: an API page (.json) or a blob-storage file (.json, .jsonl); several API pages may be given")
    p.add_argument("out", metavar="out_folder", help="where the stage folders go, one per trace")
    p.add_argument("--commitment", default=None,
                   help="your order file (the as-agreed record); its commitments.json is copied beside it. Without it the "
                        "trace's root input is the order, marked proxy: true")
    p.add_argument("--prefix", default=None, help="the stage folder name's prefix (default: the first export's file stem)")
    p.add_argument("--no-scrub", action="store_true", help="keep home-path user names, e-mail addresses and key-shaped strings")


def run_cli(a: argparse.Namespace) -> int:
    for name, meta in convert(a.exports, a.out, commitment=a.commitment, prefix=a.prefix, scrub=not a.no_scrub):
        print(summary(os.path.join(a.out, name), meta))
    return 0
