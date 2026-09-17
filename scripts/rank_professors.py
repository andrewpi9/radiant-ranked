#!/usr/bin/env python3
"""Standalone runner: `python scripts/rank_professors.py [options]`.

Thin shim so the script works from a clean checkout with no install step; the
logic lives in the importable `radiant_rank` package.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from radiant_rank.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
