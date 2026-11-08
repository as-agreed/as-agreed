#!/usr/bin/env python3
"""ledger: did the claims from one step of a workflow reach the next step, with their status preserved?

Standard library only. Runs on your machine. Nothing leaves it.

A claim is a sentence someone downstream could rely on. Its status is four markers read from
its own words: how sure (epistemic), found or not found (existential), from what source
(provenance), as of when (temporal). The ledger records each claim when it is produced and,
at every later step, whether it is present in the outgoing artifact and whether its markers
survived. A check is binary per claim per hop. There are no probabilities, because none are
earned: a count with a denominator is what you act on.

Commands
  run       <folder>                     a folder of ordered stage files (01-scout.md, 02-message.md, ...):
                                          register, check each against the previous, report, draw
  register  <file> --stage NAME          cut a file into claims, record them in the ledger
  check     <file> --stage NAME --against STAGE[,STAGE] [--fail-on dropped,changed,danger,dropped_must]
                                          gate: is every upstream claim present with its status preserved?
  report                                  counts per hop, with denominators
  flow      --out flow.svg                the status-flow picture from the ledger

Claim ids: 12 hex of the normalised sentence. An artifact may carry explicit tags [c:<id>] to
reference a claim; then the check finds it by id even if the wording changed. Without tags the
check uses word overlap (a third of the claim's content words on one line, at least two; two
thirds for a claim marked must-travel) or a 60-character shared substring. It misses paraphrase
and translation, and a different line that shares enough words can pass for the claim; tag a
claim with [c:<id>] where that matters. Both limits come from the study this tool was built for;
see README.

The file checked last (the one `check` names, the last stage of `run`) is also read sentence by sentence, whatever
came before it: an absence ("have not", "there is no", "not found") with no date in its sentence is DANGER `undated absence`, and "the only", "the largest",
"the biggest", "the first to", "nobody has" or "no one has" with no dated source in brackets and no footnote marker is
DANGER `unsourced superlative`. Both fail the gate under --fail-on danger. A sentence already held as hardened or
inverted keeps its one DANGER line; the other finding is printed under it as "also".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, OrderedDict, defaultdict
from typing import Dict, List, Optional, Tuple

from . import NAME, __version__

# ---------- lexicon (English part seeded from Zhou et al. 2024 weakeners/strengtheners; Russian added) ----------
WEAKENERS = [
    r"not found", r"could not (?:verify|confirm|find|open)", r"couldn't", r"unverified", r"not (?:confirmed|verified)",
    r"appears?", r"seems?", r"\bmay\b", r"\bmight\b", r"likely", r"unclear", r"no evidence", r"probably", r"possibly",
    r"approximately", r"\babout\b", r"\baround\b", r"estimate[sd]?", r"i think", r"i believe", r"suggests?", r"perhaps",
    r"вероятно", r"возможно", r"похоже", r"кажется", r"предполож\w*", r"не подтвержд\w*", r"не подтвердил\w*",
    r"не удал\w*", r"не наш[её]л\w*", r"не найден\w*", r"не открыл\w*", r"не открыва\w*", r"не провер\w*",
    r"неясно", r"нет данных", r"оценочн\w*", r"по оценке", r"примерно", r"около", r"скорее всего", r"видимо",
    r"не уверен\w*", r"спорн\w*", r"пограничн\w*", r"наверн(?:ое|о)\b",
]
STRENGTHENERS = [
    r"definitely", r"certainly", r"clearly", r"confirmed", r"verified", r"\bproven\b", r"always", r"undoubtedly",
    r"точно", r"подтвержд[её]н\w*", r"проверен\w*", r"однозначно", r"наверняка", r"безусловно", r"несомненно",
]
NOT_FOUND = [r"not found", r"could not find", r"couldn't find", r"no (?:data|record|evidence|mention)", r"did not find",
             r"не найден\w*", r"не наш[её]л\w*", r"нет данных", r"не обнаружен\w*", r"отсутству\w*"]
UNKNOWN = [r"\bunknown\b", r"not known", r"неизвестн\w*", r"не известн\w*"]
NEGATIONS = [r"\bnot\b", r"\bno\b", r"\bnever\b", r"\bне\b", r"\bнет\b", r"\bни\b"]
SOURCE_WORDS = [r"according to", r"source[s]?:?", r"per the", r"from the (?:page|site|report|file|document)",
                r"по данным", r"по справке", r"источник\w*", r"согласно", r"со слов"]
TEMPORAL = [r"\bas of\b", r"\b\d{4}-\d{2}-\d{2}\b", r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b", r"\byesterday\b", r"\btoday\b",
            r"\blast (?:week|month|year)\b", r"\bв \d{4}\b", r"по состоянию на", r"вчера", r"сегодня", r"на \d{1,2}\.\d{1,2}"]

WEAK = re.compile("|".join(WEAKENERS), re.I)
STRONG = re.compile("|".join(STRENGTHENERS), re.I)
NF = re.compile("|".join(NOT_FOUND), re.I)
UNK = re.compile("|".join(UNKNOWN), re.I)
NEG = re.compile("|".join(NEGATIONS), re.I)
SRC = re.compile("|".join(SOURCE_WORDS), re.I)
TEMP = re.compile("|".join(TEMPORAL), re.I)
URL_OR_PATH = re.compile(r"https?://[^\s)\]\"'>]+|(?:[\w.-]+/)+[\w.-]+\.\w{1,5}")
TAG = re.compile(r"\[c:([0-9a-f]{12})\]")
WORD = re.compile(r"[\w-]{4,}", re.U)
STOP = {"which", "about", "there", "these", "those", "would", "could", "should", "that", "this", "with", "from", "have",
        "will", "your", "their", "them", "they", "what", "when", "where", "then", "than", "into", "onto", "each", "every",
        "been", "being", "were", "also", "only", "just", "more", "most", "some", "such", "very", "over", "under",
        "которые", "который", "этого", "поэтому", "только", "нужно", "через", "после", "между", "также", "файле",
        "строк", "строки", "если", "есть", "нет", "когда", "чтобы", "этот", "эта", "это", "того", "тоже", "уже"}
PATHISH = re.compile(r"\S*[/\\]\S*|\b[0-9a-f]{8,}\b|\S+\.(?:md|csv|jsonl?|py|mjs|ts|png|jpg)\b", re.I)


# ---------- text handling ----------
def sentences(text: str) -> List[str]:
    # markdown headings, table rules and fenced-code markers are not claims
    text = "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#") and not re.match(r"^\s*\|?\s*-{3,}", ln) and not ln.strip().startswith("```"))
    text = TAG.sub(lambda m: f" {m.group(0)} ", text)
    parts = re.split(r"(?<=[.!?;:])\s+|\n+", text)
    out = []
    for p in parts:
        p = p.strip(" -*•\t#>|")
        p = re.sub(r"\s+", " ", p)
        if len(p) > 15:
            out.append(p)
    return out


def content_words(s: str) -> set:
    s2 = PATHISH.sub(" ", TAG.sub(" ", s))
    ws = {w.lower() for w in WORD.findall(s2)}
    marker_words = {m.group(0).lower() for m in WEAK.finditer(s2)} | {m.group(0).lower() for m in STRONG.finditer(s2)}
    return {w for w in ws if w not in STOP and w not in marker_words and not w.isdigit()}


def normalise(s: str) -> str:
    s = TAG.sub("", s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def claim_id(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()[:12]


def markers(s: str) -> Dict[str, Optional[str]]:
    w = WEAK.search(s)
    st = STRONG.search(s)
    epistemic = w.group(0) if w else (st.group(0) if st else None)
    if NF.search(s):
        existential = "not_found"
    elif UNK.search(s):
        existential = "unknown_stated"
    else:
        existential = "value_present"
    src = URL_OR_PATH.search(s) or SRC.search(s)
    provenance = src.group(0) if src else "none stated"
    t = TEMP.search(s)
    temporal = t.group(0) if t else None
    return {"epistemic": epistemic, "existential": existential, "provenance": provenance, "temporal": temporal}


def origin_class(m: Dict[str, Optional[str]]) -> str:
    if m.get("epistemic") and WEAK.search(m["epistemic"] or ""):
        return "hedged"
    if m.get("existential") in ("not_found", "unknown_stated"):
        return "not_found"
    return "flat"


# ---------- flags ----------
FAIL_ON = ("dropped", "changed", "danger", "dropped_must")
ONLY = ("all", "hedged", "not_found", "sourced", "dated", "must")
OLD_FAIL_ON = {"absent": "dropped", "absent_must": "dropped_must"}  # kept for the project's own stage folders


def _values(raw: str, flag: str, known: Tuple[str, ...], aliases: Optional[Dict[str, str]] = None) -> List[str]:
    """The comma-separated values of a flag. An unknown value stops the run: a typo must not pass the gate."""
    aliases = aliases or {}
    out: List[str] = []
    for v in (s.strip() for s in raw.split(",")):
        if not v:
            continue
        if v in aliases:
            print(f"note: {flag} {v} is the old name of {aliases[v]}; it still works", file=sys.stderr)
            v = aliases[v]
        if v not in known:
            print(f"error: {flag} value {v!r} is not known; use any of {','.join(known)}", file=sys.stderr)
            raise SystemExit(2)
        out.append(v)
    if not out:
        print(f"error: {flag} needs a value; use any of {','.join(known)}", file=sys.stderr)
        raise SystemExit(2)
    return out


def parse_fail_on(raw: str) -> Tuple[str, ...]:
    return tuple(_values(raw, "--fail-on", FAIL_ON, OLD_FAIL_ON))


def parse_only(raw: str) -> List[str]:
    return _values(raw, "--only", ONLY)


# ---------- ledger io ----------
def load(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append(path: str, rows: List[dict]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------- register ----------
def mark_warnings(text: str, min_words: int) -> List[Tuple[int, str]]:
    """Must-travel marks that register nothing: (line, why). Without this the gate passes a mark it never saw."""
    out: List[Tuple[int, str]] = []
    fenced = False
    for n, ln in enumerate(text.splitlines(), 1):
        if ln.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or not MUST.search(ln):
            continue
        late = sum(1 for m in MUST.finditer(ln) if not ln[:m.start()].rstrip() or ln[:m.start()].rstrip()[-1] in ".!?;:…")
        if late:
            out.append((n, "the mark follows the sentence's final punctuation; put it just before"))
        kept = sum(1 for s in sentences(ln) if MUST.search(s) and not MUST.match(s) and content_words(s))
        if len(MUST.findall(ln)) - late > kept:
            out.append((n, "the sentence has no content word to be a claim"))
    return out


def register(path: str, stage: str, ledger: str, min_words: int, quiet: bool = False) -> List[dict]:
    text = open(path, encoding="utf-8").read()
    for n, why in mark_warnings(text, min_words):
        print(f"warning: {os.path.basename(path)} line {n}: must-travel mark not registered: {why}", file=sys.stderr)
    # the mark is written once, in the file that first carried the sentence; a later copy of the same claim id keeps it
    marked_before = {r["claim_id"] for r in load(ledger) if r.get("kind") == "claim" and r.get("must")}
    rows = []
    seen = set()
    for s in sentences(text):
        cw = content_words(s)
        marked = bool(MUST.search(s)) and not MUST.match(s)
        if len(cw) < (1 if marked else min_words):  # a marked sentence is a claim whatever its length (D-66)
            continue
        tag = TAG.search(s)
        cid = tag.group(1) if tag else claim_id(s)
        if cid in seen:
            continue
        seen.add(cid)
        rows.append({"kind": "claim", "claim_id": cid, "stage": stage, "file": os.path.basename(path),
                     "text": MUST.sub("", TAG.sub("", s)).strip()[:500], "markers": markers(s),
                     "must": (bool(MUST.search(s)) and not MUST.match(s)) or cid in marked_before, "ts": now()})
    append(ledger, rows)
    if not quiet:
        c = Counter(origin_class(r["markers"]) for r in rows)
        print(f"registered {len(rows)} claims from {os.path.basename(path)} at stage '{stage}': "
              f"hedged {c['hedged']}, not_found {c['not_found']}, flat {c['flat']}")
    return rows


# ---------- check ----------
def find(claim: str, cid: str, lines: List[str], text: str, must: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """Returns (match_kind, span) or (None, None). A must-travel claim needs two thirds of its content words: three
    shared words on a different line must not pass for it."""
    if f"[c:{cid}]" in text:
        for ln in lines:
            if f"[c:{cid}]" in ln:
                return "id", ln.strip()[:300]
        return "id", None
    cw = content_words(claim)
    if len(cw) >= 2:
        need = max(2, (2 * len(cw) + 2) // 3 if must else (len(cw) + 2) // 3)
        best, best_score = None, 0.0
        for ln in lines:
            shared = cw & content_words(ln)
            if len(shared) >= need:
                score = len(shared) / len(cw)
                if score > best_score:
                    best, best_score = ln, score
        if best is not None:
            return "words", best.strip()[:300]
    norm_claim = normalise(claim)
    norm_text = normalise(text)
    if 0 < len(norm_claim) < 60 and re.search(rf"\b{re.escape(norm_claim)}\b", norm_text):  # a short claim, whole
        return "substring", norm_claim
    for k in range(0, max(0, len(norm_claim) - 60) + 1, 10):
        piece = norm_claim[k:k + 60]
        if len(piece) == 60 and piece in norm_text:
            return "substring", piece
    return None, None


def fate(claim_m: Dict[str, Optional[str]], claim_text: str, span: Optional[str]) -> Dict[str, str]:
    """Per-marker fate in the copy. Rules follow the study's deterministic scorer."""
    out: Dict[str, str] = {}
    if span is None:
        return {"epistemic": "unknown", "existential": "unknown", "provenance": "unknown", "temporal": "unknown"}
    src_weak = bool(claim_m["epistemic"] and WEAK.search(claim_m["epistemic"]))
    src_strong = bool(claim_m["epistemic"] and STRONG.search(claim_m["epistemic"]))
    par_weak, par_strong = bool(WEAK.search(span)), bool(STRONG.search(span))
    if src_weak:
        # "not confirmed / could not verify" is a statement about missing verification. Stated flat
        # downstream, it is hardened (the report contract's example); negated downstream, inverted.
        negated_verification = bool(re.search(r"not (?:confirmed|verified|found)|could not (?:verify|confirm)|unverified|"
                                              r"не подтвержд|не найден|не наш[её]л|не провер", claim_text, re.I))
        if par_weak:
            out["epistemic"] = "kept"
        elif negated_verification and NEG.search(span) and not NF.search(span):
            out["epistemic"] = "inverted"
        elif negated_verification or par_strong:
            out["epistemic"] = "hardened"
        else:
            out["epistemic"] = "dropped"
    elif src_strong:
        out["epistemic"] = "kept" if par_strong else ("softened" if par_weak else "dropped")
    else:
        out["epistemic"] = "n/a"
    ex = claim_m["existential"]
    if ex in ("not_found", "unknown_stated"):
        if NF.search(span) or UNK.search(span):
            out["existential"] = "kept"
        elif NEG.search(span):
            out["existential"] = "hardened"  # 'not found' became 'does not exist'
        else:
            out["existential"] = "dropped"
    else:
        out["existential"] = "n/a"
    prov = claim_m["provenance"]
    if prov and prov != "none stated":
        out["provenance"] = "kept" if (prov.lower() in span.lower() or (URL_OR_PATH.search(span) is not None)) else "dropped"
    else:
        out["provenance"] = "n/a"
    t = claim_m["temporal"]
    if t:
        out["temporal"] = "kept" if (t.lower() in span.lower() or TEMP.search(span)) else "dropped"
    else:
        out["temporal"] = "n/a"
    return out


