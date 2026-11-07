"""The conformance suite of SPEC §10, as code: one sentence, one function, run through an adapter's binding.

    python3 -m as_agreed conformance            # the plain adapter
    python3 -m as_agreed conformance plain      # an adapter registered by name
    python3 -m as_agreed conformance langgraph  # the LangGraph adapter, where langgraph is installed

A third-party adapter runs the suite from its own code: `run_suite(my_factory)` with
`my_factory(run_id=None, pipeline=None) -> Adapter`. No module is loaded by name here, so the
repository's no-network test keeps its word for this file too.

Sentences marked for one adapter run there only and are skipped by name elsewhere; the import sentences
I3 to I5 are deferred with the importers (the P6 test of 2026-11-02) and skipped by name until then.
The socket guard of K13 is supplied by the caller (the tests pass one); the suite itself opens nothing.
The Temporal suite runs inside an activity, from `tests/test_adapter_temporal.py`: the engine parts of A4, A6 and
T5 need workflow code, which that module runs on the test server and hands in as `engine=` (what it observed);
the sentences assert on it here. Without it those parts are skipped by name.
"""
from __future__ import annotations

import ast
import copy
import csv
import io
import json
import os
import re
import tempfile
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import ledger, sdk
from ..adapters.base import Adapter
from ..adapters.plain import PlainAdapter
from ..commitment import build
from ..drift import TIER1_CLASSES
from ..importers.stages import Link, links_to_run, run_folder

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.dirname(HERE)
# the same two kinds of module the repository's no-network test refuses: network, and loaders or process starters
NETWORK = {"socket", "ssl", "urllib", "http", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "telnetlib",
           "xmlrpc", "websocket", "websockets", "urllib3", "grpc", "imaplib", "poplib", "nntplib", "socketserver",
           "asyncio", "webbrowser", "subprocess", "multiprocessing", "pty", "ctypes", "importlib", "posix", "nt",
           "_posixsubprocess", "runpy", "pkgutil", "zipimport", "concurrent"}

ORDER = ("Pay the October invoice from Northwind Freight, up to 18,400 dollars, only if it matches purchase order 4471.\n"
         "Finance must confirm the vendor's new bank details by phone before any payment [!].\n")
PLAIN_ORDER = "Pay the October invoice from Northwind Freight, up to 18,400 dollars, only if it matches purchase order 4471.\n"
RESEARCH = ("The invoice total is 18,400 dollars, per the invoice PDF.\n"
            "Purchase order 4471 lists 18,400 dollars, per the procurement export of 2026-10-27.\n"
            "The vendor changed its bank details on 2026-10-20, so finance must confirm the vendor's new bank details by phone before any payment.\n"
            "The contract probably allows early payment, but clause 7.2 was not found in the copy we have.\n")
COMMIT_BAD = {"amount": 19800.0, "po": "4471", "approved": True,
              "note": "Invoice matches purchase order 4471 at 19,800 dollars. The contract allows early payment under clause 7.2. Approved for early payment today."}
COMMIT_GOOD = {"amount": 18400.0, "po": "4471", "approved": True, "phone_confirmed": True,
               "note": "Invoice matches purchase order 4471 at 18,400 dollars, per the procurement export of 2026-10-27. Finance must confirm the vendor's new bank details by phone before any payment. The contract probably allows early payment, but clause 7.2 was not found in the copy we have. Approved for early payment today."}
SCHEMA = {"amount": float, "po": str, "approved": bool}
PAY = "pay the October invoice from Northwind Freight"
PHONE = "confirm the vendor's new bank details by phone before any payment"
# one commitment with every declared field, for the sentences that compare records field by field
FULL = {"act": PAY, "obligor": "finance-ops", "obligee": "northwind-freight", "modality": "MUST", "condition": {"po": "4471"},
        "limit": {"amount": {"max": 18400, "unit": "USD"}, "audience": {"in": ["finance", "legal"]},
                  "blocked": {"not_in": ["sanctioned"]}, "floor": {"min": 100}, "window": {"until": "2026-12-31"}},
        "deadline": "2026-11-20", "purpose": "keep the vendor's terms",
        "acceptance_criteria": [{"criterion_id": "signed", "text": "the controller signs the payment off"}]}
DROP = object()


class NotHere(Exception):
    """Raised by a sentence whose engine part cannot run in this environment: the sentence is skipped by name."""


@dataclass
class Result:
    id: str
    status: str   # pass | fail | skip
    message: str


SENTENCES: Dict[str, Tuple[Callable, Any]] = {}
DEFERRED = {"I3": "deferred with the Langfuse importer (P6, 2026-11-02)",
            "I4": "deferred with the LangSmith importer (P6, 2026-11-02)",
            "I5": "deferred with the OTLP importer (P6, 2026-11-02)"}


def sentence(sid: str, adapter: Any = None) -> Callable:
    """`adapter` names the one binding a sentence runs on, or a tuple of them; None runs it everywhere."""
    def deco(fn: Callable) -> Callable:
        SENTENCES[sid] = (fn, adapter)
        return fn
    return deco


FACTORIES: Dict[str, Callable[..., Adapter]] = {}


def register_factory(name: str, factory: Callable[..., Adapter]) -> None:
    """Make an adapter factory available to the command line by name."""
    FACTORIES[name] = factory


class Env:
    def __init__(self, factory: Callable[..., Adapter], guarded: bool = False, engine: Optional[dict] = None):
        self.factory = factory
        self.binding = factory().binding
        self.skipped_shared: List[str] = []
        self.ran: List[str] = []
        self.guarded = guarded
        self.engine = engine  # what an adapter's test module observed on the engine itself (A4, A6, T5)

    def outputs(self, result: Any) -> Any:
        """A passing step's outputs, read through the binding (an Outcome on Temporal)."""
        return self.binding.outputs_of(result)

    def adapter(self, **kw: Any) -> Adapter:
        return self.factory(**kw)

    def code(self, call: Callable[[], Any]) -> int:
        try:
            return int(self.binding.code_of(call()))
        except Exception as e:  # noqa: BLE001  the binding reads codes off raised errors too
            return int(self.binding.code_of(e))


def load_factory(name: str) -> Callable[..., Adapter]:
    """An adapter factory registered by name; the plain adapter is always there."""
    if not FACTORIES:
        from ..adapters.plain import memory_adapter
        register_factory("plain", memory_adapter)
    if name == "langgraph" and name not in FACTORIES:
        from ..adapters.langgraph import langgraph_adapter  # raises ImportError naming the package when missing
        register_factory("langgraph", langgraph_adapter)
    if name not in FACTORIES:
        raise KeyError(f"no adapter named {name!r}; known: {', '.join(sorted(FACTORIES))}. "
                       f"A third-party adapter runs run_suite(factory) from its own code.")
    return FACTORIES[name]


def run_suite(factory: Callable[..., Adapter], only: Optional[List[str]] = None, guard: Any = None,
              engine: Optional[dict] = None) -> List[Result]:
    """Run the sentences; `guard` is a context manager the caller supplies (the tests pass one that refuses sockets);
    `engine` is what an adapter's own test module observed on its engine, for the sentences that need workflow code."""
    env = Env(factory, guarded=guard is not None, engine=engine)
    results: List[Result] = []
    ids = [i for i in SENTENCES if (only is None or i in only)]
    ids.sort(key=lambda i: (i == "A1", i))  # A1 last: it looks at what the others did
    with (guard if guard is not None else nullcontext()):
        results.extend(_run(env, ids))
    return results


def _run(env: Env, ids: List[str]) -> List[Result]:
    results: List[Result] = []
    for sid in ids:
        fn, adapter_only = SENTENCES[sid]
        if sid in DEFERRED:
            results.append(Result(sid, "skip", DEFERRED[sid]))
            continue
        names = adapter_only if isinstance(adapter_only, tuple) else ((adapter_only,) if adapter_only else ())
        if names and env.binding.name not in names:
            results.append(Result(sid, "skip", f"adapter: {', '.join(names)}; skipped on {env.binding.name}"))
            continue
        try:
            msg = fn(env)
            results.append(Result(sid, "pass", msg or ""))
            env.ran.append(sid)
        except NotHere as e:
            results.append(Result(sid, "skip", str(e)))
        except AssertionError as e:
            results.append(Result(sid, "fail", str(e) or "assertion failed"))
            if not adapter_only:
                env.skipped_shared.append(sid)
        except Exception as e:  # noqa: BLE001
            results.append(Result(sid, "fail", f"{type(e).__name__}: {e}"))
            if not adapter_only:
                env.skipped_shared.append(sid)
    return results


# ---------- helpers ----------
def commitments() -> List[sdk.Commitment]:
    return [sdk.Commitment(PAY, "finance-ops", "northwind-freight", "MUST", commitment_id="pay-invoice",
                           condition={"po": "4471"}, limit={"amount": {"max": 18400, "unit": "USD"}},
                           carried_by={"condition.po": "po", "limit.amount": "amount"}),
            sdk.Commitment(PHONE, "finance-ops", "controller", "MUST", commitment_id="phone-check",
                           acceptance_criteria=[sdk.Criterion("the vendor's new bank details are confirmed by phone",
                                                              "machine_checkable", field="phone_confirmed",
                                                              criterion_id="phone")])]


def registered(env: Env, order: str = ORDER, cs: Optional[List[Any]] = None, **kw: Any):
    ad = env.adapter()
    agreed = sdk.register(order, commitments() if cs is None else cs, adapter=ad, source="order", **kw)
    return ad, agreed


def full(env: Env, **over: Any):
    data = copy.deepcopy(FULL)
    data.update(over)
    return registered(env, PLAIN_ORDER, [sdk.Commitment(commitment_id="c1", **data)])


def brief(**over: Any) -> Dict[str, Any]:
    """A downstream record derived from FULL; `field=DROP` leaves the key out."""
    rec = copy.deepcopy(FULL)
    rec["parent_id"] = "c1"
    for k, v in over.items():
        if v is DROP:
            rec.pop(k, None)
        else:
            rec[k] = v
    return rec


def labels(agreed: Any, outputs: Any, **kw: Any) -> Dict[str, str]:
    return {v.field: v.label for v in sdk.check(outputs, agreed, **kw) if v.unit_kind == "commitment" and v.tier == 1}


def limit_of(dim: str, value: Any) -> Dict[str, Any]:
    lim = copy.deepcopy(FULL["limit"])
    lim[dim] = value
    return brief(limit=lim)


def research(agreed: Any, text: Any = RESEARCH, name: str = "research") -> Any:
    @sdk.step(name, agreed)
    def body(inputs: str, reason: Optional[List[str]] = None) -> Any:
        return text
    return body(ORDER)


class Submit:
    def __init__(self) -> None:
        self.calls: List[str] = []

    def __call__(self, outputs: Any, *, idempotency_key: str) -> None:
        self.calls.append(idempotency_key)


def commit(agreed: Any, outputs: Any, *, retries: int = 0, on_retry: Any = None, submit: Optional[Submit] = None,
           schema: Optional[dict] = SCHEMA, **kw: Any) -> Tuple[Any, Submit, List[int]]:
    sub = submit or Submit()
    calls: List[int] = []

    @sdk.step("commit", agreed, commit=sub, schema=schema, retries=retries, **kw)
    def body(inputs: str, reason: Optional[List[str]] = None) -> Any:
        calls.append(0 if reason is None else len(reason))
        return (on_retry if on_retry is not None else outputs) if reason else outputs

    try:
        result = body(RESEARCH)
    except sdk.AsAgreedError as e:
        result = e
    return result, sub, calls


def verdicts(ad: Adapter, link: str, kind: Optional[str] = None, tier: Any = None) -> List[dict]:
    return [r for r in ad.records() if r.get("kind") == "verdict" and r.get("link") == link
            and (kind is None or r.get("unit_kind") == kind) and (tier is None or r.get("tier") == tier)]


def labels_of(ad: Adapter, link: str, unit: str, field: str) -> List[str]:
    """Every label for one field at one link: a field is decided more than once where a commitment is split."""
    return [v.get("trajectory") or v.get("drift_class") or v.get("undecided") or ""
            for v in verdicts(ad, link) if v["unit"] == unit and v["field"] == field]


def label_of(ad: Adapter, link: str, unit: str, field: str) -> str:
    vs = [v for v in verdicts(ad, link) if v["unit"] == unit and v["field"] == field]
    assert vs, f"no verdict for {unit} {field} at {link}"
    v = vs[0]
    return v.get("trajectory") or v.get("drift_class") or v.get("undecided") or ""


def status_of(ad: Adapter, agreed: Any, link: str, prefix: str) -> str:
    uid = next(c.id for c in agreed.claims if c.text.startswith(prefix))
    vs = [v for v in verdicts(ad, link) if v["unit"] == uid]
    assert vs, f"no verdict for {prefix} at {link}"
    return vs[0]["status"]


def holds(ad: Adapter, link: Optional[str] = None) -> List[dict]:
    return [r for r in ad.records() if r.get("kind") == "hold" and (link is None or r.get("link") == link)]


def tmpdir() -> str:
    return tempfile.mkdtemp(prefix="as-agreed-conf-")


def refusal(env: Env, call: Callable[[], Any]) -> Tuple[int, Optional[BaseException]]:
    """The code the binding reads off a call, and the error it raised (None where it returned a result)."""
    try:
        got = call()
    except Exception as e:  # noqa: BLE001  the binding reads codes off raised errors too
        return int(env.binding.code_of(e)), e
    return int(env.binding.code_of(got)), None


