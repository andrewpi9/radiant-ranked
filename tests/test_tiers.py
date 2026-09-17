"""Tier assignment must partition the board exactly — no gaps, no overlaps."""

from __future__ import annotations

import pytest

from radiant_rank import tiers


def test_published_shares_total_the_documented_figure():
    # The published VALORANT figures round to 100.02, not 100. Assignment
    # normalises by this, so a change here silently reshapes every tier.
    assert tiers.TOTAL_SHARE == pytest.approx(100.02, abs=1e-9)


def test_twenty_five_divisions_across_nine_families():
    assert len(tiers.TIERS) == 25
    assert len(tiers.FAMILIES) == 9
    assert tiers.TIERS[0].name == "Radiant"
    assert tiers.TIERS[-1].name == "Iron 1"


def test_divisions_are_ordered_best_first():
    # Radiant has no division number; every other family runs 3, 2, 1.
    for family in tiers.FAMILIES[1:]:
        divisions = [t.division for t in tiers.TIERS if t.family == family]
        assert divisions == [3, 2, 1], family


@pytest.mark.parametrize("total", [1, 2, 5, 25, 100, 999, 4033, 10000])
def test_assign_covers_every_position_exactly_once(total):
    assigned = tiers.assign(total)
    assert len(assigned) == total
    # Ranks only ever move down the table, never back up.
    assert assigned == sorted(assigned)
    assert set(assigned) <= set(range(len(tiers.TIERS)))


@pytest.mark.parametrize("total", [1, 2, 5, 25, 100, 4033])
def test_summary_spans_are_contiguous_and_complete(total):
    rows = tiers.summarize(tiers.assign(total))
    assert sum(r["count"] for r in rows) == total

    spans = [(r["first_rank"], r["last_rank"]) for r in rows if r["count"]]
    assert spans[0][0] == 1
    assert spans[-1][1] == total
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert start == end + 1


def test_assign_handles_an_empty_board():
    assert tiers.assign(0) == []
    assert sum(r["count"] for r in tiers.summarize([])) == 0


def test_top_division_is_never_rounded_away():
    # Radiant is 0.05% — on any small board that rounds to zero, but the top
    # of a leaderboard called "Radiant Ranked" must not be empty.
    for total in range(1, 60):
        assert tiers.assign(total)[0] == 0, total


def test_realised_shares_track_the_published_targets():
    rows = tiers.summarize(tiers.assign(200_000))
    for row in rows:
        expected = 100.0 * row["target_share"] / tiers.TOTAL_SHARE
        assert row["actual_share"] == pytest.approx(expected, abs=0.02), row["name"]


def test_gold_straddles_the_median():
    # Gold containing the median professor is the defining property of the
    # distribution; it is what fixes the cumulative share at ~50%.
    total = 4033
    rows = tiers.summarize(tiers.assign(total))
    median_rank = (total + 1) // 2
    holder = next(
        r for r in rows if r["count"] and r["first_rank"] <= median_rank <= r["last_rank"]
    )
    assert holder["family"] == "Gold"


def test_roll_up_preserves_totals():
    divisions = tiers.summarize(tiers.assign(4033))
    families = tiers.roll_up(divisions)

    assert len(families) == 9
    assert sum(f["divisions"] for f in families) == 25
    assert sum(f["count"] for f in families) == sum(d["count"] for d in divisions)

    gold = next(f for f in families if f["name"] == "Gold")
    assert gold["first_rank"] < gold["last_rank"]