def changed(f: Dict[str, str]) -> bool:
    return any(v in ("dropped", "hardened", "inverted", "softened") for v in f.values())


def dangerous(f: Dict[str, str]) -> bool:
    return any(v in ("hardened", "inverted") for v in f.values())


MUST = re.compile(r"\[(?:!|must)\]", re.I)


# ---------- sentences that fail on their own (an absence with no date, a superlative with no dated source) ----------
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|"
          r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)")
_YEAR = r"(?:19|20)\d{2}"
DATE = re.compile(  # an ISO date (or year and month), a numeric date, a month and year, an "as of" date
    rf"\b{_YEAR}-(?:0[1-9]|1[0-2])(?:-\d{{2}})?\b|\b\d{{1,2}}[./]\d{{1,2}}[./]{_YEAR}\b"
    rf"|\b{_MONTH}\b\.?,?\s+(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s+)?{_YEAR}\b"
    rf"|\bas of\s+(?:the\s+)?(?:\d|{_MONTH}\b)|по состоянию на\s+(?:\d|{_MONTH})", re.I)
# its own list, apart from the register's NOT_FOUND: a letter says "I have not run", not "not found"; the fix is a date
# "never" is left out (D-66): "we never sell your data" is a promise, and promises are promise_check's job
ABSENCE = re.compile(r"\b(?:have\s+not|haven[\'\u2019]t|has\s+not|hasn[\'\u2019]t|there\s+(?:is|are)\s+no|no\s+one\s+has|"
                     r"nobody\s+has|(?:did|could)\s+not\s+find|(?:didn|couldn)[\'\u2019]t\s+find|not\s+found|"
                     r"no\s+(?:data|records?|evidence|mentions?))\b"
                     r"|не\s+найден\w*|не\s+наш[её]л\w*|нет\s+данных|не\s+обнаружен\w*|отсутству\w*", re.I)