# ---------- Register ----------
@sentence("R1")
def r1(env: Env) -> str:
    a1 = sdk.register(ORDER, commitments(), adapter=env.adapter())
    a2 = sdk.register(ORDER, commitments(), adapter=env.adapter())
    other = commitments()
    other[1] = sdk.Commitment("confirm the bank details by letter", "finance-ops", "controller", "MUST", commitment_id="phone-check")
    a3 = sdk.register(ORDER, other, adapter=env.adapter())
    assert a1.id == a2.id and a1.version == "1", "the same order and commitments in two runs gave different ids"
    assert a1.id != a3.id, "a changed commitment kept the id"
    a4 = sdk.register(ORDER, commitments(), adapter=env.adapter(), version=2)
    assert a4.version == "2" and a4.id != a1.id, "the same order and commitments at major version 2 kept the id of version 1"
    return f"id {a1.id} stable; a changed commitment gives {a3.id}, and major version 2 gives {a4.id}"


@sentence("R2")
def r2(env: Env) -> str:
    ad, agreed = registered(env)
    try:
        sdk.register(ORDER, commitments(), adapter=ad)
        raise AssertionError("a second registration at the same version was accepted")
    except sdk.AsAgreedExists:
        pass
    assert len([r for r in ad.records() if r.get("kind") == "as_agreed"]) == 1, "the first record was replaced"
    v2 = sdk.register(ORDER, commitments(), adapter=ad, version=2)
    assert v2.version == "2"
    return "refused at the same version; version 2 accepted"


@sentence("R3")
def r3(env: Env) -> str:
    try:
        sdk.Commitment(PAY, "finance-ops", "northwind-freight", "SHALL")
        raise AssertionError("a modality outside the ladder was stored")
    except ValueError as e:
        assert "SHALL" in str(e), "the error does not name the modality"
    for field in ("act", "obligor", "obligee"):
        args = {"act": PAY, "obligor": "finance-ops", "obligee": "northwind-freight", "modality": "MUST", field: " "}
        try:
            sdk.Commitment(**args)
            raise AssertionError(f"a commitment without {field} was stored")
        except ValueError as e:
            assert field in str(e), str(e)
    return "ValueError names the modality off the ladder and each missing required field"


@sentence("R4")
def r4(env: Env) -> str:
    ad, agreed = registered(env, "The contract probably allows early payment, but clause 7.2 was not found in the copy we have.", [])
    c = agreed.claims[0]
    assert c.markers["epistemic"] == "probably" and c.markers["existential"] == "not_found", c.markers
    assert c.origin == "hedged"
    return "epistemic probably, existential not_found"


@sentence("R5")
def r5(env: Env) -> str:
    err = io.StringIO()
    with redirect_stderr(err):
        ad, agreed = registered(env, "Finance must confirm the bank details by phone [!].\nThe amount is checked twice [must].\n"
                                     "Pay the vendor on Friday. [!] The invoice total is 18,400 dollars per the PDF.\n", [])
    musts = [c.text for c in agreed.claims if c.must]
    assert "Finance must confirm the bank details by phone." in musts, musts
    assert "The amount is checked twice." in musts, musts
    assert not any("invoice total" in t or "Pay the vendor" in t for t in musts), f"a misplaced mark marked a sentence: {musts}"
    assert not any("[" in c.text for c in agreed.claims), "a mark was left in a claim's text"
    warnings = [r for r in ad.records() if r.get("kind") == "warning"]
    assert warnings and warnings[0].get("line") == 3, f"no warning naming line 3: {warnings}"
    assert "line 3" in err.getvalue(), "no stderr line naming the line"
    return "both marks count before the final punctuation; a misplaced mark warns and marks nothing"


