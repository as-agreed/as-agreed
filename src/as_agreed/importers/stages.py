"""The stage folder (SPEC §8.1) and the common runner every importer feeds (SPEC §8, second paragraph)."""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..adapters.plain import PlainAdapter
from ..errors import DriftHold, EmptyAsAgreed, SchemaError
from ..hold import DEFAULT_FAIL_ON
from ..report import Report, build

PREFIX = re.compile(r"^(\d+)[-_ ]*(.*)$")
COMMITMENTS_FILE = "commitments.json"
PROSE = (".md", ".txt")


def stage_files(folder: str, notes: Optional[List[str]] = None) -> List[str]:
    """The stage files of SPEC §8.1, sorted by numeric prefix, then by name: the first is the order and the one
    with the highest prefix is the commit step. A stage is a prefixed `.md` or `.txt`, or a prefixed `.json` with
    no same-stem `.md` or `.txt` (its outputs are that JSON). A `.json` with a same-stem prose file declares that
    stage's outputs, a `.json` whose number a prose stage already holds is an auxiliary file (the scripted retry
    beside a commit), and `commitments.json` is neither: none of them is a stage, and none is warned about. A `.md` or
    `.txt` with no numeric prefix is not a stage either, and a line naming it is appended to `notes` when given, so
    that a stray file is seen and cannot become the commit."""
    entries = sorted(f for f in os.listdir(folder) if not f.startswith(".") and f.lower() != COMMITMENTS_FILE)
    prose = [f for f in entries if f.lower().endswith(PROSE)]
    stems = {os.path.splitext(f)[0] for f in prose}
    held = {int(m.group(1)) for m in (PREFIX.match(f) for f in prose) if m}  # the numbers prose stages hold
    stages = []
    for f in entries:
        is_json = f.lower().endswith(".json") and os.path.splitext(f)[0] not in stems  # else the outputs of a prose stage
        if f not in prose and not is_json:
            continue
        m = PREFIX.match(f)
        if not m:  # only a prose file was ever a stage, so only a prose file is named; an unprefixed `.json` never was
            if f in prose and notes is not None:
                notes.append(f"{f} is not a stage: its name has no numeric prefix (01-, 02-, ...)")
        elif not (is_json and int(m.group(1)) in held):
            stages.append(f)

    def key(fn: str):
        return (int(PREFIX.match(fn).group(1)), fn)

    return [os.path.join(folder, f) for f in sorted(stages, key=key)]