# a superlative about the world; "the first" and "the one" alone are ordinals ("the first team"), "the first to" is not
SUPERLATIVE = re.compile(r"\bthe\s+(?:only|largest|biggest)(?![-\w])|\bthe\s+first\s+to\b|\b(?:nobody|no\s+one)\s+has\b",
                         re.I)
FOOTNOTE = re.compile(r"\[\^[^\]\s]+\]|\[\d{1,3}\]|[¹²³⁴⁵⁶⁷⁸⁹⁰]")
BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)")
CODE_SPAN = re.compile(r"`[^`]*`")
OWN_FIX = {"undated absence": 'write when it was true in the sentence, for example "as of YYYY-MM-DD"',
           "unsourced superlative": 'put its source and date in brackets, for example "[source, YYYY-MM-DD]", '
                                    'or a footnote marker; or cut the superlative'}


def own_sentences(text: str) -> List[str]:
    """Sentences as a reader meets them: a paragraph's lines joined, cut only at . ! ? (a date before a colon stays in
    its sentence), a bracket after the full stop kept with the sentence before it. Headings, table rules and code
    blocks are left out."""
    blocks: List[str] = []
    cur: List[str] = []
    fenced = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("```"):
            fenced, s = not fenced, ""
        if fenced or not s or s.startswith("#") or re.match(r"^\|?\s*:?-{3,}", s):
            if cur:
                blocks.append(" ".join(cur))
                cur = []
            continue
        if cur and re.match(r"^(?:[-*+•>|]|\d+[.)])\s", s):  # a list item, a quote or a table row starts anew
            blocks.append(" ".join(cur))
            cur = []
        cur.append(s)
    if cur:
        blocks.append(" ".join(cur))
    out = []
    for b in blocks:
        for p in re.split(r"(?:(?<=[.!?])|(?<=[.!?][\"'\u201d\u2019)\]]))\s+(?![\s\[(])", b):
            p = re.sub(r"\s+", " ", p.strip(" -*•\t#>|"))
            if len(p) > 15:
                out.append(p)
    return out


