"""The posterior has to punish thin evidence — that is the whole point of it."""

from __future__ import annotations

import pytest

from radiant_rank.model import build_contenders, posterior_mean, to_rating, to_unit


def record(**kw):
    base = {
        "id": "T1", "first_name": "A", "last_name": "B",
        "department": "Physics", "school_name": "Test U", "school_id": "S1",
        "avg_rating": 4.0, "avg_difficulty": 3.0,
        "num_ratings": 50, "would_take_again_percent": 80.0,
    }
    base.update(kw)
    return base


def test_rating_scale_maps_and_clamps():
    assert to_unit(1.0) == 0.0
    assert to_unit(5.0) == 1.0
    assert to_unit(3.0) == pytest.approx(0.5)
    # RMP has been known to emit out-of-range values; they must not escape [0,1].
    assert to_unit(7.0) == 1.0
    assert to_unit(-2.0) == 0.0
    assert to_rating(to_unit(4.3)) == pytest.approx(4.3)


def test_min_ratings_threshold_excludes_thin_and_unrated():
    records = [
        record(id="keep", num_ratings=5),
        record(id="thin", num_ratings=4),
        record(id="unrated", num_ratings=0, avg_rating=0),
    ]
    contenders, _ = build_contenders(records, min_ratings=5)
    assert [c.id for c in contenders] == ["keep"]


def test_a_thin_five_loses_to_a_well_backed_four_point_seven():
    # The headline behaviour of the whole model. The prior is pinned to the real
    # field mean (3.79/5); inferring it from a two-professor field would put the
    # anchor up at 4.6 and there would be nothing left to shrink toward.
    records = [
        record(id="thin", avg_rating=5.0, num_ratings=5),
        record(id="backed", avg_rating=4.7, num_ratings=200),
    ]
    contenders, _ = build_contenders(
        records, min_ratings=5, prior_mean=to_unit(3.79)
    )
    by_id = {c.id: c for c in contenders}
    assert posterior_mean(by_id["thin"]) < posterior_mean(by_id["backed"])


def test_more_evidence_narrows_the_posterior():
    records = [
        record(id="few", avg_rating=4.5, num_ratings=5),
        record(id="many", avg_rating=4.5, num_ratings=500),
    ]
    contenders, _ = build_contenders(records, min_ratings=5)
    by_id = {c.id: c for c in contenders}

    def spread(c):  # Beta variance
        a, b, n = c.alpha, c.beta, c.alpha + c.beta
        return a * b / (n * n * (n + 1))

    assert spread(by_id["many"]) < spread(by_id["few"])


def test_shrinkage_pulls_toward_the_field_and_never_past_it():
    records = [
        record(id="high", avg_rating=5.0, num_ratings=6),
        record(id="low", avg_rating=1.0, num_ratings=6),
        record(id="anchor", avg_rating=3.8, num_ratings=400),
    ]
    contenders, prior_mean = build_contenders(records, min_ratings=5)
    by_id = {c.id: c for c in contenders}

    assert posterior_mean(by_id["high"]) < to_unit(5.0)
    assert posterior_mean(by_id["low"]) > to_unit(1.0)
    # Shrinkage moves toward the prior; it must not overshoot past it.
    assert posterior_mean(by_id["high"]) > prior_mean
    assert posterior_mean(by_id["low"]) < prior_mean


@pytest.mark.parametrize("avg", [1.0, 5.0])
def test_extreme_averages_stay_samplable(avg):
    # A perfect 5.0 gives beta = 0 without a prior, which is not a valid Beta
    # and would crash the sampler partway through a tournament.
    contenders, _ = build_contenders([record(avg_rating=avg, num_ratings=9)], min_ratings=5)
    c = contenders[0]
    assert c.alpha > 0 and c.beta > 0


def test_zero_prior_strength_is_rejected_up_front():
    with pytest.raises(ValueError):
        build_contenders([record()], min_ratings=5, prior_strength=0.0)


def test_prior_mean_is_weighted_by_rating_volume():
    # Weighting by professor instead of by rating would let a crowd of
    # thinly-rated outliers drag the anchor meant to discipline them.
    records = [record(id=f"thin{i}", avg_rating=5.0, num_ratings=5) for i in range(20)]
    records.append(record(id="anchor", avg_rating=2.0, num_ratings=5000))
    _, prior_mean = build_contenders(records, min_ratings=5)
    assert prior_mean < to_unit(3.0)
