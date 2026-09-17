"""The simulated-Elo tournament.

Every round, the field is shuffled and paired off. A match is resolved by
drawing one sample from each professor's Beta posterior — the higher draw wins.
Ratings then update with the standard Elo formula.

Because the draw is random, a professor with a wide posterior beats a stronger
opponent sometimes and loses to a weaker one often; over hundreds of rounds
that averages out to a rating reflecting both how good they look *and* how sure
we are. K decays across rounds so early matches move ratings quickly and later
ones settle them.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Optional, Sequence

from . import config
from .model import Contender

log = logging.getLogger(__name__)


def simulate(
    contenders: Sequence[Contender],
    *,
    rounds: Optional[int] = None,
    k_start: Optional[float] = None,
    k_end: Optional[float] = None,
    seed: Optional[int] = None,
    start_elo: Optional[float] = None,
    assign: bool = True,
) -> list[float]:
    """Run the tournament and return final Elo ratings, ordered like ``contenders``.

    With ``assign`` the ratings are also written back onto each contender.
    """
    rounds = config.ELO_ROUNDS if rounds is None else rounds
    k_start = config.ELO_K_START if k_start is None else k_start
    k_end = config.ELO_K_END if k_end is None else k_end
    seed = config.RANDOM_SEED if seed is None else seed
    start_elo = config.ELO_START if start_elo is None else start_elo

    n = len(contenders)
    if n < 2:
        raise ValueError("need at least two contenders to run a tournament")

    rng = random.Random(seed)
    draw = rng.betavariate

    # Parallel lists: this loop runs ~600k times, so keep attribute lookups out.
    alphas = [c.alpha for c in contenders]
    betas = [c.beta for c in contenders]
    elos = [start_elo] * n
    order = list(range(n))

    matches = 0
    for round_no in range(rounds):
        k = k_start if rounds == 1 else k_start + (k_end - k_start) * (round_no / (rounds - 1))
        rng.shuffle(order)

        # An odd field leaves one professor with a bye; the reshuffle each round
        # keeps it from landing on the same person twice.
        for i in range(0, n - 1, 2):
            a = order[i]
            b = order[i + 1]

            draw_a = draw(alphas[a], betas[a])
            draw_b = draw(alphas[b], betas[b])

            elo_a = elos[a]
            elo_b = elos[b]
            expected_a = 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))

            if draw_a > draw_b:
                score_a = 1.0
            elif draw_a < draw_b:
                score_a = 0.0
            else:  # continuous draws, so effectively unreachable
                score_a = 0.5

            # Zero-sum: b's delta is exactly the negation of a's.
            delta = k * (score_a - expected_a)
            elos[a] = elo_a + delta
            elos[b] = elo_b - delta
            matches += 1

        if rounds >= 20 and (round_no + 1) % (rounds // 5) == 0:
            log.info(
                "  round %d/%d (K=%.1f, %d matches so far)",
                round_no + 1, rounds, k, matches,
            )

    log.info("simulated %d matches across %d rounds", matches, rounds)

    if assign:
        for contender, elo in zip(contenders, elos):
            contender.elo = elo
    return elos


def simulate_ensemble(
    contenders: Sequence[Contender],
    *,
    tournaments: Optional[int] = None,
    rounds: Optional[int] = None,
    k_start: Optional[float] = None,
    k_end: Optional[float] = None,
    seed: Optional[int] = None,
    start_elo: Optional[float] = None,
    assign: bool = True,
) -> tuple[list[float], list[float]]:
    """Average several independent tournaments; return ``(mean_elo, stddev_elo)``.

    A single tournament leaves real noise at the top of the board: professors
    there have near-identical posteriors, so which of them lands at #3 is partly
    the RNG. Averaging independent runs cuts that variance without needing a
    prohibitively long tournament, and beats one long run at equal compute
    (measured: 5x500 rounds is strictly better than 1x2500).

    The per-professor standard deviation across runs is returned as well — it is
    the honest width of a rank, and near-ties show up as a large value.
    """
    tournaments = config.ELO_TOURNAMENTS if tournaments is None else tournaments
    seed = config.RANDOM_SEED if seed is None else seed
    if tournaments < 1:
        raise ValueError("tournaments must be >= 1")

    n = len(contenders)
    sums = [0.0] * n
    sum_squares = [0.0] * n

    for index in range(tournaments):
        log.info("tournament %d/%d (seed %d)", index + 1, tournaments, seed + index)
        elos = simulate(
            contenders,
            rounds=rounds,
            k_start=k_start,
            k_end=k_end,
            seed=seed + index,
            start_elo=start_elo,
            assign=False,
        )
        for i, elo in enumerate(elos):
            sums[i] += elo
            sum_squares[i] += elo * elo

    means = [total / tournaments for total in sums]
    stddevs = [
        math.sqrt(max(0.0, sq / tournaments - mean * mean))
        for sq, mean in zip(sum_squares, means)
    ]

    if assign:
        for contender, mean, sd in zip(contenders, means, stddevs):
            contender.elo = mean
            contender.elo_stddev = sd
    return means, stddevs


def spearman(ranks_a: dict[str, int], ranks_b: dict[str, int]) -> float:
    """Spearman rank correlation between two rankings over the same ids.

    Ranks are distinct integers here, so the no-ties shortcut is exact.
    """
    shared = set(ranks_a) & set(ranks_b)
    n = len(shared)
    if n < 2:
        return float("nan")
    d2 = sum((ranks_a[i] - ranks_b[i]) ** 2 for i in shared)
    return 1.0 - (6.0 * d2) / (n * (n * n - 1))
