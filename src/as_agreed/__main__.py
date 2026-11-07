"""python3 -m as_agreed {reconcile,demo,conformance} and the ledger's own {run,register,check,report,flow}.

`run`, `register`, `check`, `report` and `flow` are today's command line, delegated unchanged to
`as_agreed.ledger`. `reconcile <folder>` is the SDK's folder run: every link checked against the
registered as-agreed record, numeric file order, the README's recommended selection.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import NAME, __version__

LEDGER_COMMANDS = ("run", "register", "check", "report", "flow")


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in LEDGER_COMMANDS:
        from . import ledger
        sys.argv = [f"{NAME}"] + argv
        try:
            ledger.main()
        except SystemExit as e:
            return int(e.code or 0)
        return 0
    ap = argparse.ArgumentParser(prog=NAME, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"{NAME} {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rc = sub.add_parser("reconcile", help="check every stage file of a folder against the registered as-agreed record")
    rc.add_argument("folder")
    rc.add_argument("--fail-on", default="danger,dropped_must", help="for claims: any of danger,dropped_must,changed,dropped")
    rc.add_argument("--on-undecided", default=None, choices=("hold", "record"),
                    help="at the commit, a binding commitment no tier could decide: hold (default) or record")
    rc.add_argument("--min-words", type=int, default=3)
    rc.add_argument("--retries", type=int, default=0, help="reflective retries at the commit (0 to 3)")
    rc.add_argument("--out", default=None, help="where ledger.jsonl, counts.csv and flow.svg go (default <folder>/as-agreed)")
    rc.add_argument("--title", default="")
    map_flags = ((("--values",), "put the agreed and arrived values on flow.svg (that file is then not for sending)"),
                 (("--expand-claims",), "draw one ribbon per claim on flow.svg instead of one grey band per link"))
    for names, text in map_flags:
        rc.add_argument(*names, action="store_true", help=text)
    dm = sub.add_parser("demo", help="the planted-failure demo: a change that passes every schema check and is held before the commit")
    dm.add_argument("--fixture", choices=("widened", "dropped"), default="widened")
    dm.add_argument("--no-retry", action="store_true")
    dm.add_argument("--out", default=None)
    for names, text in map_flags:
        dm.add_argument(*names, action="store_true", help=text)
    cf = sub.add_parser("conformance", help="run the suite of SPEC §10 against an adapter registered by name (default: plain)")
    cf.add_argument("adapter", nargs="?", default="plain", help="plain; langgraph and temporal when their adapters land")
    cf.add_argument("--only", default="", help="comma-separated sentence ids")
    a = ap.parse_args(argv)
    if a.cmd == "reconcile":
        from .errors import AsAgreedError
        from .importers.stages import run_folder
        from .ledger import parse_fail_on as ledger_fail_on
        try:
            code = run_folder(a.folder, fail_on=ledger_fail_on(a.fail_on), on_undecided=a.on_undecided,
                              min_words=a.min_words, out_dir=a.out, retries=a.retries, title=a.title)
            if a.values or a.expand_claims:  # the run's files stand; only its map is drawn again, as asked
                import os
                from .report import redraw
                out = a.out or os.path.join(a.folder, "as-agreed")
                redraw(os.path.join(out, "ledger.jsonl"), os.path.join(out, "flow.svg"), title=a.title or
                       f"Where did the commitments go? {os.path.basename(a.folder)}", values=a.values,
                       expand_claims=a.expand_claims)
            return code
        except (AsAgreedError, ValueError, json.JSONDecodeError) as e:
            # SPEC §6: a bad argument or a bad input is code 2 (1 is only a standing hold); one line, no traceback;
            # an error that carries its own code keeps it, the others are usage errors
            print(f"{NAME}: {' '.join(str(e).split())}", file=sys.stderr)
            return int(getattr(e, "code", 2))
    if a.cmd == "demo":
        from .demo import run_demo
        if not (a.values or a.expand_claims):
            return run_demo(a.fixture, retry=not a.no_retry, out_dir=a.out)
        import os
        import tempfile
        from .demo import FIXTURES
        from .report import redraw
        out = a.out or tempfile.mkdtemp(prefix="as-agreed-demo-")
        code = run_demo(a.fixture, retry=not a.no_retry, out_dir=out)
        redraw(os.path.join(out, "ledger.jsonl"), os.path.join(out, "flow.svg"),
               title=f"Where did the commitments go? {FIXTURES[a.fixture]}", values=a.values, expand_claims=a.expand_claims)
        return code
    if a.cmd == "conformance":
        from .conformance import run_suite, load_factory
        results = run_suite(load_factory(a.adapter), only=[s for s in a.only.split(",") if s.strip()] or None)
        failed = [r for r in results if r.status == "fail"]
        for r in results:
            print(f"{r.id:<4} {r.status:<5} {r.message}")
        print(f"\n{sum(1 for r in results if r.status == 'pass')} of {len(results)} pass, "
              f"{sum(1 for r in results if r.status == 'skip')} skipped (named above), {len(failed)} failed")
        return 1 if failed else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