@sentence("R6")
def r6(env: Env) -> str:
    out = tmpdir()
    ad = env.adapter()
    rep = links_to_run([Link("research", RESEARCH), Link("commit", "Approved for early payment today.")],
                       root_input=ORDER, adapter=ad, out_dir=out)
    head = [r for r in ad.records() if r.get("kind") == "as_agreed"][0]
    assert head["proxy"] is True, "proxy flag missing on the record"
    assert any("proxy" in ln for ln in rep.tail_lines), rep.tail_lines
    with open(rep.csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows and all(r["proxy"] == "yes" for r in rows), "proxy column not yes"
    with open(rep.svg_path, encoding="utf-8") as f:
        assert "proxy" in f.read(), "the map's footnote does not name the proxy"
    return "proxy on the record, the tail, the CSV and the map"


@sentence("R7")
def r7(env: Env) -> str:
    try:
        sdk.register("Hi.", [], adapter=env.adapter())
    except sdk.EmptyAsAgreed:
        return "EmptyAsAgreed"
    raise AssertionError("an empty as-agreed record registered")


@sentence("R8")
def r8(env: Env) -> str:
    def stamped(ad: Adapter, *kinds: str) -> int:
        bad = [r for r in ad.records() if not (r.get("format") and r.get("pipeline") and r.get("run_id"))]
        assert not bad, f"records without format, pipeline or run_id: {[r.get('kind') for r in bad]}"
        unwritten = [k for k in kinds if not any(r.get("kind") == k for r in ad.records())]
        assert not unwritten, f"the run wrote no {', '.join(unwritten)} record, so none was looked at"
        return len(ad.records())

    ad, agreed = registered(env)
    research(agreed)
    n = stamped(ad)
    ad2, agreed2 = registered(env)  # a run that retried
    research(agreed2)
    commit(agreed2, COMMIT_BAD, retries=1, on_retry=COMMIT_GOOD)
    n += stamped(ad2, "retry", "hold")
    ad3, agreed3 = registered(env)  # a run in which a tier failed
    research(agreed3)
    commit(agreed3, COMMIT_GOOD, tiers=[sdk.Tier2(boom)])
    n += stamped(ad3, "tier_failure", "hold")
    ad4, agreed4 = registered(env)  # a run that failed its schema
    research(agreed4)
    commit(agreed4, {"po": "4471", "approved": True})
    n += stamped(ad4, "schema_failure")
    return f"{n} records stamped, in a plain run and in runs that retried, lost a tier and failed a schema"


@sentence("R9")
def r9(env: Env) -> str:
    cases = [("a bound with no direction", dict(limit={"amount": 18400}), "amount"),
             ("a bound of the wrong shape", dict(limit={"amount": {"max": "plenty"}}), "amount"),
             ("a deadline that is no date", dict(deadline="next week"), "next week"),
             ("a machine_checkable criterion with no key", dict(acceptance_criteria=[sdk_criterion_args()]), "machine_checkable"),
             ("carried_by naming a field not held", dict(carried_by={"limit.amount": "amount"}), "limit.amount")]
    for what, kw, name in cases:
        try:
            sdk.Commitment(PAY, "finance-ops", "northwind-freight", "MUST", **kw)
            raise AssertionError(f"{what} was stored")
        except ValueError as e:
            assert name in str(e), (what, str(e))
    return f"{len(cases)} typed fields without a decision procedure refused, each by name"


def sdk_criterion_args() -> Dict[str, str]:
    return {"text": "the amount is checked", "verification": "machine_checkable"}


@sentence("R10")
def r10(env: Env) -> str:
    ad, agreed = registered(env, ORDER, [sdk.Commitment(PAY, "finance-ops", "northwind-freight", "MUST")])
    rec = [r for r in ad.records() if r.get("kind") == "commitment"][0]
    fourteen = ("commitment_id", "parent_id", "source", "captured_at", "obligor", "obligee", "act", "modality", "condition",
                "limit", "deadline", "acceptance_criteria", "purpose", "version")
    missing = [k for k in fourteen if k not in rec]
    assert not missing, f"keys missing from the record: {missing}"
    assert rec["parent_id"] is None and rec["captured_at"] and rec["source"]["artifact"] == "order", rec
    research(agreed)
    rep = sdk.report(agreed, tmpdir(), quiet=True)
    line = [ln for ln in rep.tail_lines if ln.startswith("uncheckable")]
    assert line and all(f in line[0] for f in ("condition", "limit", "deadline", "acceptance_criteria")), rep.tail_lines
    return "four fields registered; fourteen keys on the record; the report names what is uncheckable"


@sentence("R11")
def r11(env: Env) -> str:
    c = sdk.Commitment(PAY, "finance-ops", "northwind-freight", "MUST", carried_by={"limit.amount": "amount"},
                       limit={"amount": {"max": 18400}},
                       acceptance_criteria=[sdk.Criterion("use discretion", "unverifiable", criterion_id="discretion")])
    ad, agreed = registered(env, ORDER, [c])
    commit(agreed, COMMIT_GOOD)
    rep = sdk.report(agreed, tmpdir(), quiet=True)
    assert rep.code == 0, rep.gate_line
    assert any(ln.startswith("unverifiable") and "discretion" in ln for ln in rep.tail_lines), rep.tail_lines
    return "recorded unverifiable and reported as a finding; the tool does not fail on it"


# ---------- Check ----------
@sentence("K1")
def k1(env: Env) -> str:
    ad = env.adapter()

    @sdk.step("research", adapter=ad)
    def body(inputs: str, reason: Optional[List[str]] = None) -> str:
        return RESEARCH

    before = len(ad.records())
    code = env.code(lambda: body(ORDER))
    assert code == 2, f"code {code} without a commitment record"
    assert len(ad.records()) == before, "something was written to the sink"
    hand, _ = build(ORDER, commitments(), run_id=ad.run_id, ts="2026-10-31T00:00:00Z")
    hand.adapter = ad

    @sdk.step("research", hand)
    def body2(inputs: str, reason: Optional[List[str]] = None) -> str:
        return RESEARCH

    code2 = env.code(lambda: body2(ORDER))
    assert code2 == 2, f"a hand-built record passed with code {code2}"
    assert len(ad.records()) == before, "a hand-built record wrote to the sink"
    # the step, sdk.check and sdk.hold are three ways in, and each must refuse a record that is not the registered one
    ad0, agreed0 = registered(env)
    verdicts0 = sdk.check(RESEARCH, agreed0)
    assert verdicts0 and all(isinstance(v, sdk.Verdict) for v in verdicts0), "the untouched registered record gave no verdicts"
    assert isinstance(sdk.hold(verdicts0, agreed0), sdk.Hold), "the untouched registered record was not held or passed"
    assert env.outputs(research(agreed0)) == RESEARCH, "the untouched registered record did not run"

    def through(sink: Adapter, record: Any) -> Dict[str, Callable[[], Any]]:
        def step_call() -> Any:
            @sdk.step("research", record, **({"adapter": sink} if record is None else {}))
            def inner(inputs: str, reason: Optional[List[str]] = None) -> str:
                return RESEARCH
            return inner(ORDER)

        return {"a step": step_call, "sdk.check": lambda: sdk.check(RESEARCH, record, adapter=sink),
                "sdk.hold": lambda: sdk.hold(verdicts0, record, adapter=sink)}

    def refused(sink: Adapter, record: Any, what: str, *named: str) -> None:
        for how, call in through(sink, record).items():
            written = len(sink.records())
            code3, err = refusal(env, call)
            assert code3 == 2, f"{how} given {what}: code {code3}"
            if err is not None:  # an engine may hand the refusal back as a result: the code is then all there is to read
                assert isinstance(err, sdk.NoCommitment), f"{how} given {what}: {type(err).__name__}: {err}"
                assert all(n in str(err) for n in named), f"{how} given {what}: the error does not name {', '.join(named)}"
            assert len(sink.records()) == written, f"{how} given {what} wrote to the sink"

    ad1 = env.adapter()
    refused(ad1, None, "no registered record")
    hand1, _ = build(ORDER, commitments(), run_id=ad1.run_id, ts="2026-10-31T00:00:00Z")
    hand1.adapter = ad1
    refused(ad1, hand1, "a record built by hand and never registered")
    agreed0.commitments[0].limit = {"amount": {"max": 99999, "unit": "USD"}}
    refused(ad0, agreed0, "a registered record whose limit was changed in memory", "pay-invoice")
    ad5, agreed5 = registered(env)
    agreed5.commitments = []
    refused(ad5, agreed5, "a registered record whose commitments were cleared", "pay-invoice", "phone-check")
    return ("NoCommitment, code 2, nothing written, for a missing and for a hand-built record, and for a registered one "
            "changed in memory (the commitment that disagrees named), through a step, sdk.check and sdk.hold")


@sentence("K2")
def k2(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    commit(agreed, COMMIT_GOOD)
    vs = [r for r in ad.records() if r.get("kind") == "verdict"]
    assert all(v.get("as_agreed") == agreed.id and v.get("version") for v in vs), "a verdict without the record or its version"
    assert {v["version"] for v in verdicts(ad, "commit#1@1")} == {"1.1"}, "the commit's verdicts do not carry version 1.1"
    return "every verdict names the as-agreed record and its version; 1.1 after the research link"


@sentence("K3")
def k3(env: Env) -> str:
    ad, agreed = full(env)
    got = labels(agreed, {"commitments": [brief()]})
    assert set(got.values()) == {"PRESERVED"}, got
    assert len(got) == len(agreed.commitments[0].field_paths()), got
    loose = labels(agreed, {"commitments": [brief(obligor=" Finance-Ops ", act=PAY.upper() + ".", modality="must")]})
    assert set(loose.values()) == {"PRESERVED"}, loose
    ad2, agreed2 = full(env)
    sent = [r for r in ad2.records() if r.get("kind") == "commitment"]
    forwarded = sdk.check({"commitments": sent}, agreed2)  # the as-agreed records themselves, passed along as they are
    assert not [v for v in forwarded if v.unit_kind == "commitment" and v.drift_class not in ("PRESERVED", None)], forwarded
    return f"{len(got)} fields PRESERVED, on an exact and on a normalised match; a forwarded record is the same commitment"


@sentence("K4")
def k4(env: Env) -> str:
    ad, agreed = full(env)
    got = labels(agreed, {"commitments": [brief(deadline=DROP, obligee=DROP, limit={"amount": FULL["limit"]["amount"]})]})
    assert got["deadline"] == "DROPPED" and got["obligee"] == "DROPPED" and got["limit.audience"] == "DROPPED", got
    assert got["obligor"] == "PRESERVED", got
    whole = labels(agreed, {"commitments": []})
    assert set(whole.values()) == {"DROPPED"} and len(whole) == len(agreed.commitments[0].field_paths()), whole
    return "a field missing downstream is DROPPED; a commitment with no record at the link is DROPPED field by field"


@sentence("K5")
def k5(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed, {"po": "4471", "note": "nothing on the amount"}, "summary")
    assert label_of(ad, "summary#1@1", "pay-invoice", "limit.amount") == "DROPPED"
    research(agreed, {"amount": 18400.0, "po": "4471"}, "detail")
    assert label_of(ad, "detail#1@1", "pay-invoice", "limit.amount") == "restored_verbatim", "no trajectory label after a loss"
    assert label_of(ad, "detail#1@1", "pay-invoice", "condition.po") == "PRESERVED", "a label on a field never lost"
    vs = sdk.check({"amount": 18400.0}, agreed, history=sdk.NO_HISTORY)
    v = next(x for x in vs if x.field == "limit.amount")
    assert v.drift_class == "PRESERVED" and v.trajectory is None and v.history_incomplete, "no history did not give the flag"
    return "restored_verbatim across hops; PRESERVED with history_incomplete when the records are unavailable"


@sentence("K6")
def k6(env: Env) -> str:
    ad, agreed = full(env)
    got = {v.field: v for v in sdk.check({"commitments": [brief(obligor="vendor-desk", obligee="northwind-holdings")]}, agreed)
           if v.unit_kind == "commitment"}
    for field, was, now in (("obligor", "finance-ops", "vendor-desk"), ("obligee", "northwind-freight", "northwind-holdings")):
        v = got[field]
        assert v.drift_class == "REASSIGNED" and was in v.reason and now in v.reason, (v.drift_class, v.reason)
    return "REASSIGNED on either party, with both values in the reason"


@sentence("K7")
def k7(env: Env) -> str:
    ad, agreed = full(env)
    seen = set()
    outputs = [{"commitments": [brief(act="settle the invoice"), brief(obligor="other", deadline="2026-12-01")]},
               {"commitments": []}, {"commitments": [limit_of("amount", {"max": 1, "unit": "USD"})]}, RESEARCH,
               {"amount": 18400.0}, {"commitments": [brief(modality="MAY", purpose="something else")]}]
    for o in outputs:
        for v in sdk.check(o, agreed, history={"c1/act": "DROPPED"}):
            if v.unit_kind == "commitment":
                assert v.tier == 1 and v.drift_class in TIER1_CLASSES + (None,), (v.field, v.drift_class)
                assert v.trajectory != "restored_equivalent", v.field
                seen.add(v.label)
    reworded = labels(agreed, {"commitments": [brief(act="settle the invoice")]})
    assert reworded["act"] == "ESCALATED", reworded
    return f"labels seen: {sorted(seen)}; a reworded act escalates; RESTATED and restored_equivalent never appear"


@sentence("K8")
def k8(env: Env) -> str:
    ad, agreed = registered(env, "The enterprise SLA is not confirmed by the vendor's account team.\n"
                                 "We could not find a published price list for the enterprise tier.\n"
                                 "The vendor probably moved its billing to usage-based pricing in March.", [])
    assert len(agreed.claims) == 3, [c.text for c in agreed.claims]
    vs = {agreed.claim(v.unit).text[:12]: v for v in sdk.check(
        "The enterprise SLA is confirmed by the vendor's account team.\n"
        "There is no published price list for the enterprise tier.\n"
        "The vendor moved its billing to usage-based pricing in March.", agreed)}
    a = vs["The enterpri"]
    assert a.status == "DANGER" and a.marker_fates["epistemic"] == "hardened", (a.status, a.marker_fates)
    b = vs["We could not"]
    assert b.status == "DANGER" and b.marker_fates["existential"] == "hardened", (b.status, b.marker_fates)
    c = vs["The vendor p"]
    assert c.status == "CHANGED" and c.marker_fates["epistemic"] == "dropped", (c.status, c.marker_fates)
    return "negated verification stated flat: hardened; 'not found' as 'there is no': existential hardened; plain hedge lost: CHANGED"


@sentence("K9")
def k9(env: Env) -> str:
    ad, agreed = registered(env, "The onboarding team has twelve people as of 2026-09-30 per the careers page.", [])
    v = sdk.check("The onboarding team has twelve people.", agreed)[0]
    assert v.status == "CHANGED" and v.marker_fates["provenance"] == "dropped" and v.marker_fates["temporal"] == "dropped", (v.status, v.marker_fates)
    return "CHANGED, not DANGER"


@sentence("K10")
def k10(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    flat = "The contract allows early payment under clause 7.2."
    research(agreed, flat, "summary")
    research(agreed, flat, "copy")
    assert status_of(ad, agreed, "summary#1@1", "The contract probably") == "DANGER"
    assert status_of(ad, agreed, "copy#1@1", "The contract probably") == "DANGER", "the copy of the hardened text passed"
    return "hardened at the summary and at its verbatim copy"


@sentence("K11")
def k11(env: Env) -> str:
    ad, agreed = full(env)
    tier = sdk.Tier2(lambda e: {"drift_class": "RESTATED", "confidence": 0.95, "reason": "same act in other words",
                                "tier": 1, "version": "9", "as_agreed": "x"} if e.field == "act" else None)
    vs = sdk.check({"commitments": [brief(act="settle the October invoice")]}, agreed, tiers=[tier])
    first = [v for v in vs if v.tier == 1 and v.unit_kind == "commitment"]
    second = [v for v in vs if v.tier == 2]
    assert len(first) == len(agreed.commitments[0].field_paths()), "a tier 1 verdict is missing"
    assert [v.undecided for v in first if v.field == "act"] == ["ESCALATED"], "tier 2 changed tier 1's row"
    assert len(second) == 1 and second[0].drift_class == "RESTATED", second
    assert second[0].version == agreed.version and second[0].as_agreed == agreed.id, "the callable's own fields were kept"
    return "tier 2's verdict stamped by the core; every tier 1 row kept"


def boom(escalation: Any) -> Any:
    raise TimeoutError("model timed out")


@sentence("K12")
def k12(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    vs = sdk.check(RESEARCH, agreed, tiers=[sdk.Tier2(boom)])
    assert len([v for v in vs if v.tier == 1]) == sum(len(c.field_paths()) for c in agreed.commitments)
    result, sub, calls = commit(agreed, COMMIT_GOOD, tiers=[sdk.Tier2(boom)])
    tf = [r for r in ad.records() if r.get("kind") == "tier_failure"]
    assert tf and "TimeoutError" in tf[-1]["error"], tf
    assert env.binding.is_hold(result), "the commit proceeded although the configured tier failed"
    assert any("tier 2 failed" in ln for ln in holds(ad, "commit#1@1")[0]["lines"])
    return "tier 1 verdicts stand; tier_failure recorded; the commit held under the default policy"


@sentence("K13")
def k13(env: Env) -> str:
    # the socket part: the caller's guard (the tests refuse every socket for the whole suite); this file imports
    # neither socket nor a loader, so the repository's no-network scan covers it as a shipped file
    ad, agreed = registered(env)
    research(agreed)
    commit(agreed, COMMIT_GOOD)
    offenders = []
    for root, _, files in os.walk(PACKAGE):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module.split(".")[0]]
                bad = sorted(set(names) & NETWORK)
                if bad:
                    offenders.append(f"{os.path.relpath(path, PACKAGE)}: {bad}")
    assert not offenders, offenders
    return ("no network module imported in the package; "
            + ("the whole suite ran under the caller's socket guard" if env.guarded
               else "run from the tests for the socket guard (no guard supplied here)"))


@sentence("K14")
def k14(env: Env) -> str:
    ladder = ["MUST", "MUST_NOT", "SHOULD", "MAY"]
    assert list(sdk.MODALITIES) == ladder, sdk.MODALITIES
    ad, agreed = full(env, modality="SHOULD")
    assert labels(agreed, {"commitments": [brief(modality="MUST")]})["modality"] == "STRENGTHENED"
    assert labels(agreed, {"commitments": [brief(modality="MAY")]})["modality"] == "WEAKENED"
    for was in ladder:
        ad, agreed = full(env, modality=was)
        for now in ladder:
            label = labels(agreed, {"commitments": [brief(modality=now)]})["modality"]
            want = "PRESERVED" if was == now else "STRENGTHENED" if ladder.index(now) < ladder.index(was) else "WEAKENED"
            assert label == want, (was, now, label)
    ad, agreed = full(env, modality="MUST")
    v = next(x for x in sdk.check({"commitments": [brief(modality="MUST_NOT")]}, agreed) if x.field == "modality")
    assert v.drift_class == "WEAKENED" and "reversed" in v.reason, (v.drift_class, v.reason)
    return "twelve moves on the ladder decided; a reversal is named in the reason"


@sentence("K15")
def k15(env: Env) -> str:
    ad, agreed = registered(env)
    get = lambda o: labels(agreed, o)
    assert get({"amount": 18400.0})["limit.amount"] == "PRESERVED"
    assert get({"amount": 19800.0})["limit.amount"] == "WIDENED"
    assert get({"po": "4471"})["limit.amount"] == "DROPPED"
    assert get({"amount": 18400.0})["obligor"] == "UNDECLARED", "a field no key carries was decided"
    assert set(get(RESEARCH).values()) == {"UNDECLARED"}, get(RESEARCH)
    ad2, agreed2 = registered(env)
    copied = [r for r in ad2.records() if r.get("kind") == "commitment"]
    result, sub, _ = commit(agreed2, {"commitments": copied, "amount": 19800.0, "po": "4471", "phone_confirmed": True}, schema=None)
    assert env.binding.is_hold(result) and not sub.calls, "a faithful record beside a widened payload passed"
    ad3, agreed3 = registered(env)
    renamed = {"commitments": [r for r in ad3.records() if r.get("kind") == "commitment"],
               "total": 19800.0, "po": "4471", "phone_confirmed": True}
    result3, sub3, _ = commit(agreed3, renamed, schema=None)
    assert env.binding.is_hold(result3) and not sub3.calls, "the number moved to another key removed its check"
    assert "DROPPED" in labels_of(ad3, "commit#1@1", "pay-invoice", "limit.amount"), "the carried key was not read"
    ad4, agreed4 = registered(env)
    research(agreed4)
    held, sub4, _ = commit(agreed4, COMMIT_BAD, retries=1, schema=None,
                           on_retry={"commitments": [r for r in ad4.records() if r.get("kind") == "commitment"],
                                     "total": 19800.0, "po": "4471", "phone_confirmed": True})
    assert env.binding.is_hold(held) and not sub4.calls, "the retry escaped the check by renaming the key"
    return ("the key alone decides where it is carried; a field no key carries, and a prose link, stay UNDECLARED; "
            "a faithful record does not excuse the payload beside it, and a key moved aside is DROPPED, on the "
            "first attempt and on the retry")


@sentence("K16")
def k16(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    bad = dict(COMMIT_BAD, note=COMMIT_BAD["note"] + " The vendor is probably new to us.")
    result, sub, calls = commit(agreed, bad, retries=1, on_retry=COMMIT_GOOD)
    assert not env.binding.is_hold(result), "the retry did not pass"
    assert not any("probably new" in c.text for c in agreed.claims), "a discarded attempt's sentence became a reference"
    return "the held attempt's new hedge is not in the as-agreed record"


@sentence("K17")
def k17(env: Env) -> str:
    ad, agreed = full(env)
    got = lambda dim, value: labels(agreed, {"commitments": [limit_of(dim, value)]})[f"limit.{dim}"]
    assert got("amount", {"max": 19800, "unit": "USD"}) == "WIDENED"
    assert got("amount", {"max": 9000, "unit": "USD"}) == "NARROWED"
    assert got("amount", {"max": "18,400", "unit": "USD"}) == "PRESERVED"
    assert got("floor", {"min": 50}) == "WIDENED"
    assert got("floor", {"min": 200}) == "NARROWED"
    assert got("amount", {"max": 18400, "unit": "EUR"}) == "ESCALATED"
    assert got("amount", {"min": 18400}) == "ESCALATED"
    assert got("amount", 19800) == "WIDENED"
    assert got("amount", 17000) == "PRESERVED"
    assert got("floor", 50) == "WIDENED"
    assert got("amount", float("nan")) == "ESCALATED", "NaN passed as inside the bound"
    assert got("amount", float("inf")) == "ESCALATED"
    assert got("floor", 150) == "PRESERVED"
    # a part in 1e-4 is a change, as a restated bound and as a delivered value: no tolerance
    assert got("amount", {"max": 18401.84, "unit": "USD"}) == "WIDENED", "a bound relaxed by a part in 1e-4 passed"
    assert got("amount", {"max": 18398.16, "unit": "USD"}) == "NARROWED"
    assert got("floor", {"min": 100.01}) == "NARROWED"
    assert got("floor", {"min": 99.99}) == "WIDENED"
    assert got("amount", 18401.84) == "WIDENED", "a delivered value a part in 1e-4 over the bound passed"
    assert got("floor", 99.99) == "WIDENED"
    # a date bound runs both ways, restated and delivered: a later `until` and an earlier `from` relax it
    assert got("window", {"until": "2027-06-30"}) == "WIDENED" and got("window", {"until": "2026-11-30"}) == "NARROWED"
    assert got("window", "2027-06-30") == "WIDENED", "a date delivered after `until` passed"
    assert got("window", "2026-11-30") == "PRESERVED"
    ad2, agreed2 = full(env, limit=dict(FULL["limit"], opens={"from": "2026-11-01"}))
    opens = lambda value: labels(agreed2, {"commitments": [limit_of("opens", value)]})["limit.opens"]
    assert opens({"from": "2026-10-01"}) == "WIDENED", "an earlier `from` did not relax the bound"
    assert opens({"from": "2026-12-01"}) == "NARROWED", "a later `from` did not tighten the bound"
    assert opens({"from": "2026-11-01"}) == "PRESERVED"
    assert opens("2026-10-15") == "WIDENED", "a date delivered before `from` passed"
    assert opens("2026-11-15") == "PRESERVED" and opens("2026-11-01") == "PRESERVED"
    return ("a numeric bound: relaxed WIDENED, tightened NARROWED, with no tolerance; a delivered value is judged against the "
            "bound; a unit the table does not convert escalates (K29); a date bound (`until` and `from`) is decided the same way, restated or delivered")


@sentence("K18")
def k18(env: Env) -> str:
    ad, agreed = full(env)
    got = lambda dim, value: labels(agreed, {"commitments": [limit_of(dim, value)]})[f"limit.{dim}"]
    assert got("audience", {"in": ["finance", "legal", "sales"]}) == "WIDENED"
    assert got("audience", {"in": ["finance"]}) == "NARROWED"
    assert got("audience", {"in": ["legal", "finance"]}) == "PRESERVED"
    assert got("audience", {"in": ["finance", "sales"]}) == "WIDENED", "an addition beside a removal must not pass as narrowed"
    assert got("audience", ["sales"]) == "WIDENED"
    assert got("audience", ["finance"]) == "PRESERVED"
    assert got("blocked", {"not_in": []}) == "WIDENED"
    assert got("blocked", {"not_in": ["sanctioned", "embargoed"]}) == "NARROWED"
    assert got("window", {"until": "2027-06-30"}) == "WIDENED"
    assert got("window", {"until": "2026-11-30"}) == "NARROWED"
    assert got("blocked", "sanctioned") == "WIDENED", "a delivered value inside the exclusion list passed"
    assert got("blocked", ["embargoed", "sanctioned"]) == "WIDENED"
    assert got("blocked", "cleared") == "PRESERVED"
    assert got("region", {"in": ["EU"]}) == "NARROWED", "a bound nobody agreed to was not a narrowing"
    ad2, agreed2 = full(env, limit=dict(FULL["limit"], audience=["legal", "finance"]))  # a list is an allowlist
    listed = [r for r in ad2.records() if r.get("kind") == "commitment"][0]["limit"]["audience"]
    assert listed == {"in": ["finance", "legal"]}, f"a list given at registration was stored as {listed}"
    given = lambda value: labels(agreed2, {"commitments": [limit_of("audience", value)]})["limit.audience"]
    assert given(["finance"]) == "PRESERVED" and given(["sales"]) == "WIDENED", "a list given at registration is not an allowlist"
    return ("a set and a date bound: WIDENED, NARROWED, PRESERVED; an exclusion list runs the other way, for a delivered "
            "value too; a bound nobody agreed to narrows; a list given at registration is an allowlist")


@sentence("K19")
def k19(env: Env) -> str:
    ad, agreed = full(env)
    assert labels(agreed, {"commitments": [brief(deadline="2026-12-20")]})["deadline"] == "RESCHEDULED"
    assert labels(agreed, {"commitments": [brief(deadline="2026-11-01")]})["deadline"] == "RESCHEDULED"
    assert labels(agreed, {"commitments": [brief(deadline="2026-11-20")]})["deadline"] == "PRESERVED"
    assert labels(agreed, {"commitments": [brief(deadline="soon")]})["deadline"] == "ESCALATED"
    ad2, agreed2 = full(env, deadline=None)
    assert labels(agreed2, {"commitments": [brief(deadline="2026-11-20")]})["deadline"] == "RESCHEDULED", \
        "a deadline where none was agreed passed"
    return "a changed date RESCHEDULED either way, and so is a date where none was agreed; not a date, escalated"


@sentence("K20")
def k20(env: Env) -> str:
    ad, agreed = full(env)
    assert labels(agreed, {"commitments": [brief(condition={"po": "4417"})]})["condition.po"] == "RECONDITIONED"
    assert labels(agreed, {"commitments": [brief(condition=DROP)]})["condition.po"] == "RECONDITIONED"
    assert labels(agreed, {"commitments": [brief(condition={"po": "4471", "region": "EU"})]})["condition.region"] == "RECONDITIONED"
    ad, agreed = full(env, condition="only after the audit closes")
    assert labels(agreed, {"commitments": [brief(condition="once the audit is done")]})["condition"] == "ESCALATED"
    assert labels(agreed, {"commitments": [brief(condition="Only after the audit closes.")]})["condition"] == "PRESERVED"
    assert labels(agreed, {"commitments": [brief(condition=DROP)]})["condition"] == "RECONDITIONED"
    ad, agreed = full(env, condition=None)
    assert labels(agreed, {"commitments": [brief(condition="only after the audit closes")]})["condition"] == "RECONDITIONED", \
        "a prose condition added where none was agreed did not recondition"
    return ("a typed condition altered, removed or added is RECONDITIONED, and so is a prose condition added; a prose "
            "condition that differs escalates")


@sentence("K21")
def k21(env: Env) -> str:
    ad, agreed = full(env)
    extra = dict(act="extend the warranty to 36 months", obligor="finance-ops", obligee="northwind-freight", modality="MUST")
    cases = [dict(extra, commitment_id="a1", parent_id=None), dict(extra, commitment_id="a2", parent_id="nope"),
             dict(extra, commitment_id="a3")]
    vs = sdk.check({"commitments": [brief()] + cases}, agreed)
    added = [v for v in vs if v.drift_class == "ADDED_UNPARENTED"]
    assert [v.unit for v in added] == ["a1", "a2", "a3"], added
    ad2, agreed2 = full(env)
    result, sub, calls = commit(agreed2, {"commitments": [brief(), cases[0]]}, schema=None)
    assert env.binding.is_hold(result) and not sub.calls, "an unparented record was accepted silently at the commit"
    carried = dict(brief(), commitment_id="c1", parent_id=None)
    assert not [v for v in sdk.check({"commitments": [carried]}, agreed) if v.drift_class == "ADDED_UNPARENTED"], \
        "the as-agreed commitment carried on with its own id counted as unparented"
    worded = dict(extra, commitment_id="extend the warranty for the vendor", parent_id=None, modality="whatever a model wrote")
    v = next(x for x in sdk.check({"commitments": [worded]}, agreed) if x.drift_class == "ADDED_UNPARENTED")
    assert " " not in v.unit and v.unit_class == "?", (v.unit, v.unit_class)
    return "a null, an unresolved and a missing parent are ADDED_UNPARENTED and held at the commit; a worded id never reaches the files"


@sentence("K22")
def k22(env: Env) -> str:
    ad, agreed = full(env)
    field = "acceptance_criteria.signed"
    same = dict(FULL["acceptance_criteria"][0])
    assert labels(agreed, {"commitments": [brief()]})[field] == "PRESERVED"
    assert labels(agreed, {"commitments": [brief(acceptance_criteria=[dict(same, verification="unverifiable")])]})[field] == "WEAKENED"
    assert labels(agreed, {"commitments": [brief(acceptance_criteria=[])]})[field] == "DROPPED"
    got = labels(agreed, {"commitments": [brief(acceptance_criteria=[same, {"text": "the board also approves"}])]})
    assert "ADDED_UNPARENTED" in got.values(), got
    got = labels(agreed, {"commitments": [brief(acceptance_criteria=["the controller approves the payment"])]})
    assert got[field] == "ESCALATED", got
    up = dict(same, verification="machine_checkable", field="signed_off")  # a flag moved up the ladder is no weakening
    assert labels(agreed, {"commitments": [brief(acceptance_criteria=[up])]})[field] == "STRENGTHENED"
    ad2, agreed2 = full(env, acceptance_criteria=[dict(same, verification="unverifiable")])
    assert labels(agreed2, {"commitments": [brief(acceptance_criteria=[dict(same, verification="human_judged")])]})[field] \
        == "STRENGTHENED", "a flag moved up from unverifiable was not STRENGTHENED"
    ad, agreed = registered(env)
    assert labels(agreed, {"phone_confirmed": False})["acceptance_criteria.phone"] == "WEAKENED"
    assert labels(agreed, {"phone_confirmed": True})["acceptance_criteria.phone"] == "PRESERVED"
    assert labels(agreed, {"po": "4471"})["acceptance_criteria.phone"] == "DROPPED"
    return ("made unverifiable or not met WEAKENED, and a flag moved up the ladder STRENGTHENED; gone DROPPED; a reworded "
            "criterion escalates rather than guess")


@sentence("K23")
def k23(env: Env) -> str:
    ad, agreed = full(env)
    wide = limit_of("amount", {"max": 25000, "unit": "USD"})
    asked: List[Any] = []

    def seen(e: Any) -> Any:
        asked.append((e.link, e.field, e.agreed))
        return None

    @sdk.step("brief", agreed, tiers=[sdk.Tier2(seen)])
    def first(inputs: str) -> Any:
        return {"commitments": [dict(wide, act="settle the invoice")]}

    @sdk.step("handover", agreed, tiers=[sdk.Tier2(seen)])
    def second(inputs: str) -> Any:
        return {"commitments": [dict(wide, act="settle the invoice")]}  # word for word what the last hop wrote

    first(ORDER)
    second("the last hop's text")
    for link in ("brief#1@1", "handover#1@1"):
        assert label_of(ad, link, "c1", "limit.amount") == "WIDENED", f"a widened limit passed at {link}"
    assert [a for a in asked if a[1] == "act"] == [("brief#1@1", "act", PAY), ("handover#1@1", "act", PAY)], asked
    return "every tier compares against the as-agreed record: a copy of a drifted hop is still drifted, and a tier is handed the agreed value"


@sentence("K24")
def k24(env: Env) -> str:
    ad, agreed = full(env)
    asked: List[str] = []

    def t2(e: Any) -> Any:
        asked.append(e.field)
        return {"drift_class": "PRESERVED", "confidence": 1.0, "reason": "no"}

    vs = sdk.check({"commitments": [brief()]}, agreed)
    assert not [v for v in vs if v.field == "purpose"], "a purpose unchanged was given a verdict"
    other = {"commitments": [brief(purpose="win the next contract")]}
    vs = sdk.check(other, agreed, tiers=[sdk.Tier2(t2)])
    purpose = [v for v in vs if v.field == "purpose"]
    assert len(purpose) == 1 and purpose[0].undecided == "ESCALATED" and purpose[0].drift_class is None, purpose
    assert "purpose" not in asked, "tier 2 was asked about the purpose"
    answers = sdk.check(other, agreed, tiers=[sdk.Tier3(lambda e: {"drift_class": "RESTATED", "reason": "a person read it"})])
    assert [v.tier for v in answers if v.field == "purpose" and v.drift_class] == [3], answers
    return "tier 1 and tier 2 never decide the purpose; purpose-only evidence reaches tier 3"


@sentence("K25")
def k25(env: Env) -> str:
    ad, agreed = full(env)
    reworded = {"commitments": [brief(act="settle the October invoice")]}
    low = lambda e: {"drift_class": "RESTATED", "confidence": 0.4, "reason": "unsure"} if e.field == "act" else None
    person = lambda e: {"drift_class": "WIDENED", "reason": "the act now covers more"} if e.field == "act" else None
    vs = sdk.check(reworded, agreed, tiers=[sdk.Tier2(low), sdk.Tier3(person)])
    two = [v for v in vs if v.tier == 2 and v.field == "act"]
    three = [v for v in vs if v.tier == 3 and v.field == "act"]
    assert two and two[0].drift_class is None and two[0].undecided == "ESCALATED", two
    assert three and three[0].drift_class == "WIDENED", three
    vs, tf = _with_failures(agreed, reworded, lambda e: {"drift_class": "RESTATED", "reason": "no confidence"} if e.field == "act" else None)
    assert tf and "confidence" in tf[0]["error"], tf
    assert not [v for v in vs if v.tier == 2], "a verdict without a confidence stood"
    back = sdk.Tier2(lambda e: {"drift_class": "RESTATED", "confidence": 0.9, "reason": "same"} if e.field == "act" else None)
    v = next(x for x in sdk.check(reworded, agreed, tiers=[back], history={"c1/act": "DROPPED"}) if x.tier == 2)
    assert v.trajectory == "restored_equivalent", v.trajectory
    return "RESTATED only from tier 2 with a confidence; below the threshold it goes to tier 3; restored_equivalent is tier 2's"


@sentence("K26")
def k26(env: Env) -> str:
    ad, agreed = full(env, modality="SHOULD")
    outputs = [{"commitments": [brief(modality="SHOULD")]}, {"commitments": [brief(modality="SHOULD", deadline=DROP)]},
               {"commitments": [brief(modality="SHOULD"), dict(brief(), parent_id=None, commitment_id="added")]},
               {"commitments": [dict(limit_of("amount", {"max": 25000, "unit": "USD"}), modality="SHOULD")]},
               {"commitments": [dict(limit_of("amount", {"max": 9000, "unit": "USD"}), modality="SHOULD")]},
               {"commitments": [brief(modality="MUST")]}, {"commitments": [brief(modality="MAY")]},
               {"commitments": [brief(modality="SHOULD", condition={"po": "9"})]},
               {"commitments": [brief(modality="SHOULD", obligor="someone-else")]},
               {"commitments": [brief(modality="SHOULD", deadline="2027-01-01")]}]
    seen = {v.drift_class for o in outputs for v in sdk.check(o, agreed) if v.unit_kind == "commitment" and v.drift_class}
    assert seen == set(TIER1_CLASSES), sorted(set(TIER1_CLASSES) - seen)
    return f"{len(seen)} of {len(TIER1_CLASSES)} classes produced from typed fields, and none but those"


def _with_failures(agreed: Any, outputs: Any, fn: Callable) -> Tuple[List[Any], List[dict]]:
    from ..check import check_link
    ad = agreed.adapter
    return check_link(outputs, agreed, run_id=ad.run_id, link_id="check#1@1", ts=ad.clock(), history={}, tiers=[sdk.Tier2(fn)])


@sentence("K27")
def k27(env: Env) -> str:
    def site(ad: Adapter, link: str, unit: str, field: str) -> Tuple[Optional[str], Optional[str]]:
        v = next(r for r in verdicts(ad, link) if r["unit"] == unit and r["field"] == field)
        return v.get("site"), v.get("site_undecided")

    ad, agreed = registered(env)

    @sdk.step("research", agreed, link_type="compression")
    def squeeze(inputs: str, reason: Optional[List[str]] = None) -> Any:
        return {"amount": 19800.0, "po": "4471"}
    squeeze(ORDER)
    commit(agreed, COMMIT_BAD, link_type="delivery")
    assert site(ad, "research#1@1", "pay-invoice", "limit.amount") == ("seam", None)
    assert site(ad, "commit#1@1", "pay-invoice", "limit.amount") == ("seam", None), "the site moved to the copy"
    assert site(ad, "commit#1@1", "pay-invoice", "condition.po") == (None, None), "a preserved field got a site"
    types = {r["id"]: r.get("link_type", "missing") for r in ad.records() if r.get("kind") == "link"}
    assert types == {"order#1@1": None, "research#1@1": "compression", "commit#1@1": "delivery"}, types
    ad2, agreed2 = registered(env)
    research(agreed2)
    commit(agreed2, COMMIT_BAD, link_type="delivery")
    assert site(ad2, "commit#1@1", "pay-invoice", "limit.amount") == ("sink", None)
    ad3, agreed3 = registered(env)
    research(agreed3)
    result, _, _ = commit(agreed3, COMMIT_BAD)
    assert site(ad3, "commit#1@1", "pay-invoice", "limit.amount") == (None, "ESCALATED")
    assert env.binding.is_hold(result), "the untyped commit did not hold on the widened limit"
    try:
        sdk.step("x", agreed3, link_type="handoff")
        raise AssertionError("an unknown link_type was accepted")
    except ValueError as e:
        assert "handoff" in str(e), str(e)
    return ("site from the type of the link where the field first changed: seam at the compression link and at the "
            "copy, sink at a delivery link; an untyped link leaves site null and ESCALATED; an unknown type refused")


@sentence("K28")
def k28(env: Env) -> str:
    """S1 of the trace bench (draft 8, 2026-11-05): a link that addresses only some carried keys."""
    ad, agreed = registered(env)
    get = lambda o: labels(agreed, o, on_missing_key="undeclared")
    got = get({"po": "4471"})
    assert got["limit.amount"] == "UNDECLARED", got
    assert got["acceptance_criteria.phone"] == "UNDECLARED", got
    assert got["condition.po"] == "PRESERVED", got
    assert get({"po": "4471", "amount": None})["limit.amount"] == "DROPPED", "a null was not the field declared dropped"
    assert get({"po": "4471", "amount": ""})["limit.amount"] == "DROPPED"
    assert get({"amount": 19800.0})["limit.amount"] == "WIDENED"
    assert labels(agreed, {"po": "4471"})["limit.amount"] == "DROPPED", "the default changed"
    assert set(get(RESEARCH).values()) == {"UNDECLARED"}
    no_amount = {k: v for k, v in COMMIT_GOOD.items() if k != "amount"}  # every claim and other key kept
    ad2, agreed2 = registered(env)
    result, sub, _ = commit(agreed2, no_amount, schema=None, on_missing_key="undeclared")
    assert env.binding.is_hold(result) and not sub.calls, "a commit without the carried key passed"
    held = [ln for h in holds(ad2, "commit#1@1") for ln in h.get("lines", [])]
    assert any("limit.amount UNDECLARED" in ln for ln in held), held
    ad3, agreed3 = registered(env)
    result3, sub3, _ = commit(agreed3, no_amount, schema=None, on_missing_key="undeclared", on_undecided="record")
    assert not env.binding.is_hold(result3) and len(sub3.calls) == 1, "on_undecided=record did not let it through"
    try:
        sdk.step("x", agreed3, on_missing_key="skip")
        raise AssertionError("an unknown on_missing_key was accepted")
    except ValueError as e:
        assert "on_missing_key" in str(e), str(e)
    return ("under on_missing_key=undeclared a carried key the link lacks is UNDECLARED and a null is DROPPED; the "
            "default stays DROPPED; at a commit the binding commitment holds unless on_undecided=record; an unknown "
            "mode refused")


@sentence("K29")
def k29(env: Env) -> str:
    """Chunk 1 of the trace bench (draft 9, 2026-11-05; aegis-18 L09): a value in another unit than the agreed one."""
    ad, agreed = full(env, limit=dict(FULL["limit"], weight={"max": 5, "unit": "kg"}))

    def one(dim: str, value: Any, tiers: Any = (), text: Any = None) -> Any:
        out = {"commitments": [limit_of(dim, value)]} if text is None else text
        vs = [v for v in sdk.check(out, agreed, tiers=tiers) if v.field == f"limit.{dim}"]
        return vs[-1]

    v = one("weight", {"max": 5000, "unit": "g"})
    assert (v.drift_class, v.unit_agreed, v.unit_read, v.unit_converted) == ("PRESERVED", "kg", "g", True), v
    assert "converted to at most 5 kg" in v.reason, v.reason
    assert one("weight", {"max": 6000, "unit": "grams"}).drift_class == "WIDENED"
    assert one("weight", {"max": 11, "unit": "lb"}).drift_class == "NARROWED", "11 lb is 4.99 kg, under the cap"
    assert one("weight", {"max": "5,000", "unit": "kilograms"}).drift_class == "WIDENED"
    for dim, value, read in (("amount", {"max": 18400, "unit": "EUR"}, "EUR"), ("weight", {"max": 5, "unit": "m"}, "m"),
                             ("weight", {"max": 5}, None), ("window", {"until": "2026-12-31", "unit": "days"}, "days")):
        v = one(dim, value)
        assert (v.drift_class, v.undecided, v.refusal) == (None, "ESCALATED", "unit_mismatch"), (dim, value, v)
        assert v.unit_read == read and v.unit_agreed == FULL["limit"].get(dim, {"unit": "kg"}).get("unit"), v
        assert v.reason.startswith("unit_mismatch: "), v.reason
        rec = v.record()
        assert rec["refusal"] == "unit_mismatch" and "unit_converted" not in rec, rec
    after = next(v for v in sdk.check({"commitments": [limit_of("weight", {"max": 5000, "unit": "g"})]}, agreed,
                                      history={"c1/limit.weight": "DROPPED"}) if v.field == "limit.weight")
    assert after.drift_class == "PRESERVED" and after.trajectory is None, "a converted bound called verbatim"
    # the same table classes a value reader's read: converted, or refused by name with both units on the verdict
    text = "The cap is 5000 grams. The payment is 18,400 euros."
    reads = {"limit.weight": {"value": {"max": "5000", "unit": "grams"}, "confidence": 0.9, "quote": "5000 grams"},
             "limit.amount": {"value": {"max": 18400, "unit": "EUR"}, "confidence": 0.9, "quote": "18,400 euros"}}
    reader = sdk.ValueReader(lambda e: reads.get(e.field))
    w = one("weight", None, [reader], text)
    assert (w.tier, w.drift_class, w.unit_converted, w.unit_read) == (2, "PRESERVED", True, "grams"), w
    a = one("amount", None, [reader], text)
    assert (a.tier, a.drift_class, a.refusal, a.unit_agreed, a.unit_read) == (2, None, "unit_mismatch", "USD", "EUR"), a
    assert "cannot decide the value read: unit_mismatch" in a.reason and a.proposed_class is None, a
    return ("a bound in another unit: mass, length and time converted to the agreed unit and classed, the verdict "
            "saying so; a currency, another dimension, no unit and a date bound refused as unit_mismatch with "
            "unit_agreed and unit_read, declared or read")


@sentence("K30")
def k30(env: Env) -> str:
    """Chunk 1 of the trace bench (draft 9, 2026-11-05; t03 L07): a proposal carries the class it would give."""
    ad, agreed = registered(env)
    answer: Dict[str, Any] = {}
    reader = sdk.ValueReader(lambda e: answer.get(e.field))
    get = lambda: next(v for v in sdk.check(RESEARCH, agreed, tiers=[reader]) if v.tier == 2
                       and v.field == "limit.amount")
    answer["limit.amount"] = {"value": 19800, "confidence": 0.6, "quote": "18,400 dollars"}
    low = get()
    assert (low.drift_class, low.undecided, low.proposed_class) == (None, "ESCALATED", "WIDENED"), low
    assert low.record()["proposed_class"] == "WIDENED", low.record()
    answer["limit.amount"] = {"value": 18400, "confidence": 0.5, "quote": "18,400 dollars"}
    assert get().proposed_class == "PRESERVED", "a faithful proposal lost its class"
    answer["limit.amount"] = {"value": 19800, "confidence": 0.99, "quote": "19,800 dollars"}
    assert get().proposed_class == "WIDENED", "a read whose quote is not in the text lost its proposed class"
    answer["limit.amount"] = {"value": 19800, "confidence": 0.9, "quote": "18,400 dollars"}
    high = get()
    assert high.drift_class == "WIDENED" and high.proposed_class is None and "proposed_class" not in high.record(), high
    ad2, agreed2 = full(env)
    low2 = sdk.Tier2(lambda e: {"drift_class": "RESTATED", "confidence": 0.4, "reason": "unsure"} if e.field == "act" else None)
    t2 = next(v for v in sdk.check({"commitments": [brief(act="settle the October invoice")]}, agreed2, tiers=[low2])
              if v.tier == 2)
    assert (t2.drift_class, t2.proposed_class) == (None, "RESTATED"), t2
    ad3, agreed3 = registered(env)
    answer["limit.amount"] = {"value": 19800, "confidence": 0.6, "quote": "18,400 dollars"}

    @sdk.step("research", agreed3, tiers=[reader])
    def body(inputs: str, reason: Optional[List[str]] = None) -> Any:
        return RESEARCH
    body(ORDER)
    rep = sdk.report(ad3, out_dir=tmpdir(), quiet=True)
    lines = [ln for ln in rep.unit_lines if "limit.amount" in ln]
    assert any("ESCALATED" in ln and "proposed_class WIDENED" in ln for ln in lines), lines
    assert any(ln.startswith("fields 1 of ") and "proposed at research#1@1 (WIDENED 1)" in ln for ln in rep.count_lines), \
        rep.count_lines
    return ("a read below the threshold, or with a quote not in the text, and a tier 2 answer below it carry "
            "proposed_class beside ESCALATED; a decided read carries none; the report prints each proposal with its "
            "proposed class and counts them per link")


# ---------- Hold ----------
@sentence("H1")
def h1(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    result, sub, calls = commit(agreed, COMMIT_GOOD)
    assert not env.binding.is_hold(result) and len(sub.calls) == 1, (result, sub.calls)
    link = [r for r in ad.records() if r.get("kind") == "link" and r.get("id") == "commit#1@1"][0]
    assert sub.calls[0] == link["idempotency_key"], "the commit hook did not receive the link's key"
    return "pass; commit hook called once with the key"


@sentence("H2")
def h2(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    result, sub, calls = commit(agreed, COMMIT_BAD)
    assert env.binding.is_hold(result)
    lines = holds(ad, "commit#1@1")[0]["lines"]
    line = [ln for ln in lines if "phone-check" in ln]
    assert line and "DROPPED" in line[0] and "commit#1@1" in line[0] and "v1.1" in line[0], lines
    assert "finance-ops" in line[0] and "controller" in line[0] and PHONE in line[0], "the line does not say who owes what"
    ad2, agreed2 = full(env)
    result2, sub2, _ = commit(agreed2, {"commitments": []}, schema=None)
    lines2 = holds(ad2, "commit#1@1")[0]["lines"]
    assert env.binding.is_hold(result2) and len(lines2) == 1 and "c1 DROPPED" in lines2[0], lines2
    return "a DANGER line names the commitment, the field, the class, the link, the version and who owes what"


@sentence("H3")
def h3(env: Env) -> str:
    ad, agreed = registered(env, "The enterprise SLA is not confirmed by the vendor's account team.\nThe total is 18,400 dollars, per the invoice PDF.", [])
    result, sub, calls = commit(agreed, "The enterprise SLA is confirmed by the vendor's account team.\nThe total is 18,400 dollars, per the invoice PDF.", schema=None)
    assert env.binding.is_hold(result), "a hardened marker did not hold"
    ad2, agreed2 = registered(env, "The total is 18,400 dollars, per the invoice PDF.", [])
    result2, sub2, _ = commit(agreed2, "The total is 18,400 dollars.", schema=None)
    assert not env.binding.is_hold(result2) and len(sub2.calls) == 1, "a dropped provenance alone held"
    v = verdicts(ad2, "commit#1@1")[0]
    assert v["status"] == "CHANGED"
    return "hold on hardened; CHANGED reported on a dropped source"


def _demo(fixture: str, retry: bool) -> Tuple[int, str]:
    from ..demo import run_demo
    out = io.StringIO()
    with redirect_stdout(out):
        code = run_demo(fixture, retry=retry, out_dir=tmpdir())
    return code, out.getvalue()


@sentence("H4")
def h4(env: Env) -> str:
    code, text = _demo("widened", False)
    assert code == 1, f"code {code}"
    assert "schema: PASS" in text and "limit.amount WIDENED" in text, text
    assert text.index("schema: PASS") < text.index("DANGER"), "schema line not printed before the hold's lines"
    assert "never called" in text, "the payment system was reached"
    code2, text2 = _demo("widened", True)
    assert code2 == 0 and "restored_verbatim" in text2, text2
    return "a WIDENED limit passes the schema and is held before the commit; the retry restores it"


@sentence("H5")
def h5(env: Env) -> str:
    ad, agreed = registered(env)
    out = env.outputs(research(agreed, "Nothing about the phone or the order here.", "summary"))
    assert out == "Nothing about the phone or the order here."
    rep = sdk.report(agreed, tmpdir(), quiet=True)
    assert rep.code == 2 and "NOT GATED" in rep.gate_line, rep.gate_line
    research(agreed)
    result, sub, _ = commit(agreed, COMMIT_GOOD)
    assert not env.binding.is_hold(result) and len(sub.calls) == 1, (result, sub.calls)
    rep2 = sdk.report(agreed, tmpdir(), quiet=True)
    assert rep2.code == 0, rep2.gate_line
    return ("the intermediate step recorded and returned; a run that gated nothing is not a pass; the commit "
            "step decides the code")


@sentence("H6")
def h6(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    discards: List[Any] = []
    orig = ad.discard
    ad.discard = lambda ctx, outputs: discards.append(outputs) or orig(ctx, outputs)  # type: ignore[assignment]
    result, sub, calls = commit(agreed, COMMIT_BAD, retries=1)
    assert env.binding.is_hold(result) and env.binding.code_of(result) == 1
    assert len(discards) == 1 and discards[0] == COMMIT_BAD, discards
    assert sub.calls == [], "the commit hook was called"
    assert len(calls) == 2 and calls[0] == 0 and calls[1] > 0, calls
    return "discard once, commit never, code 1"


@sentence("H7")
def h7(env: Env) -> str:
    ad, agreed = registered(env)
    result, sub, calls = commit(agreed, {"amount": "eighteen", "po": "4471", "approved": True}, retries=1)
    assert isinstance(result, sdk.SchemaError) and env.binding.code_of(result) == 3, result
    assert sub.calls == [] and calls == [0], "the body was retried or the commit called"
    assert not holds(ad, "commit#1@1"), "a hold record was written for a schema failure"
    return "SchemaError, code 3, no hold, no retry, no commit"


@sentence("H8")
def h8(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)

    def fail(e: Any) -> Any:
        raise RuntimeError("model unavailable")

    result, sub, calls = commit(agreed, COMMIT_GOOD, tiers=[sdk.Tier2(fail)])
    assert env.binding.is_hold(result) and sub.calls == []
    line = [ln for ln in holds(ad, "commit#1@1")[0]["lines"] if "tier 2" in ln][0]
    assert "RuntimeError" in line, line
    assert not verdicts(ad, "commit#1@1", tier=2), "the failing tier's partial verdicts were kept"
    return "held with the tier's name and error; no partial verdicts from the failed tier"


@sentence("H9")
def h9(env: Env) -> str:
    ad, agreed = registered(env)
    try:
        sdk.step("commit", agreed, commit=Submit(), on_loss="record")
    except ValueError as e:
        assert "commit" in str(e), str(e)
        return "refused at decoration"
    raise AssertionError("commit= with on_loss='record' was accepted")


@sentence("H10")
def h10(env: Env) -> str:
    wide = limit_of("amount", {"max": 25000, "unit": "USD"})
    tight = limit_of("amount", {"max": 9000, "unit": "USD"})
    lost = brief(deadline=DROP)
    table = [("MUST", wide, True), ("SHOULD", wide, True), ("MAY", wide, True), ("MUST", lost, True), ("MUST_NOT", lost, True),
             ("SHOULD", lost, False), ("MAY", lost, False), ("MUST", tight, True), ("SHOULD", tight, False)]
    for modality, record, held in table:
        ad, agreed = full(env, modality=modality)
        result, sub, _ = commit(agreed, {"commitments": [dict(record, modality=modality)]}, schema=None)
        assert env.binding.is_hold(result) is held, (modality, held, result)
    # all seven classes of change (SPEC §2.3), each produced alone at a modality that does not bind
    same = dict(FULL["acceptance_criteria"][0])
    up = {"SHOULD": "MUST", "MAY": "SHOULD"}
    changes = {"ADDED_UNPARENTED": lambda m: [brief(modality=m), dict(brief(modality=m), parent_id=None, commitment_id="added")],
               "WIDENED": lambda m: [dict(wide, modality=m)],
               "STRENGTHENED": lambda m: [brief(modality=up[m])],
               "WEAKENED": lambda m: [brief(modality=m, acceptance_criteria=[dict(same, verification="unverifiable")])],
               "RECONDITIONED": lambda m: [brief(modality=m, condition={"po": "4417"})],
               "REASSIGNED": lambda m: [brief(modality=m, obligor="someone-else")],
               "RESCHEDULED": lambda m: [brief(modality=m, deadline="2027-01-01")]}
    assert all(c in sdk.DRIFT_CLASSES for c in changes) and len(changes) == 7, sorted(changes)
    for modality in ("SHOULD", "MAY"):
        ad, agreed = full(env, modality=modality)
        result, sub, _ = commit(agreed, {"commitments": [brief(modality=modality)]}, schema=None)
        assert not env.binding.is_hold(result) and len(sub.calls) == 1, (modality, "an unchanged record held", result)
        for cls, make in changes.items():
            ad, agreed = full(env, modality=modality)
            result, sub, _ = commit(agreed, {"commitments": make(modality)}, schema=None)
            lines = [ln for h in holds(ad) for ln in h["lines"]]
            assert env.binding.is_hold(result) and not sub.calls, (modality, cls, "a change did not hold", result)
            assert any(f" {cls} " in ln for ln in lines), (modality, cls, lines)
    return ("a change to what was agreed holds at any modality, each of the seven classes alone at SHOULD and at MAY; a loss "
            "holds where the commitment binds")


@sentence("H11")
def h11(env: Env) -> str:
    ad, agreed = full(env)
    reworded = {"commitments": [brief(act="settle the October invoice")]}
    result, sub, _ = commit(agreed, reworded, schema=None)
    assert env.binding.is_hold(result) and any("act ESCALATED" in ln for ln in holds(ad)[0]["lines"]), holds(ad)
    ad2, agreed2 = full(env)
    result2, sub2, _ = commit(agreed2, reworded, schema=None, on_undecided="record")
    assert not env.binding.is_hold(result2) and len(sub2.calls) == 1, "on_undecided='record' still held"
    ad3, agreed3 = full(env)
    result3, sub3, _ = commit(agreed3, "A note in prose that says everything was done as agreed.", schema=None)
    assert env.binding.is_hold(result3) and any("UNDECLARED" in ln for ln in holds(ad3)[0]["lines"]), holds(ad3)
    ad4, agreed4 = full(env, modality="SHOULD")
    result4, sub4, _ = commit(agreed4, "A note in prose that says everything was done as agreed.", schema=None)
    assert not env.binding.is_hold(result4), "a commitment that does not bind held on prose"
    return "an escalation no tier answered, and a binding commitment nothing declares, hold at the commit unless on_undecided='record'"


@sentence("H12")
def h12(env: Env) -> str:
    code, text = _demo("dropped", False)
    assert code == 1 and "schema: PASS" in text and "phone-check acceptance_criteria.phone DROPPED" in text, text
    assert "DROPPED!" in text and "epistemic hardened" in text, "the claims' direction is missing from the fixture"
    code2, text2 = _demo("dropped", True)
    assert code2 == 0, text2
    return "a binding commitment dropped passes the schema and holds; the claims' direction meets it at the commit"


# ---------- Retry ----------
@sentence("T1")
def t1(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    seen: Dict[str, Any] = {}

    @sdk.step("commit", agreed, commit=Submit(), schema=SCHEMA, retries=1)
    def explicit(inputs: str, reason: Optional[List[str]] = None) -> Any:
        if reason:
            seen["explicit"] = reason
            return COMMIT_GOOD
        return COMMIT_BAD
    explicit(RESEARCH)

    ad2, agreed2 = registered(env)
    research(agreed2)

    @sdk.step("commit", agreed2, commit=Submit(), schema=SCHEMA, retries=1)
    def kwargs_body(inputs: str, **kw: Any) -> Any:
        if kw.get("reason"):
            seen["kwargs"] = kw["reason"]
            return COMMIT_GOOD
        return COMMIT_BAD
    kwargs_body(RESEARCH)

    ad3, agreed3 = registered(env)
    research(agreed3)

    @sdk.step("commit", agreed3, commit=Submit(), schema=SCHEMA, retries=1)
    def ctx_body(inputs: str) -> Any:
        r = sdk.current_reason()
        if r:
            seen["contextvar"] = r
            return COMMIT_GOOD
        return COMMIT_BAD
    ctx_body(RESEARCH)
    for how, lines in seen.items():
        commitment_lines = [ln for ln in lines if ln.startswith("DANGER commitment")]
        assert commitment_lines, f"{how}: no commitment line in the reason"
        assert all(PAY in ln or PHONE in ln for ln in commitment_lines), f"{how}: a line names an id without the act"
    assert set(seen) == {"explicit", "kwargs", "contextvar"}, seen.keys()
    return "reason delivered through reason=, **kwargs and current_reason(); each line quotes the act"


@sentence("T2")
def t2(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    result, sub, calls = commit(agreed, COMMIT_BAD, retries=2, on_retry=COMMIT_BAD)
    assert env.binding.is_hold(result) and len(calls) == 3, calls
    for bad in (4, -1, 1.5):
        try:
            sdk.step("x", agreed, retries=bad)
            raise AssertionError(f"retries={bad!r} accepted")
        except ValueError:
            pass
    msg = "exactly two reruns then the hold; 4, -1 and 1.5 refused at decoration"
    if env.binding.name == "Temporal":
        msg += "; the count kept across a Temporal retry through heartbeat details is not in the stub, skipped by name"
    return msg


@sentence("T3")
def t3(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    commit(agreed, COMMIT_BAD, retries=1, on_retry=COMMIT_GOOD)
    keys = [r["idempotency_key"] for r in ad.records() if r.get("kind") == "link" and r.get("step") == "commit"]
    assert len(keys) == 2 and keys[0] == keys[1], keys
    ad2, agreed2 = registered(env)
    research(agreed2)
    commit(agreed2, COMMIT_GOOD)
    key2 = [r["idempotency_key"] for r in ad2.records() if r.get("kind") == "link" and r.get("step") == "commit"][0]
    assert key2 != keys[0], "two runs share a key"

    def key_of(cs: Optional[List[Any]] = None, text: str = "the note to send") -> str:
        """The commit's key in a run whose identity is fixed, so that only the record or the inputs can move it."""
        sink = env.adapter(run_id="conformance-t3")
        record = sdk.register(ORDER, commitments() if cs is None else cs, adapter=sink, source="order")

        @sdk.step("commit", record, commit=Submit(), schema=SCHEMA)
        def body(inputs: str, reason: Optional[List[str]] = None) -> Any:
            return COMMIT_GOOD
        try:
            body(text)
        except sdk.AsAgreedError:
            pass
        return [r["idempotency_key"] for r in sink.records() if r.get("kind") == "link" and r.get("step") == "commit"][0]

    base = key_of()
    other = commitments()
    other[1] = sdk.Commitment("confirm the bank details by letter", "finance-ops", "controller", "MUST", commitment_id="phone-check")
    assert key_of() == base, "the same run, record and inputs gave another key"
    assert key_of(text="another note to send") != base, "two invocations with different inputs share a key"
    assert key_of(cs=other) != base, "two as-agreed records that differ share a key"
    return "one key across attempts; different keys across runs, across inputs and across as-agreed records"


@sentence("T4")
def t4(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    result, sub, calls = commit(agreed, COMMIT_BAD, retries=1, on_retry=COMMIT_GOOD)
    assert not env.binding.is_hold(result) and len(sub.calls) == 1
    assert [r for r in ad.records() if r.get("kind") == "retry"], "no retry record"
    assert label_of(ad, "commit#1@2", "pay-invoice", "limit.amount") == "restored_verbatim"
    assert label_of(ad, "commit#1@2", "phone-check", "acceptance_criteria.phone") == "restored_verbatim"
    rep = sdk.report(agreed, tmpdir(), quiet=True)
    assert "after 1 retry" in rep.gate_line, rep.gate_line
    return "pass after one retry; both fields restored_verbatim; the report says so"


@sentence("T5")
def t5(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    raised = 0
    bodies = 0

    @sdk.step("commit", agreed, commit=Submit(), schema=SCHEMA, retries=1)
    def body(inputs: str, reason: Optional[List[str]] = None) -> Any:
        nonlocal bodies
        bodies += 1
        return COMMIT_BAD
    try:
        result = body(RESEARCH)
    except sdk.AsAgreedError as e:
        result = e
        raised += 1
    assert env.binding.is_hold(result) and env.binding.code_of(result) == 1 and bodies == 2, (raised, bodies, result)
    if env.binding.hold_error is not None:  # plain Python: the hold is an error, raised once
        assert raised == 1 and isinstance(result, env.binding.hold_error), (raised, result)
        return f"{env.binding.hold_error.__name__} raised once; the engine's retry policy never involved"
    assert raised == 0, f"an engine's hold is returned in the engine's form, not raised: {result!r}"
    if env.binding.name == "LangGraph":
        from langgraph.graph import END
        assert type(result).__name__ == "Command" and result.goto == END, result
        assert result.update["as_agreed"]["held"] is True, result.update
        return "a Command to END (no hold_to given) with held set; nothing raised for the engine to retry"
    if env.binding.name == "Temporal":
        assert result.passed is False and result.hold, result
        facts = (env.engine or {}).get("raise_if_held")
        if facts is None:
            return "a held Outcome returned; the raise_if_held part needs workflow code (no engine= given), skipped"
        assert facts["type"] == "DriftHold" and facts["non_retryable"] is True, facts
        assert facts["activity_attempts"] == 1 and facts["bodies"] == 2, facts
        return ("a held Outcome; raise_if_held in workflow code raised ApplicationError DriftHold, non_retryable, "
                "after one activity attempt holding both body runs")
    return "the hold returned in the engine's form"


@sentence("T6")
def t6(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    says = dict(COMMIT_BAD, amount=18400.0, note=COMMIT_BAD["note"] + " The bank details were confirmed by phone; phone_confirmed: true.")
    result, sub, calls = commit(agreed, COMMIT_BAD, retries=1, on_retry=says)
    assert env.binding.is_hold(result)
    assert label_of(ad, "commit#1@2", "phone-check", "acceptance_criteria.phone") == "DROPPED"
    return "tier 1 reads declared fields, never prose: a sentence saying it was done does not satisfy the key"


# ---------- Report ----------
def _full_run(env: Env):
    ad, agreed = registered(env)
    research(agreed)
    commit(agreed, COMMIT_BAD, retries=1, on_retry=COMMIT_GOOD)
    rep = sdk.report(agreed, tmpdir(), quiet=True)
    return ad, agreed, rep


@sentence("P1")
def p1(env: Env) -> str:
    def scan(rep: Any) -> Tuple[List[str], List[List[str]]]:
        with open(rep.csv_path, newline="") as f:
            rows = list(csv.reader(f))
        header, data = rows[0], rows[1:]
        assert "run" in header and "proxy" in header and "field" in header, header
        assert data and all(len(r) == len(header) for r in data)
        first_link = header.index("proxy") + 1
        for r in data:
            for i, cell in enumerate(r):
                assert " " not in cell.strip() and "/" not in cell, f"cell {cell!r}"
                assert not re.fullmatch(r"[0-9a-f]{13,}", cell), f"long id {cell}"
                if i >= first_link:
                    assert len(cell) <= 20, f"cell {cell[:30]!r}"
                longest = max((len(p) for p in re.findall(r"[\w-]+", cell)), default=0)
                assert longest <= 40, f"an id of {longest} characters in the {header[i]} column: {cell[:30]!r}"
        return header, data

    ad, agreed, rep = _full_run(env)
    header, data = scan(rep)
    # a downstream record may name a unit, a limit, a condition and a criterion with an id of any length: none reaches a cell
    long_id = "x" * 400
    ad2, agreed2 = full(env)
    odd = brief(limit=dict(FULL["limit"], **{long_id: {"max": 5}}), condition=dict(FULL["condition"], **{long_id: "1"}),
                acceptance_criteria=[dict(FULL["acceptance_criteria"][0]), {"criterion_id": long_id, "text": "the board also approves"}])
    research(agreed2, {"commitments": [odd, dict(brief(), parent_id=None, commitment_id=long_id)]}, "odd")
    rep2 = sdk.report(agreed2, tmpdir(), quiet=True)
    header2, data2 = scan(rep2)
    standins = [c for r in data2 for c in r if re.search(r"(^|\.)[0-9a-f]{12}$", c)]
    assert len(standins) >= 4, f"the long ids were not carried, so none was looked at: {standins}"
    return f"{len(data)} rows, {len(header)} columns, labels only; a 400-character unit, field or criterion id becomes a hash"


@sentence("P2")
def p2(env: Env) -> str:
    ad, agreed, rep = _full_run(env)
    printed = rep.text().splitlines()
    start = printed.index("counts:")
    end = next(i for i, ln in enumerate(printed) if ln.startswith("gate:"))
    block = [ln.strip() for ln in printed[start + 1:end]]
    assert block and start < end, "no count block between counts: and the gate line"
    bad = [ln for ln in block if not re.search(r"\b\d+ of \d+\b", ln)]
    assert not bad, bad
    return f"{len(block)} printed count lines between counts: and the gate line, each 'n of m'"


@sentence("P3")
def p3(env: Env) -> str:
    ad, agreed, rep = _full_run(env)
    with open(rep.svg_path, encoding="utf-8") as fh:
        svg = ledger.normalise(fh.read())
    texts = [c.act for c in agreed.commitments] + [c.text for c in agreed.claims]
    for t in map(ledger.normalise, texts):
        for k in range(0, max(0, len(t) - 60) + 1, 10):
            piece = t[k:k + 60]
            assert len(piece) < 60 or piece not in svg, f"text in the map: {piece!r}"
    return "no 60-character substring of any act or claim in the map"


@sentence("P4")
def p4(env: Env) -> str:
    ad, agreed, rep = _full_run(env)
    assert any(ln.startswith("verdicts") and all(w in ln for w in ("tier 1", "ledger", "tier 2", "tier 3")) for ln in rep.count_lines)
    assert any(ln.startswith("min_words") for ln in rep.tail_lines)
    out = tmpdir()
    rep2 = links_to_run([Link("commit", "Approved today.")], root_input=ORDER, adapter=env.adapter(), out_dir=out)
    assert any("proxy" in ln for ln in rep2.tail_lines)
    return "tiers, min_words and the proxy disclosed"


@sentence("P5")
def p5(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    assert env.code(lambda: commit(agreed, COMMIT_GOOD)[0]) == 0
    assert env.code(lambda: commit(agreed, COMMIT_BAD)[0]) == 1
    assert env.code(lambda: commit(agreed, {"po": "4471", "approved": True})[0]) == 3
    ad2 = env.adapter()

    @sdk.step("x", adapter=ad2)
    def nothing(inputs: str) -> str:
        return inputs
    assert env.code(lambda: nothing("a")) == 2

    def ended(outputs: Any) -> Tuple[Adapter, Any]:
        """A run that research and then the commit came to, and the report of it."""
        sink, record = registered(env)
        research(record)
        commit(record, outputs)
        return sink, sdk.report(record, tmpdir(), quiet=True)

    sink, rep = ended(COMMIT_GOOD)
    assert rep.code == 0, ("a pass", rep.code, rep.gate_line)
    sink, rep = ended(COMMIT_BAD)
    assert rep.code == 1 and [h["code"] for h in holds(sink, "commit#1@1")] == [1], ("a standing hold", rep.code, holds(sink))
    sink, rep = ended({"po": "4471", "approved": True})
    assert rep.code == 3 and "schema" in rep.gate_line, ("a schema failure", rep.code, rep.gate_line)
    sink, record = registered(env)
    research(record)
    assert sdk.report(record, tmpdir(), quiet=True).code == 2, "a run that gated nothing was not a usage error"
    return "codes 0, 1, 3, 2, as raised and as the report ends the run"


@sentence("P6")
def p6(env: Env) -> str:
    ad, agreed = full(env)
    vs = sdk.check({"commitments": [limit_of("amount", {"max": 25000, "unit": "USD"})]}, agreed)
    s = sdk.fidelity_score(vs)
    fields = len(agreed.commitments[0].field_paths())
    assert s["decided"] == fields and s["preserved"] == fields - 1 and abs(s["score"] - (fields - 1) / fields) < 1e-9, s
    assert sdk.fidelity_score([])["score"] is None, "a score with nothing decided"
    restated = sdk.check({"commitments": [brief(act="settle the October invoice")]}, agreed,
                         tiers=[sdk.Tier2(lambda e: {"drift_class": "RESTATED", "confidence": 1.0, "reason": "same"})])
    s2 = sdk.fidelity_score(restated)
    assert s2["preserved"] == s2["decided"] == fields, s2
    ad2, agreed2, rep = _full_run(env)
    lines = [ln for ln in rep.count_lines if ln.startswith("commitment fidelity")]
    assert lines and all(re.search(r"\d+ of \d+ decided fields", ln) for ln in lines), lines
    assert rep.fidelity["commit#1@1"]["decided"] > 0, rep.fidelity
    # a field decided twice, as where a commitment is split into two records, counts at its worst, whichever came first
    wide = limit_of("amount", {"max": 25000, "unit": "USD"})
    for pair in ([brief(), wide], [wide, brief()]):
        twice = sdk.fidelity_score(sdk.check({"commitments": pair}, agreed))
        assert twice["decided"] == fields and twice["preserved"] == fields - 1, ("counted at its best", twice)
    ad3, agreed3 = full(env)
    research(agreed3, {"commitments": [brief(), wide]}, "split_a")
    research(agreed3, {"commitments": [wide, brief()]}, "split_b")
    rep3 = sdk.report(agreed3, tmpdir(), quiet=True)
    with open(rep3.csv_path, newline="") as f:
        cell = next(r for r in csv.DictReader(f) if r["field"] == "limit.amount")
    assert cell["split_a#1@1"] == "WIDENED" and cell["split_b#1@1"] == "WIDENED", ("a faithful label hid a drifted one", cell)
    assert all(rep3.fidelity[link]["preserved"] == fields - 1 for link in ("split_a#1@1", "split_b#1@1")), rep3.fidelity
    return ("fidelity_score carries its pair; RESTATED counts as faithful; the report prints the pair; a field decided twice "
            "counts, and shows, at its worst")


@sentence("P7")
def p7(env: Env) -> str:
    ad, agreed, rep = _full_run(env)
    assert any(ln.startswith("recall: not measured") for ln in rep.tail_lines), rep.tail_lines
    row = sdk.RecallRow(2, "RESTATED", "0.1.0", labelled=50, found=45)
    rep2 = sdk.report(agreed, tmpdir(), quiet=True, recall=[row])
    assert any(ln == "recall tier 2 RESTATED 45 of 50 labelled fields, release 0.1.0" for ln in rep2.count_lines), rep2.count_lines
    assert not any(ln.startswith("recall: not measured") for ln in rep2.tail_lines)
    with open(rep2.ledger_path, encoding="utf-8") as f:
        assert any(json.loads(ln).get("kind") == "recall" for ln in f), "no recall record in the ledger"
    try:
        sdk.RecallRow(2, "CHANGED", "0.1.0", labelled=1, found=1)
        raise AssertionError("a recall row outside the eleven classes was accepted")
    except ValueError:
        pass
    return "recall per tier, class and release as n of m; with no sample the report says not measured"


@sentence("P8")
def p8(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    rep = sdk.report(agreed, tmpdir(), quiet=True)
    assert any(ln.startswith("uncheckable: pay-invoice") and "deadline" in ln for ln in rep.tail_lines), rep.tail_lines
    assert any("undeclared" in ln and re.search(r"\d+ of \d+", ln) for ln in rep.count_lines), rep.count_lines
    with open(rep.csv_path, newline="") as f:
        cells = [c for row in csv.reader(f) for c in row]
    assert "UNDECLARED" in cells, "an undeclared field left no trace in the CSV"
    return "what cannot be checked stays in the report, by field"


# ---------- Adapter ----------
@sentence("A1")
def a1(env: Env) -> str:
    assert env.ran, "A1 ran before the other sentences; it must run last"
    assert not env.skipped_shared, f"shared sentences failed or were skipped: {env.skipped_shared}"
    return f"every shared sentence ran on {env.binding.name} ({len(env.ran)} before this one)"


@sentence("A2")
def a2(env: Env) -> str:
    class Incomplete(Adapter):
        def context(self, *a: Any, **k: Any) -> Any: ...
        def rerun(self, *a: Any, **k: Any) -> Any: ...
        def record(self, *a: Any, **k: Any) -> Any: ...
        def records(self, *a: Any, **k: Any) -> Any: ...
        def clock(self) -> str: return ""
    try:
        Incomplete()
    except TypeError as e:
        msg = str(e)
        assert "commit" in msg and "discard" in msg and "raise_hold" in msg, msg
        return "refused at construction, naming commit, discard and raise_hold"
    raise AssertionError("an adapter without hooks was constructed")


@sentence("A3")
def a3(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    n = len(ad.records())
    ad.sink = env.binding.reopen(ad.sink)
    assert len(ad.records()) == n and n > 0, (n, len(ad.records()))
    msg = f"{n} records present after reopening the sink"
    if env.binding.name == "plain":  # and the file-backed sink, whatever sink the factory gave
        disk = PlainAdapter(run_dir=tmpdir())
        agreed2 = sdk.register(ORDER, commitments(), adapter=disk)
        research(agreed2)
        m = len(disk.records())
        disk.sink = disk.sink.reopen()
        assert len(disk.records()) == m and m > 0, "the JSONL sink lost records across a reopen"
        msg += f"; {m} records in ledger.jsonl after a reopen"
    return msg


@sentence("A4", adapter=("Temporal", "LangGraph"))
def a4(env: Env) -> str:
    if env.binding.name == "LangGraph":
        from langchain_core.runnables import RunnableLambda
        ad = env.adapter()
        try:
            ad.step("research")(RunnableLambda(lambda state: state))
        except ValueError as e:
            assert "Runnable" in str(e), str(e)
            return "a Runnable refused at decoration with ValueError; node functions are decorated"
        raise AssertionError("a Runnable was decorated")
    facts = (env.engine or {}).get("workflow_refusal")
    if facts is None:
        raise NotHere("adapter: Temporal; needs workflow code on a worker (engine= from tests/test_adapter_temporal.py)")
    assert facts["error"] == "WorkflowContextError" and facts["body_ran"] is False, facts
    return f"WorkflowContextError in workflow code before the body ran ({facts.get('environment', '?')})"


def _lg_graph(lg: Any, *, key: bool = True, static_edge: bool = False, sub: Optional[Submit] = None,
              reviews: Optional[List[int]] = None) -> Any:
    """START -> start (registers) -> research -> commit, with hold_to="review"; COMMIT_GOOD when state case is good."""
    from typing import Annotated, TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    from ..adapters.langgraph import merge
    sub = sub if sub is not None else Submit()
    reviews = reviews if reviews is not None else []

    def start(state: Any) -> Any:
        return lg.register(state, ORDER, commitments())

    @lg.step("research")
    def research_node(state: Any) -> Any:
        return {"note": RESEARCH}

    @lg.step("commit", commit=sub, schema=SCHEMA, hold_to="review")
    def commit_node(state: Any) -> Any:
        return dict(COMMIT_GOOD if state.get("case") == "good" else COMMIT_BAD)

    def review(state: Any) -> Any:
        reviews.append(1)
        return {"reviewed": True}

    fields: Dict[str, Any] = {"case": str, "note": str, "amount": float, "po": str, "approved": bool,
                              "phone_confirmed": bool, "reviewed": bool}
    if key:
        fields["as_agreed"] = Annotated[dict, merge]
    b = StateGraph(TypedDict("ConformanceState", fields, total=False))  # type: ignore[misc]
    for name, fn in (("start", start), ("research", research_node), ("commit", commit_node), ("review", review)):
        b.add_node(name, fn)
    b.add_edge(START, "start")
    b.add_edge("start", "research")
    b.add_edge("research", "commit")
    b.add_edge("review", END)
    if static_edge:
        b.add_edge("commit", END)
    return b.compile(checkpointer=InMemorySaver())


@sentence("A5", adapter="LangGraph")
def a5(env: Env) -> str:
    lg = env.adapter()
    sub = Submit()
    graph = lg.attach(_lg_graph(lg, sub=sub))
    held_cfg = {"configurable": {"thread_id": "a5-held"}}
    out = graph.invoke({"case": "bad"}, held_cfg)
    assert out.get("reviewed") is True and out["as_agreed"]["held"] is True, out
    assert "amount" not in out and sub.calls == [], "a payload key was written, or the commit ran, on a hold"
    kept = graph.get_state(held_cfg).values["as_agreed"]["records"]
    hold_rows = [r for r in kept if r.get("kind") == "hold"]
    assert hold_rows and hold_rows[-1]["passed"] is False and hold_rows[-1]["link"] == "commit#1@2", hold_rows
    good_cfg = {"configurable": {"thread_id": "a5-good"}}
    out2 = graph.invoke({"case": "good"}, good_cfg)
    assert out2["amount"] == 18400.0 and len(sub.calls) == 1 and out2["as_agreed"]["held"] is False, out2
    assert out2["as_agreed"]["committed"] == sub.calls[0], "the committed mark is not the commit's key"
    try:
        lg.attach(_lg_graph(lg, key=False))
        raise AssertionError("a state without the key was accepted")
    except ValueError as e:
        assert "as_agreed" in str(e), str(e)
    try:  # and a step reached in a graph whose state has no key is refused at its first call
        from langgraph.graph import START, StateGraph
        from typing import TypedDict

        @lg.step("research")
        def lone(state: Any) -> Any:
            return {"note": RESEARCH}
        b = StateGraph(TypedDict("Keyless", {"note": str}, total=False))  # type: ignore[misc]
        b.add_node("research", lone)
        b.add_edge(START, "research")
        b.compile().invoke({"note": ""})
        raise AssertionError("a step ran on a state without the key")
    except ValueError as e:
        assert "as_agreed" in str(e), str(e)
    try:
        lg.attach(_lg_graph(lg, static_edge=True))
        raise AssertionError("a commit node with a static outgoing edge was accepted")
    except ValueError as e:
        assert "commit" in str(e) and "static" in str(e), str(e)
    return (f"{len(kept)} records under as_agreed in the checkpointer; the hold a Command to review with held set "
            f"and no payload key; the pass wrote the payload and the key; no key and a static edge refused")


@sentence("A6", adapter="Temporal")
def a6(env: Env) -> str:
    facts = env.engine or {}
    if "environment" not in facts:
        raise NotHere("adapter: Temporal; needs a worker (engine= from tests/test_adapter_temporal.py)")
    ad, agreed = registered(env)
    research(agreed)
    before = len(ad.records())
    result, sub, _ = commit(agreed, COMMIT_BAD, retries=1, on_retry=COMMIT_BAD)
    written = ad.records()[before:]
    assert env.binding.is_hold(result) and not sub.calls, result
    assert result.records == written and {"link", "verdict", "hold", "retry"} <= {r["kind"] for r in written}, \
        [r["kind"] for r in result.records]
    resume = facts["resume"]
    assert resume["passed"] is True and resume["bodies"] == 1 and resume["payments"] == 1, resume
    assert resume["activity_attempts"] == 2, resume
    return (f"on {facts['environment']}: a held Outcome carrying all {len(written)} records of the step; a retry "
            f"after the commit callable ran found the committed mark and passed without re-running the body")


@sentence("A7")
def a7(env: Env) -> str:
    ad, agreed = registered(env)
    research(agreed)
    research(agreed)
    links = [r for r in ad.records() if r.get("kind") == "link" and r.get("step") == "research"]
    assert [l["id"] for l in links] == ["research#1@1", "research#2@1"], [l["id"] for l in links]
    assert links[0]["idempotency_key"] != links[1]["idempotency_key"]
    rep = sdk.report(agreed, tmpdir(), quiet=True)
    with open(rep.csv_path, newline="") as fh:
        header = fh.readline().strip().split(",")
    assert "research#1@1" in header and "research#2@1" in header
    return "two invocations, two keys, two columns"


@sentence("A8", adapter="LangGraph")
def a8(env: Env) -> str:
    lg = env.adapter()
    sub = Submit()
    reviews: List[int] = []
    graph = lg.attach(_lg_graph(lg, sub=sub, reviews=reviews))
    cfg = {"configurable": {"thread_id": "a8"}}
    first = graph.invoke({"case": "bad"}, cfg)
    assert first["as_agreed"]["held"] is True and reviews == [1], first
    second = graph.invoke({"case": "good"}, cfg)
    assert second["as_agreed"]["held"] is False and second["as_agreed"]["reason"] is None, second["as_agreed"]
    assert reviews == [1] and second["amount"] == 18400.0 and len(sub.calls) == 1, (reviews, sub.calls)
    runs = [r["run_id"] for r in second["as_agreed"]["records"] if r.get("kind") == "as_agreed"]
    assert runs == ["a8/r1", "a8/r2"], runs
    return "the second invocation on the thread starts unheld, routes past the hold target and commits; run ids a8/r1, a8/r2 minted"


# ---------- Import ----------
@sentence("I1")
def i1(env: Env) -> str:
    folder = tmpdir()
    with open(os.path.join(folder, "01-order.md"), "w") as f:
        f.write(ORDER)
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        code = run_folder(folder, adapter=env.adapter(), out_dir=tmpdir())
    assert code == 2 and folder in err.getvalue(), (code, err.getvalue())
    return "code 2 and the folder named"


@sentence("I2")
def i2(env: Env) -> str:
    folder = tmpdir()
    for name, text in (("10-a.md", "The final note says nothing about phones."), ("2-b.md", RESEARCH), ("1-order.md", ORDER)):
        with open(os.path.join(folder, name), "w") as f:
            f.write(text)
    ad = env.adapter()
    with redirect_stdout(io.StringIO()):
        run_folder(folder, adapter=ad, out_dir=tmpdir())
    links = [r["id"] for r in ad.records() if r.get("kind") == "link"]
    assert links == ["order#1@1", "b#1@1", "a#1@1"], links
    head = [r for r in ad.records() if r.get("kind") == "as_agreed"][0]
    assert head["intent_record"]["text"].startswith("Pay the October invoice")
    return "numeric order; the first file registered the as-agreed record"


@sentence("I3")
def i3(env: Env) -> str:
    raise AssertionError("deferred")


@sentence("I4")
def i4(env: Env) -> str:
    raise AssertionError("deferred")


@sentence("I5")
def i5(env: Env) -> str:
    raise AssertionError("deferred")


@sentence("I6")
def i6(env: Env) -> str:
    root = "Summarise the thread about the vendor's new bank details and the October invoice."
    ad = env.adapter()
    links_to_run([Link("commit", RESEARCH)], root_input=root, adapter=ad, out_dir=tmpdir())
    assert [r for r in ad.records() if r.get("kind") == "as_agreed"][0]["proxy"] is True
    try:
        links_to_run([Link("commit", RESEARCH)], root_input="Go.", adapter=env.adapter(), out_dir=tmpdir())
        raise AssertionError("a root with no text to register was accepted as a proxy")
    except sdk.EmptyAsAgreed as e:
        assert "--commitment" in str(e), str(e)
    ad2 = env.adapter()
    links_to_run([Link("commit", RESEARCH)], root_input=root, adapter=ad2, out_dir=tmpdir(), commitment_text=ORDER)
    head = [r for r in ad2.records() if r.get("kind") == "as_agreed"][0]
    assert head["proxy"] is False and head["intent_record"]["text"] == ORDER
    return "proxy without --commitment; the supplied order with it"


@sentence("I7")
def i7(env: Env) -> str:
    folder = tmpdir()
    with open(os.path.join(folder, "01-order.md"), "w", encoding="utf-8") as f:
        f.write(ORDER)
    with open(os.path.join(folder, "02-commit.md"), "w", encoding="utf-8") as f:
        f.write("The prose of the commit, which tier 1 does not read.\n")
    with open(os.path.join(folder, "02-commit.json"), "w", encoding="utf-8") as f:
        json.dump({"amount": 19800.0, "po": "4471"}, f)
    with open(os.path.join(folder, "commitments.json"), "w", encoding="utf-8") as f:
        json.dump([{"commitment_id": "pay-invoice", "act": PAY, "obligor": "finance-ops", "obligee": "northwind-freight",
                    "modality": "MUST", "limit": {"amount": {"max": 18400, "unit": "USD"}},
                    "carried_by": {"limit.amount": "amount"}}], f)
    ad = env.adapter()
    with redirect_stdout(io.StringIO()):
        code = run_folder(folder, adapter=ad, out_dir=tmpdir())
    assert code == 1, f"code {code}"
    assert label_of(ad, "commit#1@1", "pay-invoice", "limit.amount") == "WIDENED"
    assert [r["id"] for r in ad.records() if r.get("kind") == "link"] == ["order#1@1", "commit#1@1"]
    return "commitments.json declares the as-agreed record; a same-stem .json declares a stage's fields"