def own_dangers(text: str) -> List[Tuple[str, str]]:
    """Sentences of the outgoing file that fail whatever came before them (MISTAKES M02, M03): (kind, sentence).
    An absence ("have not", "there is no", "not found") with no date in its sentence goes stale unseen; a
    superlative about the world ("the only", "the first to", "nobody has") is true of one pool at one time unless a
    dated source in brackets or a footnote marker says which."""
    out: List[Tuple[str, str]] = []
    seen = set()
    for s in own_sentences(text):
        words = CODE_SPAN.sub(" ", s)  # a phrase in code font is code, not a claim
        found = []
        if ABSENCE.search(words) and not DATE.search(s):
            found.append("undated absence")
        if SUPERLATIVE.search(words) and not FOOTNOTE.search(s) and not any(DATE.search(b) for b in BRACKETED.findall(s)):
            found.append("unsourced superlative")
        for kind in found:
            if (kind, s) not in seen:
                seen.add((kind, s))
                out.append((kind, s))
    return out


def print_own(dangers: List[Tuple[str, str]], also: List[Tuple[str, str]] = ()) -> None:
    """One line for each: the sentence and its fix. `also`: findings on a sentence that already has a DANGER line."""
    for kind, s in dangers:
        print(f"  {'DANGER':<8} {claim_id(s)}  [{kind}]  {s}  fix: {OWN_FIX[kind]}")
    for kind, s in also:
        print(f"  {'also':<8} {claim_id(s)}  [{kind}]  {s}  fix: {OWN_FIX[kind]}")


