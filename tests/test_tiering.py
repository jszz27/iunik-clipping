import pytest

from clipping.tiering import Tier, classify


@pytest.mark.parametrize("followers,expected", [
    (0, Tier.NANO),
    (999, Tier.NANO),
    (1_000, Tier.MICRO),
    (10_000, Tier.MICRO),
    (10_001, Tier.MID),
    (100_000, Tier.MID),
    (100_001, Tier.MACRO),
    (1_000_000, Tier.MACRO),      # Mega is strictly greater than 1M
    (1_000_001, Tier.MEGA),
    (50_000_000, Tier.MEGA),
])
def test_boundaries(followers, expected):
    assert classify(followers) is expected


def test_every_tier_is_reachable():
    reached = {classify(n) for n in (500, 5_000, 50_000, 500_000, 5_000_000)}
    assert reached == set(Tier)


def test_negative_is_rejected():
    with pytest.raises(ValueError):
        classify(-1)
