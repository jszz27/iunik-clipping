"""Pipeline tests: dynamic target and the fixed batch size.

Everything here runs with max_profiles=0 and dry_run=True, so no network call and no
database write occurs.
"""

import json

import pytest

from clipping.config import load_settings
from clipping.pipeline import ingest
from clipping.providers import BATCH_SIZE, SCRAPER_20251


def settings(tmp_path, **env):
    base = {"RAPIDAPI_KEYS": "fake_key", "BRAND_HANDLE": "iunik_official"}
    base.update(env)
    return load_settings(tmp_path / "absent.env", environ=base)


def tagged_page(tmp_path, count=21, cursor="NEXT_PAGE_TOKEN"):
    """A page shaped like the real one. Providers return 21 and ignore count/limit."""
    items = [
        {
            "code": f"POST{i:03d}",
            "id": 1000 + i,
            "taken_at": 1789083192,
            "media_type": 1,
            "like_count": i,
            "comment_count": 1,
            "caption": {"text": f"post {i} #iunikxyesstyle"},
            "user": {"username": f"creator_{i:03d}"},
        }
        for i in range(count)
    ]
    path = tmp_path / "tagged.json"
    path.write_text(
        json.dumps({"data": {"items": items}, "pagination_token": cursor}),
        encoding="utf-8",
    )
    return str(path)


def run(tmp_path, **kw):
    fixture = kw.pop("fixture", None) or tagged_page(tmp_path)
    cfg = kw.pop("settings", None) or settings(tmp_path)
    return ingest(cfg, [], SCRAPER_20251, fixture=fixture, max_profiles=0,
                  dry_run=True, **kw)


# --- batch size ---

def test_a_21_post_page_is_capped_to_the_20_post_batch(tmp_path):
    # The providers ignore count and limit parameters and always return 21, so the cap
    # has to be applied after parsing rather than asked of the API.
    assert run(tmp_path).discovered == BATCH_SIZE == 20


def test_batch_size_is_overridable(tmp_path):
    assert run(tmp_path, batch_size=5).discovered == 5


def test_a_short_page_is_not_padded(tmp_path):
    assert run(tmp_path, fixture=tagged_page(tmp_path, count=3)).discovered == 3


def test_pagination_token_is_returned_for_the_next_batch(tmp_path):
    assert run(tmp_path).next_cursor == "NEXT_PAGE_TOKEN"


def test_absent_pagination_token_becomes_none(tmp_path):
    assert run(tmp_path, fixture=tagged_page(tmp_path, cursor="")).next_cursor is None


# --- dynamic target ---

def test_target_argument_overrides_the_configured_handle(tmp_path):
    assert run(tmp_path, target="someone_else").target == "someone_else"


def test_target_falls_back_to_brand_handle(tmp_path):
    assert run(tmp_path).target == "iunik_official"


@pytest.mark.parametrize("given,expected", [
    ("@SomeBrand", "somebrand"),
    ("  spaced  ", "spaced"),
    ("MiXeD_Case", "mixed_case"),
])
def test_target_is_normalised(tmp_path, given, expected):
    assert run(tmp_path, target=given).target == expected


def test_no_target_anywhere_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="No target account"):
        run(tmp_path, settings=settings(tmp_path, BRAND_HANDLE=""))


def test_rows_are_empty_without_profile_lookups(tmp_path):
    # Follower counts come only from the profile endpoint, so with max_profiles=0 no post
    # can be tiered. Reporting them untiered would mean inventing a tier.
    result = run(tmp_path)
    assert result.rows == []
    assert result.tiered == 0
    assert result.skipped == 20
