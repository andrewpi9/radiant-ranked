#!/usr/bin/env python3
"""Standalone runner: `python scripts/ingest_rmp.py [options]`.

Kept as a thin shim so the script works from a clean checkout with no install
step; all the logic lives in the importable `radiant_ingest` package.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from radiant_ingest.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
