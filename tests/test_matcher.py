from datetime import date

from clipping.matcher import UNATTRIBUTED, Campaign, extract_hashtags, match

SPRING = Campaign(
    id="spring_glow_2026",
    name="Spring Glow Launch",
    hashtags=frozenset({"iunikspringglow", "이유닉스프링"}),
    start=date(2026, 3, 1),
    end=date(2026, 4, 30),
    priority=10,
)
ALWAYS_ON = Campaign(
    id="always_on",
    name="Always On",
    hashtags=frozenset({"iunikpartner"}),
    start=date(2026, 1, 1),
    end=date(2026, 12, 31),
    priority=1,
)
CAMPAIGNS = [SPRING, ALWAYS_ON]


def test_extracts_korean_and_ascii_hashtags():
    caption = "새 제품 후기! #이유닉스프링 #IUnikSpringGlow #skincare"
    assert extract_hashtags(caption) == ["이유닉스프링", "iunikspringglow", "skincare"]


def test_extract_handles_empty_caption():
    assert extract_hashtags(None) == []
    assert extract_hashtags("no tags here") == []


def test_extract_deduplicates_case_insensitively():
    assert extract_hashtags("#IUnik #iunik #IUNIK") == ["iunik"]


def test_korean_hashtag_matches_campaign():
    tags = extract_hashtags("#이유닉스프링 협찬")
    assert match(tags, date(2026, 3, 15), CAMPAIGNS) == "spring_glow_2026"


def test_highest_priority_wins_when_both_match():
    tags = ["iunikspringglow", "iunikpartner"]
    assert match(tags, date(2026, 3, 15), CAMPAIGNS) == "spring_glow_2026"


def test_post_outside_campaign_window_falls_through():
    # Same tag, but posted after Spring ended, so only the always-on campaign applies.
    tags = ["iunikspringglow", "iunikpartner"]
    assert match(tags, date(2026, 6, 1), CAMPAIGNS) == "always_on"


def test_no_matching_hashtag_is_unattributed():
    assert match(["skincare"], date(2026, 3, 15), CAMPAIGNS) == UNATTRIBUTED


def test_no_campaigns_at_all_is_unattributed():
    assert match(["iunikpartner"], date(2026, 3, 15), []) == UNATTRIBUTED