def own_summary(dangers: List[Tuple[str, str]], path: str) -> str:
    c = Counter(kind for kind, _ in dangers)
    return (f"{len(dangers)} sentence(s) of {os.path.basename(path)} fail on their own: "
            f"{c['undated absence']} undated absence, {c['unsourced superlative']} unsourced superlative.")


def selected(c: dict, only: List[str]) -> bool:
    """Which upstream claims must travel. 'all' (default), or any of: hedged, not_found, sourced, dated, must."""
    if not only or "all" in only:
        return True
    m = c["markers"]
    o = origin_class(m)
    tests = {
        "hedged": o == "hedged",
        "not_found": o == "not_found",
        "sourced": m.get("provenance") not in (None, "none stated"),
        "dated": bool(m.get("temporal")),
        "must": bool(c.get("must")),
    }
    return any(tests.get(k, False) for k in only)


def check(path: str, stage: str, against: List[str], ledger: str, fail_on: List[str], min_words: int, only: List[str],
          own: bool = True) -> int:
    """Check the file against the upstream claims; with `own`, also read its sentences on their own (own_dangers)."""
    rows = load(ledger)
    upstream: List[dict] = []
    by_id: Dict[str, dict] = {}
    for r in rows:  # a sentence carried word for word is one claim, however many stages hold it
        if r["kind"] == "claim" and r["stage"] in against and selected(r, only):
            if r["claim_id"] in by_id:
                by_id[r["claim_id"]]["must"] = by_id[r["claim_id"]].get("must") or r.get("must")
            else:
                by_id[r["claim_id"]] = r
                upstream.append(r)
    text = open(path, encoding="utf-8").read()
    own_found = own_dangers(text) if own else []
    if not upstream:
        print(f"no claims registered at stage(s) {against} matching --only {only}; run register first", file=sys.stderr)
        if not (own_found and "danger" in fail_on):
            return 2
        # nothing upstream is selected, but a sentence that fails on its own must not pass for "nothing to check"
        register(path, stage, ledger, min_words, quiet=True)
        print(f"check {os.path.basename(path)} (stage '{stage}'): no upstream claim under --only {','.join(only)}; "
              f"its own sentences\n")
        print_own(own_found)
        print("\n" + own_summary(own_found, path))
        print(f"gate: FAIL ({len(own_found)} claims; fail-on = {','.join(fail_on)})")
        return 1
    # the outgoing artifact is itself registered at its stage, so later steps can be checked against it
    register(path, stage, ledger, min_words, quiet=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    hops = []
    n_preserved = n_changed = n_dropped = n_danger = n_dropped_must = 0
    print(f"check {os.path.basename(path)} (stage '{stage}') against {len(upstream)} claims from {against}\n")
    for c in upstream:
        kind, span = find(c["text"], c["claim_id"], lines, text, must=bool(c.get("must")))
        present = kind is not None
        f = fate(c["markers"], c["text"], span) if present else {"epistemic": "n/a", "existential": "n/a", "provenance": "n/a", "temporal": "n/a"}
        if not present:
            mark, n_dropped = ("DROPPED!" if c.get("must") else "DROPPED"), n_dropped + 1
            n_dropped_must += 1 if c.get("must") else 0
        elif dangerous(f):
            mark, n_danger, n_changed = "DANGER", n_danger + 1, n_changed + 1
        elif changed(f):
            mark, n_changed = "CHANGED", n_changed + 1
        else:
            mark, n_preserved = "ok", n_preserved + 1
        fates = ", ".join(f"{k} {v}" for k, v in f.items() if v not in ("n/a", "kept", "unknown"))
        print(f"  {mark:<8} {c['claim_id']}  [{origin_class(c['markers'])}]  {c['text'][:90]}" + (f"\n            {fates}" if fates else ""))
        hops.append({"kind": "hop", "claim_id": c["claim_id"], "from_stage": c["stage"], "to_stage": stage,
                     "to_file": os.path.basename(path), "present": present, "match": kind, "span": span,
                     "origin": origin_class(c["markers"]), "fate": f, "ts": now()})
    append(ledger, hops)
    held = [normalise(h["span"]) for h in hops if h["present"] and h["span"] and dangerous(h["fate"])]
    also = [(k, s) for k, s in own_found if any(x and (x in normalise(s) or normalise(s) in x) for x in held)]
    own_found = [d for d in own_found if d not in also]
    print_own(own_found, also)
    n = len(upstream)
    print(f"\n{n} upstream claims: {n_preserved} present with status preserved, {n_changed} present with status changed "
          f"({n_danger} hardened or inverted), {n_dropped} dropped ({n_dropped_must} of them marked must-travel).")
    if own_found or also:
        print(own_summary(own_found, path) + (f" Also on a sentence held above: {len(also)}." if also else ""))
    bad = ((n_dropped if "dropped" in fail_on else 0) + (n_danger if "danger" in fail_on else 0)
           + ((n_changed - (n_danger if "danger" in fail_on else 0)) if "changed" in fail_on else 0)
           + (n_dropped_must if "dropped_must" in fail_on and "dropped" not in fail_on else 0)
           + (len(own_found) if "danger" in fail_on else 0))
    if bad:
        print(f"gate: FAIL ({bad} claims; fail-on = {','.join(fail_on)})")
        return 1
    print("gate: pass")
    return 0


# ---------- report and flow ----------
def report(ledger: str) -> None:
    rows = load(ledger)
    claims = [r for r in rows if r["kind"] == "claim"]
    hops = [r for r in rows if r["kind"] == "hop"]
    per_stage = Counter(c["stage"] for c in claims)
    print("claims registered: " + ", ".join(f"{s} {n}" for s, n in per_stage.items()))
    by = defaultdict(list)
    for h in hops:
        by[(h["from_stage"], h["to_stage"])].append(h)
    for (a, b), hs in by.items():
        n = len(hs)
        present = [h for h in hs if h["present"]]
        preserved = [h for h in present if not changed(h["fate"])]
        danger = [h for h in present if dangerous(h["fate"])]
        print(f"{a} -> {b}: {len(present)}/{n} present; {len(preserved)}/{len(present) or 1} of those with status preserved; "
              f"{len(danger)} hardened or inverted; by origin: " +
              ", ".join(f"{o} {sum(1 for h in present if h['origin'] == o)}/{sum(1 for h in hs if h['origin'] == o)} present"
                        for o in ("hedged", "not_found", "flat") if any(h["origin"] == o for h in hs)))


def flow(ledger: str, out: str, title: str) -> None:
    from .flowsvg import Flow, Node, Stage, render
    rows = load(ledger)
    hops = [r for r in rows if r["kind"] == "hop"]
    if not hops:
        print("no hops in the ledger yet; run check first", file=sys.stderr)
        return
    colors = {"hedged": "#e5862e", "not_found": "#d9b92f", "flat": "#6f9f6f"}
    RED = "#c0392b"
    # a must-travel claim that did not arrive is a loss, not word-matched noise: drawn red like a hardened one (K1.0d)
    must = {r["claim_id"] for r in rows if r.get("kind") == "claim" and r.get("must")}
    stages_order: List[str] = []
    for h in hops:
        if h["to_stage"] not in stages_order:
            stages_order.append(h["to_stage"])
    first_from = hops[0]["from_stage"]
    # with more than one hop, claims first registered at a later stage also start in this column
    origin_label = f"claims at '{first_from}'" if len({h["from_stage"] for h in hops}) == 1 else "claims where first registered"
    st0 = Stage(origin_label, [Node(f"o::{k}", lab, colors[k]) for k, lab in
                                              (("hedged", "hedged"), ("not_found", "'not found' or 'unknown'"), ("flat", "stated flat"))])
    sts = [st0]
    for s in stages_order:
        sts.append(Stage(f"at '{s}'", [Node(f"{s}::preserved", "present, status preserved", "#5b8c5a"),
                                       Node(f"{s}::changed", "present, status changed", "#d98c5f"),
                                       Node(f"{s}::danger", "hardened or inverted", RED, stroke=RED, emphasis=True,
                                            min_px=14),
                                       Node(f"{s}::lost", "dropped, must-travel", RED, "flow stops here", stroke=RED,
                                            emphasis=True, min_px=14),
                                       Node(f"{s}::dropped", "dropped", "#9a9a9a", "flow stops here")]))
    fl = Flow(sts, title=title, subtitle="Binary per claim per hop. Ribbon colour = what the claim carried at origin; "
                                         "red = hardened, or a must-travel claim dropped. Counts only.")
    # one link set per hop, grouped by origin; a claim dropped at a hop stops there
    prev_node = {}
    for s in stages_order:
        hs = [h for h in hops if h["to_stage"] == s]
        cnt = Counter()
        for h in hs:
            src = prev_node.get(h["claim_id"], f"o::{h['origin']}")
            if not h["present"]:
                dst = f"{s}::lost" if h["claim_id"] in must else f"{s}::dropped"
            elif dangerous(h["fate"]):
                dst = f"{s}::danger"
            elif changed(h["fate"]):
                dst = f"{s}::changed"
            else:
                dst = f"{s}::preserved"
            cnt[(src, dst, h["origin"])] += 1
            if h["present"]:
                prev_node[h["claim_id"]] = dst
        for (src, dst, o), v in cnt.items():
            col = RED if (src.endswith("::danger") or dst.endswith(("::danger", "::lost"))) else colors[o]
            fl.add(src, dst, v, col, f"{src} -> {dst} ({o}): {v}")
    n_claims = len({h["claim_id"] for h in hops})
    fl.footnote = [f"{n_claims} claims, {len(stages_order)} hop(s). Matching: explicit [c:id] tags, else a third of content words (at least two; two thirds for a must-travel claim) on one line, else a 60-character substring.",
                   "Paraphrase and translation are missed, and a line sharing enough words can pass for a claim, so 'dropped' and 'present' are approximate.",
                   f"Generated {now()} by ledger.py; no claim text in this file."]
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(fl, width=1500, height=720, margin_left=230, margin_right=260))
    print(f"wrote {out}")


