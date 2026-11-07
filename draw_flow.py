#!/usr/bin/env python3
"""Draw a status-flow SVG from any per-claim CSV, from a clone: `python3 draw_flow.py claims.csv --stages ...`.

The code and its options are in `src/as_agreed/draw_flow.py` (`python3 draw_flow.py --help`).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from as_agreed.draw_flow import main  # noqa: E402

if __name__ == "__main__":
    main()
