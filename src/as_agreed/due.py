"""The due list (0.1.0): which fields a step owes for each commitment it serves, from the step's declared binding.

A binding is a mapping: `serves` (the ids of the commitments the step serves), `kind` (`sets_terms`, a brief that sets
the terms for the next step; `takes_effect`, the step whose output acts; `mentions`, a step that owes nothing), and
optionally `author` and `recipient` (who wrote the step and who receives it, by the names the record uses)."""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from .commitment import AsAgreed, Commitment
from .drift import DROPPED, UNDECLARED, Finding

KINDS = ("sets_terms", "takes_effect", "mentions")
KEYS = ("serves", "kind", "author", "recipient")
ACT_HEADS = ("act", "limit", "deadline", "condition", "acceptance_criteria")


def _name(value: Any) -> Optional[str]:
    return " ".join(str(value).split()).casefold() if value is not None else None


class Due:
    """One step's binding, checked against the as-agreed record it serves."""

    def __init__(self, binding: Mapping[str, Any], agreed: AsAgreed):
        if not isinstance(binding, Mapping):
            raise ValueError("a binding is a mapping with serves, kind, and optionally author and recipient")
        unknown = sorted(str(k) for k in binding if k not in KEYS)
        if unknown:
            raise ValueError(f"binding keys not known: {', '.join(unknown)}; use {', '.join(KEYS)}")
        self.kind = binding.get("kind")
        if self.kind not in KINDS:
            raise ValueError(f"binding kind is one of {', '.join(KINDS)}, not {self.kind!r}")
        serves = binding.get("serves")
        if isinstance(serves, str) or not isinstance(serves, (list, tuple)):
            raise ValueError("binding serves is a list of commitment ids")
        held = {c.commitment_id for c in agreed.commitments}
        unknown_ids = [str(s) for s in serves if s not in held]
        if unknown_ids:
            raise ValueError(f"binding serves commitments the record does not hold: {', '.join(unknown_ids)}")
        self.serves = set(serves)
        self.author = binding.get("author")
        self.recipient = binding.get("recipient")

    def owed(self, c: Commitment) -> List[str]:
        """V4's due list. A brief owes every field but the obligee. An act owes what it does (act, limits, deadline,
        conditions, criteria), and the obligor only when the obligor wrote it or receives it: a third party's report
        may say "the order went out" without naming who sent it (X60, 2026-11-15)."""
        fields = c.field_paths()
        if self.kind == "sets_terms":
            return [f for f in fields if f != "obligee"]
        if self.kind == "takes_effect":
            heads = set(ACT_HEADS)
            if self.author is None or _name(c.obligor) in (_name(self.author), _name(self.recipient)):
                heads.add("obligor")
            return [f for f in fields if f.partition(".")[0] in heads]
        return []

    def apply(self, c: Commitment, findings: List[Finding]) -> List[Finding]:
        """An owed field whose key is missing is DROPPED by code. A field the step does not owe gives no finding for
        not being there, and a change to it still counts."""
        owed = set(self.owed(c))
        out: List[Finding] = []
        for f in findings:
            if f.field in owed and f.missing_key and f.undecided == UNDECLARED:
                out.append(Finding(f.field, DROPPED, f"owed by this {self.kind} step, and its key {f.missing_key} "
                                                     f"is not in the output", f.agreed, missing_key=f.missing_key))
            elif f.field in owed or not (f.drift_class == DROPPED or f.undecided == UNDECLARED):
                out.append(f)
        return out
