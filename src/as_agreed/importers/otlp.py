"""The OpenTelemetry importer (SPEC §8.4): an OTLP JSON span file into the stage folders `reconcile` reads.

    as-agreed import otlp <export>... <out_folder> [--commitment <order.md>] [--prefix <name>] [--no-scrub]
    as-agreed reconcile <out_folder>/<run>

Reads OTLP JSON `TracesData` (the file exporter's JSON Lines, one `TracesData` per line, or one JSON object or a list
of them): spans from `resourceSpans[].scopeSpans[].spans[]`, keys in lowerCamelCase, attributes as
`{"key": ..., "value": {"stringValue": ...}}` (`intValue` as a string, `boolValue`, `doubleValue`, `arrayValue`,
`kvlistValue`, `bytesValue`). The file is not ordered, so spans are grouped by `traceId` across lines and files; a
repeated span id keeps its first row. A span with no `spanId` or no `traceId` is refused with the field named.

The rule (SPEC §8.4, with OpenTelemetry's GenAI conventions; the shared part is `importers/common.py`, which is the
Langfuse importer's rule):
  root      the span with an empty `parentSpanId` (a span whose parent is not in the file counts as a root too); the
            earliest when there are several.
  parent    `parentSpanId`; a link's parent is the nearest ancestor span that is a link, else the order (0).
  order     siblings (and every span) by `startTimeUnixNano`, then `endTimeUnixNano`; the meta says so.
  step      a span with `gen_ai.operation.name` (its value is the link's kind: `chat`, `invoke_agent`, `execute_tool`,
            ...) or with a content attribute; any other span is structure only.
  text      the output: `gen_ai.output.messages` (structured, or a JSON string), the `content` of each message's text
            parts, one message to a line, and when that text is a JSON object, its §1.8 lines and the stage's `.json`;
            messages with no text part (a tool call) are kept as their §1.8 lines; else `gen_ai.tool.call.result`, as
            §1.8. The input is `gen_ai.input.messages` (the order is the root's first user message) or
            `gen_ai.tool.call.arguments`.
  content   the message attributes are opt-in: OpenTelemetry instrumentations record no content unless
            `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT` is `SPAN_ONLY` or `SPAN_AND_EVENT` (the default is
            `NO_CONTENT`; content on events, `EVENT_ONLY`, is not read). A step with no content attribute is a link with
            empty text, counted apart (`content_not_captured`), and the summary line names the setting; content kept
            in external storage (a `_ref` attribute) is counted as `content_by_reference` and not read. A trace with no
            order text is refused with the `--commitment` hint.
  agent     `gen_ai.agent.name`, else `gen_ai.agent.id`, on the span or the nearest span above it; a name never declares
            one.
  final     the root's output when it has one; otherwise the leaf step that ends last. `delivered` is null when that
            step's content was not captured.
Standard library only; nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .common import Node, add_arguments as common_arguments, assemble, convert_traces, summary
from .langfuse import Scrub, empty, parse_io, text_of, user_text

AGENT_KEYS = ("gen_ai.agent.name", "gen_ai.agent.id")
INPUT_KEYS = ("gen_ai.input.messages", "gen_ai.tool.call.arguments")
OUTPUT_KEYS = ("gen_ai.output.messages", "gen_ai.tool.call.result")
CONTENT_KEYS = INPUT_KEYS + OUTPUT_KEYS + ("gen_ai.system_instructions",)
SETTING = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT"
NOT_CAPTURED = (f"content not captured: OpenTelemetry instrumentations record message content only when {SETTING} is "
                f"SPAN_ONLY or SPAN_AND_EVENT (default NO_CONTENT)")


# ---------- reading ----------
def any_value(v: Any) -> Any:
    """An OTLP `AnyValue` as a Python value."""
    if not isinstance(v, dict):
        return v
    if "stringValue" in v:
        return v["stringValue"]
    if "boolValue" in v:
        return v["boolValue"]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "arrayValue" in v:
        return [any_value(x) for x in (v["arrayValue"] or {}).get("values") or []]
    if "kvlistValue" in v:
        return {kv.get("key"): any_value(kv.get("value")) for kv in (v["kvlistValue"] or {}).get("values") or []
                if isinstance(kv, dict)}
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def attributes(span: Dict[str, Any]) -> Dict[str, Any]:
    return {a["key"]: any_value(a.get("value")) for a in span.get("attributes") or [] if isinstance(a, dict) and "key" in a}


def load_file(path: str) -> List[Dict[str, Any]]:
    """The spans of one file of `TracesData` objects."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        data: Any = json.loads(text)
        docs = data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        docs = []
        for n, ln in enumerate(text.splitlines(), 1):  # JSON Lines, one TracesData per line
            if ln.strip():
                try:
                    docs.append(json.loads(ln))
                except json.JSONDecodeError as e:
                    raise ValueError(f"{path}: line {n} is not JSON ({e.msg}); an OTLP file is JSON or JSON Lines") from None
    spans: List[Dict[str, Any]] = []
    for i, d in enumerate(docs, 1):
        if not isinstance(d, dict) or not isinstance(d.get("resourceSpans"), list):
            raise ValueError(f"{path}: item {i} is not a TracesData object (no `resourceSpans` list; SPEC §8.4)")
        for rs in d["resourceSpans"]:
            for ss in (rs.get("scopeSpans") or []) if isinstance(rs, dict) else []:
                spans.extend(s for s in ((ss.get("spans") or []) if isinstance(ss, dict) else []) if isinstance(s, dict))
    for i, s in enumerate(spans):
        for k in ("spanId", "traceId"):
            if empty(s.get(k)):
                raise ValueError(f"{path}: span {i} ({s.get('name')!r}) has no `{k}`; every OTLP span carries one (SPEC §8.4)")
    return spans


