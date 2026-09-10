"""Classify a creator into an influencer tier by follower count."""

from enum import Enum


class Tier(str, Enum):
    NANO = "Nano"
    MICRO = "Micro"
    MID = "Mid"
    MACRO = "Macro"
    MEGA = "Mega"


# Inclusive upper bound for each tier, ascending. Mega is unbounded and so has no entry.
_UPPER_BOUNDS = (
    (999, Tier.NANO),
    (10_000, Tier.MICRO),
    (100_000, Tier.MID),
    (1_000_000, Tier.MACRO),
)


def classify(follower_count: int) -> Tier:
    """Map a follower count onto a Tier.

    Exactly 1,000,000 is Macro, since Mega is specified as strictly greater than 1M.
    """
    if follower_count < 0:
        raise ValueError(f"follower_count must be non-negative, got {follower_count}")
    for upper, tier in _UPPER_BOUNDS:
        if follower_count <= upper:
            return tier
    return Tier.MEGA
