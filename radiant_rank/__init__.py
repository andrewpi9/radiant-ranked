"""Radiant Ranked — simulated-Elo rating engine.

Phase 3: turn the aggregate RateMyProfessors data pulled by `radiant_ingest`
into a ranked leaderboard.

RMP gives us aggregates (avg_rating, num_ratings), never head-to-head results,
so "matches" are simulated: each professor is modelled as a Beta posterior over
their true quality, and every match samples once from each. A professor with
400 ratings has a tight posterior and wins consistently; one with a single
5-star rating has a wide posterior and regresses toward the field.

Entry point: ``python scripts/rank_professors.py`` (see ``radiant_rank.cli``).
"""

from .model import Contender, build_contenders, posterior_mean, to_unit
from .elo import simulate
from .pipeline import rank

__all__ = [
    "Contender",
    "build_contenders",
    "posterior_mean",
    "to_unit",
    "simulate",
    "rank",
]