def nanos(s: Dict[str, Any], key: str) -> int:
    v = s.get(key)
    if empty(v):
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"span {s.get('spanId')}: `{key}` is {v!r}, not nanoseconds since the epoch") from None


def iso(ns: int) -> Optional[str]:
    return (dt.datetime(1970, 1, 1) + dt.timedelta(microseconds=ns // 1000)).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if ns else None


def messages_text(v: Any) -> Tuple[str, Optional[Dict[str, Any]], bool]:
    """(text, outputs when a mapping, dotted) of `gen_ai.output.messages`: the content of each message's text parts,
    one message to a line; a JSON object there is read as one; no text part at all keeps the messages as §1.8 lines."""
    v = parse_io(v)
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list):
        return text_of(v)
    lines = []
    for m in v:
        parts = m.get("parts") if isinstance(m, dict) else None
        texts = [str(p["content"]) for p in parts if isinstance(p, dict) and p.get("type") == "text"
                 and not empty(p.get("content"))] if isinstance(parts, list) else []
        if texts:
            lines.append("\n".join(texts))
    if not lines:
        return text_of({"messages": v})
    joined = "\n".join(lines)
    parsed = parse_io(joined)
    return text_of(parsed) if isinstance(parsed, dict) else (joined, None, False)


def to_node(s: Dict[str, Any], i: int) -> Node:
    at = attributes(s)
    op = at.get("gen_ai.operation.name")
    captured = any(not empty(at.get(k)) for k in CONTENT_KEYS)
    out_key = next((k for k in OUTPUT_KEYS if not empty(parse_io(at.get(k)))), None)
    if out_key == "gen_ai.output.messages":
        text, mapping, dotted = messages_text(at[out_key])
    elif out_key:
        text, mapping, dotted = text_of(parse_io(at[out_key]))
    else:
        text, mapping, dotted = "", None, False
    user = user_text(at.get("gen_ai.input.messages"))
    agent = next(((str(at[k]).strip(), k) for k in AGENT_KEYS if isinstance(at.get(k), (str, int)) and str(at[k]).strip()), None)
    start, end = nanos(s, "startTimeUnixNano"), nanos(s, "endTimeUnixNano")
    return Node(id=str(s["spanId"]), parent=str(s["parentSpanId"]) if not empty(s.get("parentSpanId")) else None,
                name=str(s.get("name") or ""), kind=str(op) if op else "span", start=start, end=end, line=iso(start),
                order=(start, end, i), task=user, user=user,
                has_input=any(not empty(parse_io(at.get(k))) for k in INPUT_KEYS), text=text, outputs=mapping,
                has_output=out_key is not None, dotted=dotted, step=bool(op) or captured, captured=captured, agent=agent,
                extra={"by_reference": any(k.startswith("gen_ai.") and k.endswith("_ref") for k in at)})


def read_trace(spans: List[Dict[str, Any]], scrub: Scrub) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(meta, links) for one trace's spans, link 0 the order."""
    tid = str(spans[0]["traceId"])
    nodes = [to_node(s, i) for i, s in enumerate(spans)]
    meta, links = assemble(nodes, scrub, trace_id=tid, siblings="ordered by start time (startTimeUnixNano)")
    by_id: Dict[str, Node] = {}
    for n in nodes:
        by_id.setdefault(n.id, n)  # a repeated span id keeps its first row, as in assemble()
    missing = [l["n"] for l in links[1:] if l.get("content") == "not captured"]
    meta["content_not_captured"], meta["content_not_captured_at_links"] = len(missing), missing
    meta["content_setting"] = SETTING
    meta["content_by_reference"] = sum(1 for n in by_id.values() if n.extra.get("by_reference"))
    meta["spans_not_steps"] = sum(1 for n in by_id.values() if not n.step)
    return meta, links


def read_exports(paths: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    traces: Dict[str, List[Dict[str, Any]]] = {}
    for p in paths:
        for s in load_file(p):
            traces.setdefault(str(s["traceId"]), []).append(s)
    return traces


def convert(exports: List[str], out: Optional[str] = None, *, commitment: Optional[str] = None,
            prefix: Optional[str] = None, scrub: bool = True) -> List[Tuple[str, Dict[str, Any]]]:
    """Read the export files, then write one stage folder per trace under `out`; every trace is read before anything
    is written. Returns (run name, meta) per trace, by trace id."""
    traces = read_exports(exports)
    return convert_traces({t: (lambda sc, spans=spans: read_trace(spans, sc)) for t, spans in traces.items()}, exports,
                          out, commitment=commitment, prefix=prefix, scrub=scrub, source="OTLP JSON",
                          no_order_hint=f" ({NOT_CAPTURED})")


def notes(meta: Dict[str, Any]) -> List[str]:
    out = []
    if meta["content_not_captured"]:
        out.append(f"content not captured on {meta['content_not_captured']} of {meta['links_after_order']} links "
                   f"(set {SETTING}=SPAN_ONLY; the default NO_CONTENT records none)")
    if meta["content_by_reference"]:
        out.append(f"content in external storage on {meta['content_by_reference']} spans (_ref attributes, not read)")
    return out


# ---------- command line: as-agreed import otlp ----------
def add_arguments(p: argparse.ArgumentParser) -> None:
    common_arguments(p, "an OTLP JSON span file: TracesData as JSON, or JSON Lines with one TracesData per line")


def run_cli(a: argparse.Namespace) -> int:
    for name, meta in convert(a.exports, a.out, commitment=a.commitment, prefix=a.prefix, scrub=not a.no_scrub):
        print(summary(os.path.join(a.out, name), meta, notes(meta)))
    return 0