def run(folder: str, only: List[str], fail_on: List[str], min_words: int, against_all: bool, title: str) -> int:
    """A folder of stage files in order (01-scout.md, 02-message.md, ...): register the first, check each next
    one against the previous stage (or all previous with --against-all), then report and draw. The ledger and
    the picture live in the folder. Re-running rebuilds both from scratch."""
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".md", ".txt")) and not f.startswith("."))
    if len(files) < 2:
        print("need at least two stage files (e.g. 01-scout.md, 02-message.md)", file=sys.stderr)
        return 2
    ledger = os.path.join(folder, "ledger.jsonl")
    if os.path.exists(ledger):
        os.remove(ledger)

    def stage_of(fn: str) -> str:
        return re.sub(r"^\d+[-_ ]*", "", os.path.splitext(fn)[0])

    worst = 0
    checked = 0
    stages_done: List[str] = []
    for i, fn in enumerate(files):
        path = os.path.join(folder, fn)
        st = stage_of(fn)
        if i == 0:
            register(path, st, ledger, min_words)
        else:
            against = stages_done if against_all else [stages_done[-1]]
            print()
            rc = check(path, st, against, ledger, fail_on, min_words, only, own=i == len(files) - 1)
            if rc == 2:  # nothing selected at this hop: its own claims still count for the next ones
                print(f"nothing to check at stage '{st}' under --only {','.join(only)}")
                register(path, st, ledger, min_words, quiet=True)
            else:
                checked += 1
                worst = max(worst, rc)
        stages_done.append(st)
    print()
    report(ledger)
    flow(ledger, os.path.join(folder, "flow.svg"), title or f"Where did the claims go? {os.path.basename(os.path.abspath(folder))}")
    unregistered = 0
    for fn in files:
        with open(os.path.join(folder, fn), encoding="utf-8") as f:
            unregistered += len(mark_warnings(f.read(), min_words))
    if unregistered:
        print(f"marks: {unregistered} must-travel mark(s) not registered, see the warnings above")
    if not checked:
        print(f"gate: NOTHING TO CHECK (no claim selected at any stage under --only {','.join(only)})")
        return 2
    return worst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"{NAME} {__version__}")
    ap.add_argument("--ledger", default="ledger.jsonl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run", help="process a folder of ordered stage files end to end")
    rn.add_argument("folder"); rn.add_argument("--only", default="all")
    rn.add_argument("--fail-on", default="dropped,danger", help="what fails the gate: any of dropped,changed,danger,dropped_must (dropped_must: only a missing [!] claim fails)")
    rn.add_argument("--min-words", type=int, default=3); rn.add_argument("--against-all", action="store_true"); rn.add_argument("--title", default="")
    r = sub.add_parser("register"); r.add_argument("file"); r.add_argument("--stage", required=True); r.add_argument("--min-words", type=int, default=3)
    c = sub.add_parser("check"); c.add_argument("file"); c.add_argument("--stage", required=True); c.add_argument("--against", required=True)
    c.add_argument("--fail-on", default="dropped,danger", help="what fails the gate: any of dropped,changed,danger,dropped_must"); c.add_argument("--min-words", type=int, default=3)
    c.add_argument("--only", default="all", help="which upstream claims must travel: all, or any of hedged,not_found,sourced,dated,must")
    sub.add_parser("report")
    fl = sub.add_parser("flow"); fl.add_argument("--out", default="flow.svg"); fl.add_argument("--title", default="Where did the claims go?")
    a = ap.parse_args()
    if a.cmd == "run":
        sys.exit(run(a.folder, parse_only(a.only), list(parse_fail_on(a.fail_on)), a.min_words, a.against_all, a.title))
    if a.cmd == "register":
        register(a.file, a.stage, a.ledger, a.min_words)
    elif a.cmd == "check":
        sys.exit(check(a.file, a.stage, [s.strip() for s in a.against.split(",")], a.ledger, list(parse_fail_on(a.fail_on)),
                       a.min_words, parse_only(a.only)))
    elif a.cmd == "report":
        report(a.ledger)
    elif a.cmd == "flow":
        flow(a.ledger, a.out, a.title)


if __name__ == "__main__":
    main()
