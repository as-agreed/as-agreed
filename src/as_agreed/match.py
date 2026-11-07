"""Status of a claim at one link (SPEC §1.7): the upward direction, on the matching rules of as_agreed.ledger.

This module reads prose, because a claim's status lives in its words. It decides nothing about a commitment's
fields; that is `drift.py`, which reads declared fields only.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import ledger
from .commitment import Claim

CLAIM_STATUSES = ("PRESERVED", "CHANGED", "DANGER", "DROPPED")


def candidates(text: str) -> List[str]:
    """Claims are matched against sentences: each non-empty line is cut with the ledger's splitter, so a serialised
    value that holds several sentences on one line does not lend one sentence's words to another."""
    out: List[str] = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        parts = ledger.sentences(ln)
        out.extend(parts if parts else [ln.strip()])
    return out


def claim_status(c: Claim, text: str) -> Tuple[str, Dict[str, str], str, Optional[str]]:
    """(status, per-marker fates, reason, span) as ledger.py computes them, sentence by sentence."""
    lines = candidates(text)
    k, span = ledger.find(c.text, c.id, lines, text, must=c.must)
    if k is None or span is None:
        return "DROPPED", {}, "not found", None
    f = ledger.fate(c.markers, c.text, span)
    if ledger.dangerous(f):
        status = "DANGER"
    elif ledger.changed(f):
        status = "CHANGED"
    else:
        status = "PRESERVED"
    return status, f, marker_summary(f) + f"; span: {span[:120]}", span


def verbatim(c: Claim, text: str) -> bool:
    return ledger.normalise(c.text) in ledger.normalise(text)


def marker_summary(f: Dict[str, str]) -> str:
    return ", ".join(f"{mk} {v}" for mk, v in f.items() if v not in ("n/a", "kept", "unknown")) or "all markers kept"
