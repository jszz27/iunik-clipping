import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from clipping import store
from clipping.matcher import Campaign
from clipping.models import Creator, Metrics, Post

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def creator():
    return Creator(handle="creator_one", follower_count=5_000, full_name="Creator One")


def a_post(shortcode="ABC123", taken_at=None, **kw) -> Post:
    return Post(
        shortcode=shortcode,
        creator_handle="creator_one",
        taken_at=taken_at or ago(days=1),
        caption=kw.pop("caption", "sponsored #iunikspringglow"),
        hashtags=kw.pop("hashtags", ["iunikspringglow"]),
        **kw,
    )


def count(conn, table) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- the verification named in the plan for this step ---

def test_same_post_twice_yields_one_post_row_and_two_metric_rows(conn, creator):
    store.upsert_creator(conn, creator, now=ago(hours=8))
    post = a_post()

    first = store.upsert_post(conn, post, follower_count=5_000, now=ago(hours=8))
    store.record_metrics(conn, post.shortcode, Metrics(like_count=10, comment_count=1),
                         now=ago(hours=8))

    second = store.upsert_post(conn, post, follower_count=5_200, now=NOW)
    store.record_metrics(conn, post.shortcode, Metrics(like_count=95, comment_count=7), now=NOW)

    assert (first, second) == (True, False)
    assert count(conn, "posts") == 1
    assert count(conn, "metrics") == 2


# --- creators and follower history ---

def test_upsert_creator_assigns_tier_and_appends_history(conn, creator):
    assert store.upsert_creator(conn, creator, now=ago(days=2)) == "Micro"

    grown = creator.model_copy(update={"follower_count": 250_000})
    assert store.upsert_creator(conn, grown, now=NOW) == "Macro"

    assert count(conn, "creators") == 1
    assert conn.execute("SELECT tier FROM creators").fetchone()["tier"] == "Macro"
    history = conn.execute(
        "SELECT follower_count FROM follower_history ORDER BY captured_at"
    ).fetchall()
    assert [r["follower_count"] for r in history] == [5_000, 250_000]


def test_repeat_check_in_same_second_does_not_duplicate_history(conn, creator):
    store.upsert_creator(conn, creator, now=NOW)
    store.upsert_creator(conn, creator, now=NOW)
    assert count(conn, "follower_history") == 1


def test_handle_is_normalised(conn):
    store.upsert_creator(conn, Creator(handle="@Creator_ONE", follower_count=10), now=NOW)
    assert conn.execute("SELECT handle FROM creators").fetchone()["handle"] == "creator_one"


# --- what a re-ingest may and may not overwrite ---

def test_tier_at_post_is_frozen_but_campaign_can_be_reattributed(conn, creator):
    store.upsert_creator(conn, creator, now=ago(days=1))
    post = a_post()
    store.upsert_post(conn, post, follower_count=5_000, campaign_id="unattributed",
                      now=ago(days=1))

    # The creator goes viral, and a new campaign later claims the post.
    store.upsert_post(conn, post, follower_count=2_000_000, campaign_id="spring_glow_2026",
                      now=NOW)

    row = conn.execute("SELECT * FROM posts").fetchone()
    assert row["tier_at_post"] == "Micro"                 # frozen at discovery
    assert row["creator_followers_at_post"] == 5_000      # frozen at discovery
    assert row["campaign_id"] == "spring_glow_2026"       # re-attribution allowed


def test_caption_and_thumbnail_are_refreshed(conn, creator):
    store.upsert_creator(conn, creator, now=NOW)
    store.upsert_post(conn, a_post(caption="original"), follower_count=5_000, now=NOW)
    store.upsert_post(conn, a_post(caption="edited by creator"), follower_count=5_000, now=NOW)
    assert conn.execute("SELECT caption FROM posts").fetchone()["caption"] == "edited by creator"


def test_hashtags_are_stored_once_per_post(conn, creator):
    store.upsert_creator(conn, creator, now=NOW)
    post = a_post(hashtags=["iunikspringglow", "이유닉스프링"])
    store.upsert_post(conn, post, follower_count=5_000, now=NOW)
    store.upsert_post(conn, post, follower_count=5_000, now=NOW)

    tags = [r["hashtag"] for r in conn.execute(
        "SELECT hashtag FROM post_hashtags ORDER BY hashtag")]
    assert tags == ["iunikspringglow", "이유닉스프링"]


def test_post_url_is_derived_when_absent(conn, creator):
    store.upsert_creator(conn, creator, now=NOW)
    store.upsert_post(conn, a_post(), follower_count=5_000, now=NOW)
    url = conn.execute("SELECT url FROM posts").fetchone()["url"]
    assert url == "https://www.instagram.com/p/ABC123/"


def test_post_for_unknown_creator_is_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_post(conn, a_post(), follower_count=5_000, now=NOW)


# --- refresh decay ---

def test_never_measured_post_is_due(conn, creator):
    store.upsert_creator(conn, creator, now=NOW)
    store.upsert_post(conn, a_post(), follower_count=5_000, now=NOW)
    assert store.posts_due_for_refresh(conn, now=NOW) == ["ABC123"]


