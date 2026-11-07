"""Errors and codes of SPEC §6."""
from __future__ import annotations

from typing import Any, List, Optional


class AsAgreedError(Exception):
    code = 2


class NoCommitment(AsAgreedError):
    """A check or a step ran before register, or with an as-agreed record not in the sink or the context."""
    code = 2


class AsAgreedExists(AsAgreedError):
    """A second registration in the run without a higher major version."""
    code = 2


class EmptyAsAgreed(AsAgreedError):
    """An as-agreed record with no commitment and no claim."""
    code = 2


class WorkflowContextError(AsAgreedError):
    """The decorator ran in Temporal workflow code."""
    code = 2


class SchemaError(AsAgreedError):
    """An output failed the declared schema; the commit is withheld; non-retryable on engines."""
    code = 3

    def __init__(self, problems: List[str], step: str = ""):
        self.problems = list(problems)
        self.step = step
        super().__init__(f"schema failed at {step or 'the step'}: " + "; ".join(self.problems))


class DriftHold(AsAgreedError):
    """The hold stands after the retries; raised once by the plain-Python adapter."""
    code = 1

    def __init__(self, lines: List[str], hold: Optional[Any] = None, outcome: Optional[Any] = None):
        self.lines = list(lines)
        self.hold = hold
        self.outcome = outcome
        super().__init__("drift hold: " + " | ".join(self.lines))