def stage_name(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    m = PREFIX.match(base)
    return (m.group(2) or base) if m else base


def read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(f"{path}: {e.msg}", e.doc, e.pos) from None


def stage_outputs(path: str) -> Any:
    """A stage's outputs: the `.json` stage itself, else its same-stem `.json` when it has one (declared fields), else its text (prose)."""
    if path.lower().endswith(".json"):
        return read_json(path)
    declared = os.path.splitext(path)[0] + ".json"
    if os.path.exists(declared):
        return read_json(declared)
    with open(path, encoding="utf-8") as f:
        return f.read()


RECORD_KEYS = ("commitments", "proxy", "drafter")


@dataclass
class Declaration:
    """What `commitments.json` declares: the commitments, and for a proxy record its provenance (SPEC §8.1, draft 8)."""
    commitments: List[Any]
    proxy: bool = False
    drafter: Optional[Dict[str, str]] = None


def declared_record(folder: str) -> Declaration:
    """`commitments.json` beside the stage files: a list of records with the fields of SPEC §1.2, or an object holding
    that list under `commitments` with `proxy` and `drafter` (model, version, date, prompt_id) beside it."""
    path = os.path.join(folder, COMMITMENTS_FILE)
    if not os.path.exists(path):
        return Declaration([])
    data = read_json(path)
    if isinstance(data, list):
        return Declaration(data)
    if not isinstance(data, dict) or "commitments" not in data:
        raise ValueError(f"{path} must hold a list of commitments, or an object with that list under commitments "
                         f"(and proxy, drafter beside it)")
    unknown = sorted(k for k in data if k not in RECORD_KEYS)
    if unknown:
        raise ValueError(f"{path}: keys not known: {', '.join(unknown)}; it takes {', '.join(RECORD_KEYS)}")
    cs = data.get("commitments")
    if not isinstance(cs, list):
        raise ValueError(f"{path}: commitments must be a list of commitments")
    proxy = data.get("proxy", False)
    if not isinstance(proxy, bool):
        raise ValueError(f"{path}: proxy is true or false, not {proxy!r}")
    from ..commitment import check_drafter
    try:
        drafter = check_drafter(data.get("drafter"), proxy)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None
    return Declaration(cs, proxy, drafter)


def declared_commitments(folder: str) -> List[Any]:
    """The commitments `commitments.json` declares, in either of its two forms."""
    return declared_record(folder).commitments


@dataclass
class Link:
    """One link of an imported run: the step's name, its text, and, when the importer has them, the step's structured
    outputs, which are then what the step returns, so a link can declare fields (SPEC §8, draft 8)."""
    step: str
    text: str
    parent: Optional[str] = None
    outputs: Any = None

    @property
    def returned(self) -> Any:
        return self.text if self.outputs is None else self.outputs


def links_to_run(links: Sequence[Link], *, root_input: str, adapter: Optional[PlainAdapter] = None,
                 commitment_text: Optional[str] = None, commitments: Sequence[Any] = (), source: str = "trace",
                 out_dir: Optional[str] = None, fail_on: Tuple[str, ...] = DEFAULT_FAIL_ON, min_words: int = 3,
                 retries: int = 0, title: str = "", quiet: bool = True, drafter: Optional[Dict[str, str]] = None,
                 tiers: Iterable[Any] = (), on_missing_key: str = "dropped",
                 on_undecided: Optional[str] = None) -> Report:
    """Register the as-agreed record (the supplied order, else the task as stated marked proxy, with its drafter when a
    model drafted the commitments) and check every link: its `outputs` when the importer has them, else its text.
    `tiers`, `on_missing_key` and `on_undecided` reach every step as on `sdk.step`."""
    from .. import sdk
    ad = adapter or PlainAdapter(run_dir=out_dir or ".")
    tiers = tuple(tiers)
    if commitment_text is not None:
        agreed = sdk.register(commitment_text, commitments, adapter=ad, source=source, min_words=min_words, proxy=False,
                              drafter=drafter)
    else:
        try:
            agreed = sdk.register(root_input, commitments, adapter=ad, source=source, min_words=min_words, proxy=True,
                                  drafter=drafter)
        except EmptyAsAgreed:
            raise EmptyAsAgreed("the trace's root carries no text to register as the task as stated (content not captured, "
                                "or fewer than three content words); pass --commitment <order file>") from None
    n = len(links)
    for i, link in enumerate(links, 1):
        last = i == n

        def body(previous: Any, _out: Any = link.returned, **kw: Any) -> Any:
            return _out

        fn = sdk.step(link.step, agreed, commit=True if last else None, retries=retries if last else 0,
                      fail_on=fail_on, adapter=ad, tiers=tiers, on_missing_key=on_missing_key,
                      on_undecided=on_undecided)(body)
        try:
            fn(links[i - 2].returned if i > 1 else root_input)
        except DriftHold:
            pass
    rep = build(ad.records(), out_dir or ad.run_dir, title=title, run_id=ad.run_id)
    if not quiet:
        print(rep.text())
    return rep


def run_folder(folder: str, *, adapter: Optional[PlainAdapter] = None, fail_on: Tuple[str, ...] = DEFAULT_FAIL_ON,
               on_undecided: Optional[str] = None, min_words: int = 3, out_dir: Optional[str] = None, retries: int = 0,
               title: str = "", quiet: bool = False, on_missing_key: str = "dropped") -> int:
    """A folder of ordered stage files: the first is the order, every later file one link, the one with the highest
    numeric prefix the commit. `commitments.json` declares the as-agreed commitments; a same-stem `.json` declares
    a stage's fields, and a prefixed `.json` with no prose beside it is a stage of its own (`stage_files`)."""
    from .. import sdk
    folder = os.path.abspath(folder)
    notes: List[str] = []
    files = stage_files(folder, notes) if os.path.isdir(folder) else []
    for note in notes:  # once per file that is not a stage
        print(f"warning: {note}", file=sys.stderr)
    if len(files) < 2:
        print(f"need at least two stage files in {folder} (for example 01-order.md, 02-research.md)", file=sys.stderr)
        return 2
    out = out_dir or os.path.join(folder, "as-agreed")
    ad = adapter or PlainAdapter(run_dir=out, pipeline=os.path.basename(folder))
    with open(files[0], encoding="utf-8") as f:
        order = f.read()
    declared = declared_record(folder)  # a proxy record carries its provenance into the record and the report
    agreed = sdk.register(order, declared.commitments, adapter=ad, source=os.path.basename(files[0]),
                          min_words=min_words, pipeline=os.path.basename(folder), proxy=declared.proxy,
                          drafter=declared.drafter)
    previous: Any = order
    for i, path in enumerate(files[1:], 1):
        outputs = stage_outputs(path)
        last = i == len(files) - 1

        def body(prev: Any, _outputs: Any = outputs, **kw: Any) -> Any:
            return _outputs

        fn = sdk.step(stage_name(path), agreed, commit=True if last else None, retries=retries if last else 0,
                      fail_on=fail_on, on_undecided=on_undecided, adapter=ad, on_missing_key=on_missing_key)(body)
        try:
            fn(previous)
        except DriftHold:
            pass
        except SchemaError as e:  # the commit was withheld; the report carries the code, not a traceback
            print(f"{e}", file=sys.stderr)
        previous = outputs
    rep = build(ad.records(), out, title=title or f"Where did the commitments go? {os.path.basename(folder)}", run_id=ad.run_id)
    if not quiet:
        print(rep.text())
    if not agreed.commitments:  # nothing was compared against a commitment: a green run here would be a pass nobody earned
        print(f"no commitment was declared for {folder}: {COMMITMENTS_FILE} is missing or empty, so nothing was compared "
              f"against the as-agreed record; the claim ledger's own run is `python3 ledger.py run <folder>`", file=sys.stderr)
        if rep.code == 0:  # a hold (1) or a schema failure (3) found something and keeps its own code
            return 2
    return rep.code
