"""Where the record goes (SPEC §2.6). The library is the client of the As-Agreed service.

One record schema and two destinations. `LocalDestination` is a file store on this machine: the default, and the
only destination v0.1 implements. `RemoteDestination` is an interface only; the shared store it would write to is
the paid service and is not in this repository. Moving from one to the other changes this one argument, not the
integration: every record the core writes goes through the destination once, serialised by `encode`, and a remote
destination is handed those bytes unchanged. A remote destination must also answer `read` (the core reads the run's
records back at each step, for its history) and `reopen`. `report()` writes its own files to a local folder in
either mode.

A destination is the adapter's sink: `PlainAdapter(destination=LocalDestination("run/ledger.jsonl"))` is what the
default already does.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import List, Optional

from .adapters.base import JsonlSink, Sink, encode

__all__ = ["encode", "Envelope", "LocalDestination", "RemoteDestination", "verdict_of"]


def verdict_of(record: dict) -> Optional[str]:
    """The verdict a record carries, for a store that indexes by it: a hold record's `pass` or `hold`, a verdict
    record's drift class or claim status; None for every other kind of record."""
    if record.get("kind") == "hold":
        return "pass" if record.get("passed") else "hold"
    if record.get("kind") == "verdict":
        return record.get("unit_class")
    return None


@dataclass(frozen=True)
class Envelope:
    """What a remote destination is handed for one record, and nothing else."""
    workspace: str              # the shared store's account, set by whoever configures the destination
    run_id: str                 # the record's own run id
    record: bytes               # exactly the line LocalDestination would have written to the file
    verdict: Optional[str]      # verdict_of(record), carried beside it so the store need not parse it


class LocalDestination(JsonlSink):
    """The default: one JSON object per line in a file on this machine. Nothing is sent anywhere."""

    def reopen(self) -> "LocalDestination":
        return LocalDestination(self.path)


class RemoteDestination(Sink):
    """Interface only in v0.1. A subclass implements `send` (and `read`, `reopen`) against a shared store; this
    class builds the envelope, so every remote destination is handed the same bytes the local one writes."""

    def __init__(self, workspace: str):
        if not workspace:
            raise ValueError("a remote destination needs the workspace it writes to")
        self.workspace = workspace

    def append(self, record: dict) -> None:
        self.send(Envelope(self.workspace, str(record.get("run_id", "")), encode(record), verdict_of(record)))

    @abc.abstractmethod
    def send(self, envelope: Envelope) -> None: ...

    @abc.abstractmethod
    def read(self) -> List[dict]: ...

    @abc.abstractmethod
    def reopen(self) -> "RemoteDestination": ...
