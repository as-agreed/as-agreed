"""The planted-failure demo (SPEC §11): a change that passes every schema and type check and is held before the commit.

Two fixtures. `widened`, the default: the agreed limit on the amount is relaxed, the costly case. `dropped`: a binding
commitment never reaches the commit, and the two directions meet there, beside a claim hardened on the way up.
The second attempt's output is scripted (`03-commit-retry.json`), and the transcript says so: nothing here is a model
repairing itself. No model call; nothing leaves the machine.
"""
from __future__ import annotations

import json
import re
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from . import NAME, __version__, sdk
from .adapters.plain import PlainAdapter
from .drift import UNDECLARED, describe
from .errors import DriftHold
from .importers.stages import declared_commitments

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.normpath(os.path.join(HERE, "..", "..", "examples"))
if not os.path.isdir(os.path.join(EXAMPLES, "planted-widened")):
    EXAMPLES = os.path.join(HERE, "examples")  # installed from the wheel
FIXTURES = {"widened": "planted-widened", "dropped": "planted-failure"}
SCHEMA = {"amount": float, "po": str, "approved": bool}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class PaymentSystem:
    """Stands in for the system of record; records every call so the transcript can say whether it was reached."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def submit(self, note: Dict[str, Any], *, idempotency_key: str) -> None:
        self.calls.append({"key": idempotency_key, "amount": note.get("amount")})


def run_demo(fixture: str = "widened", *, retry: bool = True, out_dir: Optional[str] = None, quiet: bool = False) -> int:
    folder = os.path.join(EXAMPLES, FIXTURES[fixture])
    out = out_dir or tempfile.mkdtemp(prefix="as-agreed-demo-")
    started = time.time()

    def say(s: str = "") -> None:
        if not quiet:
            print(s)

    say(f"{NAME} {__version__}  {os.path.relpath(folder)}  tier 1 and the claim ledger, no model call; retry output scripted")
    say()
    ad = PlainAdapter(run_dir=out, pipeline=FIXTURES[fixture])
    order = _read(os.path.join(folder, "01-order.md"))
    agreed = sdk.register(order, declared_commitments(folder), source="01-order.md", pipeline=FIXTURES[fixture], adapter=ad)
    binding = sum(1 for c in agreed.commitments if c.binding)
    say(f"registered as-agreed {agreed.id} v{agreed.version} from 01-order.md: {len(agreed.commitments)} commitment(s) "
        f"({binding} binding), {len(agreed.claims)} claim(s)")
    for c in agreed.commitments:
        say(f"  {c.commitment_id}: {c.subject}")
        for path, key in (c.carried_by or {}).items():
            say(f"    {path} {describe(c.value_at(path))}, carried by the output key {key}")
        for crit in c.acceptance_criteria:
            if crit.verification == "machine_checkable":
                say(f"    acceptance_criteria.{crit.criterion_id} reads the output key {crit.field}")

    say("  commitment lines compare declared fields; claim lines compare a sentence's status words, never its numbers")
    research_text = _read(os.path.join(folder, "02-research.md"))

    @sdk.step("research", agreed)
    def research(order_text: str, reason: Optional[List[str]] = None) -> str:
        return research_text

    research(order)
    say(_research_line(ad, agreed, "research#1@1"))

    payments = PaymentSystem()
    first = json.loads(_read(os.path.join(folder, "03-commit.json")))
    second = json.loads(_read(os.path.join(folder, "03-commit-retry.json")))

    @sdk.step("commit", agreed, commit=payments.submit, schema=SCHEMA, retries=1 if retry else 0)
    def prepare_payment(note: str, reason: Optional[List[str]] = None) -> Dict[str, Any]:
        return second if reason else first

    code = 0
    try:
        prepare_payment(research_text)
    except DriftHold:
        code = 1
    schema_line = "schema  " + " | ".join(f"{k}: {t.__name__} ok" for k, t in SCHEMA.items()) + "   -> schema: PASS"
    gate: List[str] = []
    for rec in ad.records():
        if rec.get("kind") == "hold" and rec.get("link", "").startswith("commit#"):
            say(f"{rec['link']}    {schema_line}")
            for ln in _field_lines(ad, agreed, rec["link"]):
                say("             " + ln)
            if rec.get("passed"):
                gate = [f"gate: pass at {rec['link']}    commit hook called {len(payments.calls)} time(s)"
                        + (f" with key {payments.calls[0]['key']}" if payments.calls else "")]
            elif rec["attempt"] <= (1 if retry else 0):
                say(f"retry {rec['attempt']} of {1 if retry else 0}  reason re-injected ({len(rec['lines'])} line(s)); retry output scripted")
            else:
                gate = [f"gate: HOLD ({len(rec['lines'])} reason(s))    outputs discarded; the payment system was "
                        f"{'never ' if not payments.calls else ''}called    code 1"] + ["  " + d for d in rec["lines"]]
    rep = sdk.report(agreed, out, title=f"Where did the commitments go? {FIXTURES[fixture]}", quiet=True)
    say("counts:")
    for ln in rep.count_lines:
        if ln.startswith(("commitment fidelity", "verdicts", "retries")):
            say("  " + ln)
    for ln in gate:
        say(ln)
    for ln in rep.flag_lines:
        say(ln)
    for ln in rep.tail_lines[:-1]:
        say(ln)
    say(f"report: ledger.jsonl, counts.csv, flow.svg written to {out}    code {code}    ({time.time() - started:.2f} s)")
    return code


def _verdicts(ad: PlainAdapter, link: str) -> List[dict]:
    return [r for r in ad.records() if r.get("kind") == "verdict" and r.get("link") == link]


def _research_line(ad: PlainAdapter, agreed: Any, link: str) -> str:
    vs = _verdicts(ad, link)
    fields = [v for v in vs if v["unit_kind"] == "commitment"]
    und = sum(1 for v in fields if v.get("undecided") == UNDECLARED)
    cs = [v for v in vs if v["unit_kind"] == "claim"]
    found = [r for r in ad.records() if r.get("kind") == "claim" and r.get("found_at") == link]
    classes: Dict[str, int] = {}
    for c in found:
        for k, yes in (("hedged", c["origin"] == "hedged"), ("dated", bool(c["markers"].get("temporal"))),
                       ("sourced", c["markers"].get("provenance") not in (None, "none stated"))):
            classes[k] = classes.get(k, 0) + (1 if yes else 0)
    return (f"{link}  prose: {und} of {len(fields)} fields undeclared, nothing to compare   claims {len(cs)} checked; "
            f"found {len(found)} new (hedged {classes.get('hedged', 0)}, dated {classes.get('dated', 0)}, "
            f"sourced {classes.get('sourced', 0)}); as-agreed now v{agreed.version}")


def _status_words(u: Any) -> str:
    """What a claim line compares, in the field column: the status words the claim carried, never its values. A claim
    with none is matched by shared words only, so its PRESERVED says the sentence arrived, not that its numbers did."""
    m = u.markers
    carried = [name for name, yes in (("hedge", bool(m.get("epistemic"))),
                                      ("not-found", m.get("existential") not in (None, "value_present")),
                                      ("source", m.get("provenance") not in (None, "none stated")),
                                      ("date", bool(m.get("temporal")))) if yes]
    return "status: " + ", ".join(carried) if carried else "words only, no status"


def _field_lines(ad: PlainAdapter, agreed: Any, link: str) -> List[str]:
    out: List[str] = []
    silent: Dict[str, List[str]] = {}
    for r in _verdicts(ad, link):
        if r["unit_kind"] == "commitment":
            if r.get("undecided") == UNDECLARED:
                silent.setdefault(r["unit"], []).append(r["field"])
                continue
            out.append(f"{'commitment ' + r['unit']:<27} {r['field']:<28} {(r.get('trajectory') or r.get('drift_class') or r.get('undecided')):<18} {r['reason']}")
            continue
        u = agreed.claim(r["unit"])
        if u is None:
            continue
        tag = f"claim [{u.origin}{', must' if u.must else ''}]"
        label = r.get("trajectory") or r["status"]
        if r["status"] == "DROPPED":
            label = "DROPPED!" if u.must else "dropped (reported)"
        detail = ""
        if r.get("marker_fates") and r["status"] in ("CHANGED", "DANGER"):
            detail = ": " + ", ".join(f"{k} {x}" for k, x in r["marker_fates"].items() if x not in ("n/a", "kept", "unknown"))
        out.append(f"{tag:<27} {_status_words(u):<28} {label:<18} \"{u.text[:84]}\"{detail}")
    return [f"{'commitment ' + unit:<27} undeclared here: {', '.join(fields)}" for unit, fields in silent.items()] + out


def run_capture_demo(quiet: bool = False) -> int:
    """`as-agreed demo --capture` (0.1.3): the invented record in `examples/capture`, drafted with the person's words for
    each term and one wrong date; its mark before the confirm; the fixed record confirmed; the hook's check, then its
    stop after a change. It works in a new temporary folder, removed at the end, and reads in under a minute."""
    import shutil
    from . import capture
    from .hook import decide

    def say(s: str = "") -> None:
        if not quiet:
            print(s)

    work = tempfile.mkdtemp(prefix="as-agreed-capture-")
    folder = os.path.join(work, ".as-agreed")
    record = os.path.join(folder, "record.json")
    try:
        shutil.copytree(os.path.join(EXAMPLES, "capture", ".as-agreed"), folder)
        rec = json.loads(_read(record))
        binding = json.loads(_read(os.path.join(folder, "binding.json")))
        lines = capture.show_lines(rec, "record.json", binding)
        first = capture.marks(rec, binding)
        say(f"{NAME} {__version__} capture demo, an invented order; nothing is sent")
        say(lines[1])
        say()
        say("1. The agent's record gives each term the person's words (`said`). `as-agreed show`:")
        say("   " + lines[0])
        marked = {m.unit for m in first}
        block, keep = [], False
        for line in lines[2:]:
            if not line.startswith(" ") and line:
                keep = line.split()[0] in marked
            if keep:
                block.append(line)
        while block and not block[-1]:
            block.pop()
        for line in block:
            say("   " + line)
        say(f"   The other {len(rec['commitments']) - len(marked)} commitments: 0 marks.")
        for m in first:  # the agent fixes what the person's words say
            if m.path == "deadline":
                next(c for c in rec["commitments"] if c["commitment_id"] == m.unit)["deadline"] = "2031-02-20"
        with open(record, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
        after = capture.marks(json.loads(_read(record)), binding)
        say(f"2. The agent sets the date from the words. `as-agreed show`: {len(after)} marks.")
        out = capture.write_confirm(folder, "the person")
        say("3. The person runs `as-agreed confirm` in their own terminal. It reads the name there, never from stdin,")
        say(f"   and writes the name, the time and two hashes ({out['confirmed_sha256'][:12]}, written here by the demo).")
        call = {"session_id": "demo", "tool_name": "Write",
                "tool_input": {"file_path": "project/outbox/booking.json",
                               "content": json.dumps({"guests": 14, "arrive": "2031-03-12", "leave": "2031-03-14",
                                                      "total": 9200})}}
        passed, pass_lines, _ = decide(call, work)
        kept = re.search(r"(\d+ of \d+) binding terms", pass_lines[0] if pass_lines else "")
        say(f"4. A booking within the terms: {passed}, {kept.group(1) if kept else 'no'} terms kept.")
        changed = json.loads(_read(record))
        changed["commitments"][1]["limit"]["spend"]["max"] = 12000
        with open(record, "w", encoding="utf-8") as f:  # a script, not a file tool: the hash shows it
            f.write(json.dumps(changed, indent=1, ensure_ascii=False) + "\n")
        stopped, stop_lines, _ = decide(call, work)
        first_stop = (stop_lines[0] if stop_lines else "").split(" (")[0].replace("as-agreed: ", "")
        say("5. A script raises the spend limit in record.json to 12,000. The same booking:")
        say(f"   {stopped}: {first_stop}.")
        ok = len(first) == 1 and not after and passed == "pass" and stopped == "refused"
        say()
        say(f"demo: {len(first)} mark before the confirm, 0 after the fix; the booking passed, then after the change it "
            f"was {stopped}" if ok else "demo: the capture demo did not run as expected")
        return 0 if ok else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
