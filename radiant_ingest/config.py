"""Configuration for the RateMyProfessors ingestion pass.

Every tunable reads an environment variable so a refresh run can be retimed
without editing code (e.g. ``RMP_REQUEST_DELAY=1.5 make ingest``).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("RADIANT_DATA_DIR", PROJECT_ROOT / "data"))

# Final deliverable: deduped, validated, school-tagged professor records.
RAW_JSON_PATH = DATA_DIR / "professors_raw.json"
# Durable append log written page-by-page. This is what makes a crashed run
# resumable without re-pulling; RAW_JSON_PATH is derived from it at the end.
RAW_JSONL_PATH = DATA_DIR / "professors_raw.jsonl"
CHECKPOINT_PATH = DATA_DIR / "ingest_checkpoint.json"

GRAPHQL_URL = os.environ.get("RMP_GRAPHQL_URL", "https://www.ratemyprofessors.com/graphql")

# Base64 of "test:test". Not a secret — RMP ships this in their public frontend
# bundle and every community client uses it. Overridable if it ever rotates.
RMP_AUTH = os.environ.get("RMP_AUTH", "Basic dGVzdDp0ZXN0")

HEADERS = {
    "Authorization": RMP_AUTH,
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.ratemyprofessors.com/",
    "Origin": "https://www.ratemyprofessors.com",
}


def school_gid(legacy_id: int) -> str:
    """RMP global IDs are base64 of ``<Type>-<legacyId>``."""
    return base64.b64encode(f"School-{legacy_id}".encode()).decode()


# Schools to ingest. `expected_name` is only a drift check — a mismatch warns,
# it does not fail, since RMP occasionally renames schools.
#
# Legacy IDs verified 2026-09-03 against node(id:) — see DEVELOPMENT.md.
TARGET_SCHOOLS: list[dict[str, object]] = [
    {
        "legacy_id": 1232,
        "expected_name": "The University of North Carolina at Chapel Hill",
        "short_name": "UNC Chapel Hill",
    },
    {
        "legacy_id": 1350,
        "expected_name": "Duke University",
        "short_name": "Duke",
    },
]

# Pagination / politeness. 100 keeps checkpoint granularity tight; the API will
# serve 1000 but large pages mean coarser resume and heavier queries for them.
PAGE_SIZE = int(os.environ.get("RMP_PAGE_SIZE", "100"))
REQUEST_DELAY = float(os.environ.get("RMP_REQUEST_DELAY", "0.6"))
INTER_SCHOOL_DELAY = float(os.environ.get("RMP_INTER_SCHOOL_DELAY", "2.0"))

# Retry / backoff.
MAX_RETRIES = int(os.environ.get("RMP_MAX_RETRIES", "5"))
BACKOFF_BASE = float(os.environ.get("RMP_BACKOFF_BASE", "1.0"))
BACKOFF_MAX = float(os.environ.get("RMP_BACKOFF_MAX", "60.0"))
REQUEST_TIMEOUT = float(os.environ.get("RMP_REQUEST_TIMEOUT", "30.0"))

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Validation: warn if a school's pulled count drifts from the API's own
# resultCount by more than this fraction.
COUNT_TOLERANCE = float(os.environ.get("RMP_COUNT_TOLERANCE", "0.01"))
