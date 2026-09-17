"""Check the simulated tournament against a directly computed answer.

The Elo tournament is a Monte Carlo estimator of a quantity that can be
computed without simulating anything: each professor's probability of beating a
uniformly random opponent drawn from the field,

    P_i = E_j[ P(X_i > X_j) ],   X ~ the professor's Beta posterior

This module computes that directly by discretising each posterior onto a shared
grid, then reports how closely the Elo ordering agrees with it. If the two
disagree, the tournament has a bug or has not converged; if they agree, the
simulation is a defensible way to present a number the maths already supports.

No RNG is involved here, which is the point — it is an independent check, not a
second opinion from the same method.
"""

from __future__ import annotations

import logging
# `log` is the module logger everywhere in this package, so the natural
# logarithm is imported under a different name rather than shadowing it.
from math import exp, lgamma
from math import log as ln
from typing import Sequence

from .elo import spearman
from .model import Contender

log = logging.getLogger(__name__)

GRID = 1000


def win_probabilities(contenders: Sequence[Contender], grid: int = GRID) -> list[float]:
    """P(beat a uniformly random opponent) for each contender, computed exactly.

    Each Beta posterior is discretised onto ``grid`` bins of [0, 1]. For bins
    with density p_i and field-average density q, the probability that i's draw
    exceeds a random opponent's is

        sum_k p_i[k] * (Q[k-1] + q[k]/2)

    where Q is the cumulative sum of q and the half-bin term handles a tie
    inside the same bin.
    """
    n = len(contenders)
    if n == 0:
        return []

    # Bin midpoints, avoiding the singularities at 0 and 1.
    xs = [(k + 0.5) / grid for k in range(grid)]
    log_x = [ln(x) for x in xs]
    log_1mx = [ln(1.0 - x) for x in xs]

    # Discretised, normalised posterior per contender.
    densities: list[list[float]] = []
    for c in contenders:
        a, b = c.alpha, c.beta
        norm = lgamma(a + b) - lgamma(a) - lgamma(b)
        row = [exp(norm + (a - 1.0) * log_x[k] + (b - 1.0) * log_1mx[k]) for k in range(grid)]
        total = sum(row)
        if total <= 0:  # degenerate posterior; fall back to uniform
            row = [1.0 / grid] * grid
        else:
            row = [v / total for v in row]
        densities.append(row)

    # Field-average density.
    q = [0.0] * grid
    for row in densities:
        for k, v in enumerate(row):
            q[k] += v
    q = [v / n for v in q]

    # Cumulative mass strictly below each bin.
    below = [0.0] * grid
    running = 0.0
    for k in range(grid):
        below[k] = running
        running += q[k]

    out = []
    for row in densities:
        out.append(sum(row[k] * (below[k] + 0.5 * q[k]) for k in range(grid)))
    return out


def compare_to_elo(contenders: Sequence[Contender], grid: int = GRID) -> dict:
    """Rank agreement between the Elo ordering and the direct computation."""
    if len(contenders) < 2:
        raise ValueError("need at least two contenders to compare")

    probs = win_probabilities(contenders, grid=grid)

    elo_order = sorted(range(len(contenders)), key=lambda i: -contenders[i].elo)
    direct_order = sorted(range(len(contenders)), key=lambda i: -probs[i])

    elo_rank = {contenders[i].id: r for r, i in enumerate(elo_order, start=1)}
    direct_rank = {contenders[i].id: r for r, i in enumerate(direct_order, start=1)}

    def overlap(k: int) -> int:
        a = {cid for cid, r in elo_rank.items() if r <= k}
        b = {cid for cid, r in direct_rank.items() if r <= k}
        return len(a & b)

    displacements = [abs(elo_rank[cid] - direct_rank[cid]) for cid in elo_rank]
    displacements.sort()
    mid = len(displacements) // 2

    return {
        "spearman": spearman(elo_rank, direct_rank),
        "top10_overlap": overlap(10),
        "top50_overlap": overlap(50),
        "top100_overlap": overlap(100),
        "median_rank_shift": displacements[mid],
        "max_rank_shift": displacements[-1],
        "grid": grid,
    }


def report(result: dict) -> None:
    print()
    print("=" * 78)
    print(" MODEL CHECK — simulated Elo vs. directly computed win probability")
    print("=" * 78)
    print(
        "\n  The tournament estimates each professor's chance of beating a random\n"
        f"  opponent. That value is computed here exactly on a {result['grid']}-bin grid,\n"
        "  with no simulation and no RNG, and the two orderings are compared.\n"
    )
    print(f"  Spearman rho ................ {result['spearman']:.5f}")
    print(f"  top-10 agreement ............ {result['top10_overlap']}/10")
    print(f"  top-50 agreement ............ {result['top50_overlap']}/50")
    print(f"  top-100 agreement ........... {result['top100_overlap']}/100")
    print(f"  median rank shift ........... {result['median_rank_shift']}")
    print(f"  largest rank shift .......... {result['max_rank_shift']}")
    print()
    if result["spearman"] >= 0.99:
        print("  The simulation reproduces the closed-form ordering.")
    else:
        print("  DISAGREEMENT — the tournament has not converged, or has a bug.")
    print("=" * 78)
    print()
