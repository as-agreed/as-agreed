"""As-Agreed: did what was agreed reach the step that acts on it, as agreed?

The working tool today is the ledger and gate in `as_agreed.ledger`: register the claims of one
step, check them at every later step, fail before the next step when one was dropped or hardened.
Run it from a clone with `python3 ledger.py`, or, installed, as the `as-agreed` command. The SDK
(a decorator on a pipeline step, the typed commitment record of what was agreed, a field-level
comparison at each step and the hold before the commit) is in this package since release 0.0.1,
public 2026-11-07; `as_agreed.sdk` and `python3 -m as_agreed demo` run it.
"""

NAME = "as-agreed"
__version__ = "0.0.3"

# The public surface is `as_agreed.sdk`, and it stays there: `import as_agreed.sdk as ab` is the line every
# example teaches, so moving it later would break the one line every user copies. The package root is deliberately
# not the surface, because four of the five verbs (check, hold, retry, report) are also module names here, and at
# the root those names resolve to the modules. `tests/test_surface.py` keeps both halves of that true.