@pytest.mark.parametrize("age_days,since_hours,expected_due", [
    (1, 2, False),     # under a week: 6-hourly
    (1, 7, True),
    (15, 10, False),   # one to four weeks: daily
    (15, 30, True),
    (60, 100, False),  # one to three months: weekly
    (60, 200, True),
])
def test_refresh_interval_widens_with_post_age(conn, creator, age_days, since_hours,
                                               expected_due):
    store.upsert_creator(conn, creator, now=NOW)
    post = a_post(taken_at=ago(days=age_days))
    store.upsert_post(conn, post, follower_count=5_000, now=ago(days=age_days))
    store.record_metrics(conn, post.shortcode, Metrics(like_count=1, comment_count=0),
                         now=ago(hours=since_hours))

    assert (store.posts_due_for_refresh(conn, now=NOW) == ["ABC123"]) is expected_due


def test_posts_past_the_freeze_horizon_are_dropped_and_frozen(conn, creator):
    store.upsert_creator(conn, creator, now=NOW)
    old = a_post(shortcode="OLD999", taken_at=ago(days=200))
    store.upsert_post(conn, old, follower_count=5_000, now=ago(days=200))

    assert store.posts_due_for_refresh(conn, now=NOW) == []
    assert store.freeze_stale_posts(conn, now=NOW) == 1
    assert conn.execute("SELECT metrics_frozen FROM posts").fetchone()["metrics_frozen"] == 1
    assert store.freeze_stale_posts(conn, now=NOW) == 0  # idempotent


# --- reattribution ---

def campaign(cid, tags, priority=0):
    return Campaign(id=cid, name=cid, hashtags=frozenset(tags),
                    start=date(2026, 1, 1), end=date(2026, 12, 31), priority=priority)


COLLAB = campaign("iunik_x_yesstyle", {"iunikxyesstyle"}, priority=20)
ALWAYS_ON = campaign("always_on", {"iunik"}, priority=1)


@pytest.fixture
def seeded(conn, creator):
    """Two posts stored before any campaign existed, so both start unattributed."""
    store.upsert_creator(conn, creator, now=NOW)
    for code, tags in (("ABC123", ["iunikxyesstyle", "iunik"]), ("OLD999", ["kbeauty"])):
        store.upsert_post(conn, a_post(shortcode=code, hashtags=tags),
                          follower_count=5_000, now=NOW)
    return conn


def test_a_new_campaign_claims_historical_posts(seeded):
    # The point of storing every hashtag: a campaign defined today can reach back over
    # posts collected before it existed, without re-fetching anything.
    changes = store.reattribute(seeded, [COLLAB])
    assert len(changes) == 1
    assert changes[0]["before"] == "unattributed"
    assert changes[0]["after"] == "iunik_x_yesstyle"
    assert seeded.execute(
        "SELECT campaign_id FROM posts WHERE shortcode='ABC123'"
    ).fetchone()["campaign_id"] == "iunik_x_yesstyle"


def test_priority_decides_when_a_post_matches_two_campaigns(seeded):
    store.reattribute(seeded, [COLLAB, ALWAYS_ON])
    assert seeded.execute(
        "SELECT campaign_id FROM posts WHERE shortcode='ABC123'"
    ).fetchone()["campaign_id"] == "iunik_x_yesstyle"


def test_posts_matching_nothing_stay_unattributed(seeded):
    store.reattribute(seeded, [COLLAB])
    assert seeded.execute(
        "SELECT campaign_id FROM posts WHERE shortcode='OLD999'"
    ).fetchone()["campaign_id"] == "unattributed"


def test_reattribute_is_idempotent(seeded):
    assert len(store.reattribute(seeded, [COLLAB])) == 1
    assert store.reattribute(seeded, [COLLAB]) == []


def test_removing_a_campaign_releases_its_posts(seeded):
    store.reattribute(seeded, [COLLAB])
    changes = store.reattribute(seeded, [])
    assert changes[0]["after"] == "unattributed"


def test_dry_run_reports_without_writing(seeded):
    changes = store.reattribute(seeded, [COLLAB], dry_run=True)
    assert len(changes) == 1
    assert seeded.execute(
        "SELECT campaign_id FROM posts WHERE shortcode='ABC123'"
    ).fetchone()["campaign_id"] == "unattributed"


# --- run bookkeeping ---

def test_run_lifecycle_is_recorded(conn):
    run_id = store.start_run(conn, now=ago(minutes=5))
    store.finish_run(conn, run_id, posts_found=12, posts_new=3, now=NOW)

    row = conn.execute("SELECT * FROM runs").fetchone()
    assert (row["status"], row["posts_found"], row["posts_new"]) == ("ok", 12, 3)
    assert row["finished_at"] == "2026-09-10T12:00:00Z"


def test_api_calls_are_logged(conn):
    store.log_api_call(conn, "social_api", "/v1/tagged", 200, key_index=0, now=NOW)
    store.log_api_call(conn, "social_api", "/v1/info", 429, key_index=1, now=NOW)
    assert count(conn, "api_calls") == 2


def test_naive_datetimes_are_refused(conn, creator):
    store.upsert_creator(conn, creator, now=NOW)
    with pytest.raises(ValueError, match="naive datetime"):
        store.upsert_post(conn, a_post(), follower_count=5_000, now=datetime(2026, 9, 10))
