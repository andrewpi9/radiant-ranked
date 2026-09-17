"""Modelling a professor's true quality as a Beta posterior.

RMP hands us a mean and a count, not the underlying ratings. We treat
``avg_rating`` rescaled to [0, 1] as an observed success proportion over
``num_ratings`` trials, and put a weak prior on it centred on the global mean.

The posterior does two useful things at once:

* its **mean** shrinks thin evidence toward the field average, and
* its **width** encodes how much we actually know about the professor,

so sampling from it (see :mod:`radiant_rank.elo`) makes uncertainty something a
professor has to overcome rather than something they benefit from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import config


# A prior mean of exactly 0 or 1 makes the prior contribute no mass to one side,
# so a professor with a perfect 5.0 (or a flat 1.0) average gets beta = 0 — not a
# valid Beta, and a crash partway through a tournament when it is sampled. A real
# field never sits near these bounds (UNC+Duke is 0.699), so clamping here only
# ever fires on a degenerate field, such as a single-professor department.
PRIOR_MEAN_BOUND = 0.02


def to_unit(avg_rating: float) -> float:
    """Map a 1-5 RMP rating onto [0, 1], clamped."""
    span = config.RATING_MAX - config.RATING_MIN
    unit = (float(avg_rating) - config.RATING_MIN) / span
    return min(1.0, max(0.0, unit))


def to_rating(unit: float) -> float:
    """Inverse of :func:`to_unit`, for reporting a shrunk score on the 1-5 scale."""
    return config.RATING_MIN + unit * (config.RATING_MAX - config.RATING_MIN)


@dataclass
class Contender:
    """One rankable professor plus the posterior we sample them from."""

    id: str
    first_name: str
    last_name: str
    department: str
    school_name: str
    school_id: str
    avg_rating: float
    avg_difficulty: float
    num_ratings: int
    would_take_again_percent: float
    alpha: float
    beta: float
    elo: float = field(default=0.0)
    # Spread of this professor's Elo across the ensemble's tournaments. Small
    # means the rank is well determined; large means they sit in a near-tie
    # that the underlying data cannot actually resolve.
    elo_stddev: float = field(default=0.0)

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


def posterior_mean(contender: Contender) -> float:
    """Posterior mean on [0, 1] — the shrunk estimate of true quality."""
    return contender.alpha / (contender.alpha + contender.beta)


def global_mean_unit(records: Iterable[dict[str, Any]]) -> float:
    """Rating-weighted mean quality across the field, on [0, 1].

    Weighted by ``num_ratings`` so the prior reflects the average *rating*
    rather than the average *professor*; otherwise thinly-rated outliers would
    drag the very anchor meant to discipline them.
    """
    total_weight = 0.0
    total = 0.0
    for rec in records:
        n = rec.get("num_ratings") or 0
        avg = rec.get("avg_rating")
        if n > 0 and avg:
            total += to_unit(avg) * n
            total_weight += n
    if total_weight <= 0:
        return 0.5
    return total / total_weight


def build_contenders(
    records: Sequence[dict[str, Any]],
    *,
    min_ratings: int | None = None,
    prior_strength: float | None = None,
    prior_mean: float | None = None,
) -> tuple[list[Contender], float]:
    """Filter to rankable professors and attach a Beta posterior to each.

    Returns ``(contenders, prior_mean_used)``.
    """
    min_ratings = config.MIN_RATINGS if min_ratings is None else min_ratings
    prior_strength = config.PRIOR_STRENGTH if prior_strength is None else prior_strength
    if prior_strength <= 0:
        # A zero-strength prior lets alpha or beta hit 0 for a perfect 5.0 or
        # 1.0 average, which is not a valid Beta and cannot be sampled.
        raise ValueError("prior_strength must be > 0")

    if prior_mean is None:
        prior_mean = global_mean_unit(records)
    # Keep the prior non-degenerate so alpha and beta stay strictly positive.
    prior_mean = min(max(prior_mean, PRIOR_MEAN_BOUND), 1.0 - PRIOR_MEAN_BOUND)

    contenders: list[Contender] = []
    for rec in records:
        n = rec.get("num_ratings") or 0
        avg = rec.get("avg_rating")
        if n < min_ratings or not avg:
            continue

        p = to_unit(avg)
        contenders.append(
            Contender(
                id=rec["id"],
                first_name=rec.get("first_name") or "",
                last_name=rec.get("last_name") or "",
                department=rec.get("department") or "Unknown",
                school_name=rec.get("school_name") or "Unknown",
                school_id=rec.get("school_id") or "",
                avg_rating=float(avg),
                avg_difficulty=rec.get("avg_difficulty"),
                num_ratings=int(n),
                would_take_again_percent=rec.get("would_take_again_percent"),
                alpha=p * n + prior_strength * prior_mean,
                beta=(1.0 - p) * n + prior_strength * (1.0 - prior_mean),
                elo=config.ELO_START,
            )
        )
    return contenders, prior_mean
