"""Attempts, invocations and idempotency keys (SPEC §2.4 and §1.4)."""
from __future__ import annotations

from .commitment import FORMAT
from .serialise import digest

MAX_RETRIES = 3


def idempotency_key(as_agreed_digest: str, run_id: str, step: str, invocation: int, inputs_hash: str) -> str:
    """One key per invocation: the as-agreed record, the run, the step, the invocation and the inputs; never the attempt."""
    return digest("key", as_agreed_digest, run_id, step, str(int(invocation)), inputs_hash)[:16]


def link_id(step: str, invocation: int, attempt: int) -> str:
    return f"{step}#{int(invocation)}@{int(attempt)}"


def retry_record(run_id: str, after: str, next_link: str, n_lines: int, ts: str, pipeline: str = "default") -> dict:
    return {"kind": "retry", "format": FORMAT, "pipeline": pipeline, "run_id": run_id, "link": next_link,
            "after": after, "reason_lines": int(n_lines), "ts": ts}
