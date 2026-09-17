"""Configuration for the simulated-Elo rating engine."""

from __future__ import annotations

import os

from radiant_ingest.config import DATA_DIR, RAW_JSON_PATH  # noqa: F401  (re-exported)

RANKINGS_PATH = DATA_DIR / "rankings.json"

# RMP's rating scale.
RATING_MIN = 1.0
RATING_MAX = 5.0

# A professor needs this many ratings to appear on the leaderboard. Below it the
# posterior is so wide that a rank would be noise dressed up as a number.
MIN_RATINGS = int(os.environ.get("RANK_MIN_RATINGS", "5"))

# Pseudo-ratings pulling each professor's posterior toward the global mean.
# This is what stops a lone 5.0 from outranking a well-established 4.8: with
# prior strength 5, one rating is competing against five pseudo-ratings of
# average quality.
PRIOR_STRENGTH = float(os.environ.get("RANK_PRIOR_STRENGTH", "5.0"))

# Elo simulation.
ELO_START = float(os.environ.get("RANK_ELO_START", "1500.0"))
ELO_ROUNDS = int(os.environ.get("RANK_ELO_ROUNDS", "500"))
# Independent tournaments averaged together. One tournament leaves the top of
# the board seed-dependent (measured: only 32/50 of the top 50 survived a seed
# change). Averaging 10 takes that to 47/50 and makes the top 10 exactly
# reproducible, and beats a single long run at the same total compute.
ELO_TOURNAMENTS = int(os.environ.get("RANK_ELO_TOURNAMENTS", "10"))
# K decays across rounds: move fast early, settle late.
ELO_K_START = float(os.environ.get("RANK_ELO_K_START", "32.0"))
ELO_K_END = float(os.environ.get("RANK_ELO_K_END", "6.0"))

# Fixed by default so the leaderboard is reproducible run to run.
RANDOM_SEED = int(os.environ.get("RANK_SEED", "42"))

# Departments smaller than this still get ranked, but are excluded from the
# printed department boards (a "top 3 of 3" board is not interesting).
MIN_DEPARTMENT_SIZE = int(os.environ.get("RANK_MIN_DEPARTMENT_SIZE", "10"))

# Short labels for report columns, keyed by RMP school global id. Schools not
# listed fall back to an abbreviation derived from the name.
SCHOOL_LABELS = {
    "U2Nob29sLTEyMzI=": "UNC",
    "U2Nob29sLTEzNTA=": "Duke",
}


def school_label(school_id: str, school_name: str) -> str:
    """A short column label — RMP's official names are far too long to tabulate."""
    known = SCHOOL_LABELS.get(school_id)
    if known:
        return known
    name = (school_name or "").removeprefix("The ").strip()
    words = [w for w in name.split() if w[:1].isupper()]
    if len(words) >= 3:
        return "".join(w[0] for w in words)[:5]
    return name[:5] or "?"
