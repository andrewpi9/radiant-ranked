"""The tournament has to be reproducible, and has to agree with the maths."""

from __future__ import annotations

import pytest

from radiant_rank.elo import simulate, simulate_ensemble, spearman
from radiant_rank.model import build_contenders
from radiant_rank.verify import compare_to_elo, win_probabilities


def field(n=60):
    """A spread of averages and rating volumes, deterministic."""
    records = []
    for i in range(n):
        records.append({
            "id": f"T{i}",
            "first_name": "P", "last_name": str(i),
            "department": "Dept", "school_name": "Test U", "school_id": "S1",
            "avg_rating": round(2.0 + 3.0 * (i / max(n - 1, 1)), 2),
            "avg_difficulty": 3.0,
            "num_ratings": 5 + (i * 7) % 180,
            "would_take_again_percent": 50.0,
        })
    contenders, _ = build_contenders(records, min_ratings=5)
    return contenders


def test_spearman_of_a_ranking_against_itself_is_one():
    ranks = {"a": 1, "b": 2, "c": 3}
    assert spearman(ranks, ranks) == pytest.approx(1.0)
    assert spearman(ranks, {"a": 3, "b": 2, "c": 1}) == pytest.approx(-1.0)


def test_same_seed_reproduces_the_board_exactly():
    a = simulate(field(), rounds=40, seed=7, assign=False)
    b = simulate(field(), rounds=40, seed=7, assign=False)
    assert a == b


def test_a_different_seed_gives_a_different_board():
    a = simulate(field(), rounds=40, seed=7, assign=False)
    b = simulate(field(), rounds=40, seed=8, assign=False)
    assert a != b


def test_elo_is_zero_sum():
    contenders = field(40)  # even, so nobody takes a bye
    elos = simulate(contenders, rounds=30, seed=3, assign=False)
    assert sum(elos) == pytest.approx(1500.0 * len(elos), abs=1e-6)


def test_a_tournament_needs_two_players():
    with pytest.raises(ValueError):
        simulate(field(1), rounds=5, seed=1)


def test_ensemble_reports_a_spread_and_is_reproducible():
    contenders = field()
    means, sds = simulate_ensemble(contenders, tournaments=4, rounds=40, seed=11, assign=False)
    again, _ = simulate_ensemble(contenders, tournaments=4, rounds=40, seed=11, assign=False)

    assert means == again
    assert len(sds) == len(contenders)
    assert all(sd >= 0 for sd in sds)
    assert any(sd > 0 for sd in sds)


def test_ensemble_is_steadier_than_a_single_tournament():
    """Averaging independent runs is the fix for the seed sensitivity that a
    single tournament has; if it ever stops helping, the board is not publishable."""
    contenders = field(120)

    def ranks_from(elos):
        order = sorted(range(len(contenders)), key=lambda i: -elos[i])
        return {contenders[i].id: r for r, i in enumerate(order, start=1)}

    single_a = ranks_from(simulate(contenders, rounds=60, seed=100, assign=False))
    single_b = ranks_from(simulate(contenders, rounds=60, seed=200, assign=False))

    ens_a = ranks_from(simulate_ensemble(contenders, tournaments=6, rounds=60, seed=100, assign=False)[0])
    ens_b = ranks_from(simulate_ensemble(contenders, tournaments=6, rounds=60, seed=200, assign=False)[0])

    assert spearman(ens_a, ens_b) > spearman(single_a, single_b)


def test_win_probabilities_are_ordered_and_bounded():
    contenders = field()
    probs = win_probabilities(contenders, grid=400)

    assert len(probs) == len(contenders)
    assert all(0.0 <= p <= 1.0 for p in probs)
    # A random opponent beats a random professor half the time, on average.
    assert sum(probs) / len(probs) == pytest.approx(0.5, abs=0.02)


def test_the_simulation_agrees_with_the_closed_form():
    """The tournament is a Monte Carlo estimate of a computable quantity. If the
    two orderings diverge, the simulation is wrong — not merely imprecise."""
    contenders = field(120)
    simulate_ensemble(contenders, tournaments=6, rounds=150, seed=5)
    result = compare_to_elo(contenders, grid=600)
    assert result["spearman"] > 0.95
