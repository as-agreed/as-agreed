#!/usr/bin/env python3
"""Run As-Agreed from a clone, without an install: `python3 ledger.py run <folder> ...`.

The code lives in `src/as_agreed/`; installed, the same tool is the `as-agreed` command.
This file stays at the root because scripts and documents run the gate by this path.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from as_agreed.ledger import main  # noqa: E402

if __name__ == "__main__":
    main()
