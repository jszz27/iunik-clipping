"""Adapter tests.

Payloads here are synthetic but mirror the shape of real recorded responses, including
the awkward cases that actually occurred: a null like_count, an absent follower_count on
tagged items, and a numeric media_type.
"""

from datetime import timezone

import pytest

from clipping.normalize import next_cursor, parse_feed, parse_item, parse_profile

PROFILE = {
    "data": {
        "username": "some_creator",
        "full_name": "Some Creator",
        "follower_count": 159117,
        "id": 4745619514,
        "is_verified": False,
        "is_private": False,
        "profile_pic_url": "https://example.invalid/pic.jpg",
    }
}


def item(**overrides):
    base = {
        "code": "ABC123",
        "id": 3983440179543328424,
        "taken_at": 1789083192,
        "media_type": 1,
        "product_type": "feed",
        "like_count": 42,
        "comment_count": 7,
        "thumbnail_url": "https://example.invalid/thumb.jpg",
        "caption": {"text": "a little iUNIK moment #iunik #이유닉스프링"},
        "user": {"username": "some_creator", "full_name": "Some Creator"},
    }
    base.update(overrides)
    return base


# --- profiles ---

def test_parse_profile_reads_the_nested_data_object():
    c = parse_profile(PROFILE)
    assert c.handle == "some_creator"
    assert c.follower_count == 159117
    assert c.ig_user_id == "4745619514"      # coerced to string
    assert c.is_verified is False


def test_parse_profile_tolerates_an_unwrapped_payload():
    assert parse_profile(PROFILE["data"]).follower_count == 159117


def test_parse_profile_without_a_username_is_rejected():
    with pytest.raises(ValueError, match="no username"):
        parse_profile({"data": {"follower_count": 10}})


def test_missing_follower_count_becomes_zero_not_a_crash():
    assert parse_profile({"data": {"username": "x"}}).follower_count == 0


# --- media items ---

def test_parse_item_maps_the_fields_the_pipeline_needs():
    post, metrics = parse_item(item(), "tagged")
    assert post.shortcode == "ABC123"
    assert post.creator_handle == "some_creator"
    assert post.url == "https://www.instagram.com/p/ABC123/"
    assert post.discovery_source == "tagged"
    assert metrics.like_count == 42
    assert metrics.comment_count == 7


def test_taken_at_becomes_an_aware_utc_datetime():
    post, _ = parse_item(item(), "tagged")
    assert post.taken_at.tzinfo is not None
    assert post.taken_at.astimezone(timezone.utc).year == 2026


def test_hashtags_are_extracted_from_the_caption():
    post, _ = parse_item(item(), "tagged")
    assert post.hashtags == ["iunik", "이유닉스프링"]


def test_null_like_count_is_stored_as_zero():
    # Real responses return null here when a creator hides likes. Null is not zero, but
    # the column is not nullable, so the distinction is deliberately dropped.
    _, metrics = parse_item(item(like_count=None, comment_count=None), "tagged")
    assert (metrics.like_count, metrics.comment_count) == (0, 0)


def test_media_type_falls_back_to_the_numeric_code():
    post, _ = parse_item(item(product_type=None, media_type=8), "tagged")
    assert post.media_type == "carousel"


def test_empty_caption_yields_no_hashtags():
    post, _ = parse_item(item(caption=None), "tagged")
    assert post.caption == ""
    assert post.hashtags == []


@pytest.mark.parametrize("broken", [
    {"code": None},
    {"user": {}},
    {"taken_at": None},
])
def test_unusable_items_are_skipped_rather_than_raising(broken):
    assert parse_item(item(**broken), "tagged") is None


# --- feeds ---

def test_parse_feed_skips_unusable_items_and_keeps_the_rest():
    payload = {"data": {"items": [item(), item(code=None), item(code="XYZ789")]}}
    parsed = parse_feed(payload, "tagged")
    assert [p.shortcode for p, _ in parsed] == ["ABC123", "XYZ789"]


def test_empty_feed_is_not_an_error():
    assert parse_feed({"data": {"items": []}}, "tagged") == []
    assert parse_feed({}, "tagged") == []


def test_pagination_token_is_surfaced():
    assert next_cursor({"pagination_token": "abc"}) == "abc"
    assert next_cursor({"pagination_token": ""}) is None
    assert next_cursor({}) is None
