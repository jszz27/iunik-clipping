"""SQLite persistence. Posts are deduplicated; metrics and follower counts accumulate."""

import sqlite3
from datetime import datetime

from clipping.matcher import UNATTRIBUTED
from clipping.models import Creator, Metrics, Post, to_iso, utcnow
from clipping.tiering import classify

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    handle                TEXT PRIMARY KEY,
    ig_user_id            TEXT,
    full_name             TEXT,
    is_verified           INTEGER NOT NULL DEFAULT 0,
    is_private            INTEGER NOT NULL DEFAULT 0,
    follower_count        INTEGER NOT NULL,
    tier                  TEXT    NOT NULL,
    profile_pic_url       TEXT,
    last_profile_check_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    shortcode                 TEXT PRIMARY KEY,
    ig_media_id               TEXT,
    creator_handle            TEXT NOT NULL REFERENCES creators(handle),
    url                       TEXT NOT NULL,
    media_type                TEXT,
    caption                   TEXT,
    taken_at                  TEXT NOT NULL,
    thumbnail_url             TEXT,
    campaign_id               TEXT NOT NULL DEFAULT 'unattributed',
    discovery_source          TEXT NOT NULL,
    discovered_at             TEXT NOT NULL,
    creator_followers_at_post INTEGER NOT NULL,
    tier_at_post              TEXT    NOT NULL,
    metrics_frozen            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_posts_campaign ON posts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_posts_creator  ON posts(creator_handle);

CREATE TABLE IF NOT EXISTS post_hashtags (
    shortcode TEXT NOT NULL REFERENCES posts(shortcode) ON DELETE CASCADE,
    hashtag   TEXT NOT NULL,
    PRIMARY KEY (shortcode, hashtag)
);
CREATE INDEX IF NOT EXISTS idx_hashtag ON post_hashtags(hashtag);

CREATE TABLE IF NOT EXISTS metrics (
    id            INTEGER PRIMARY KEY,
    shortcode     TEXT NOT NULL REFERENCES posts(shortcode) ON DELETE CASCADE,
    captured_at   TEXT NOT NULL,
    like_count    INTEGER NOT NULL,
    comment_count INTEGER NOT NULL,
    view_count    INTEGER,
    play_count    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_metrics_post ON metrics(shortcode, captured_at);

CREATE TABLE IF NOT EXISTS follower_history (
    handle         TEXT NOT NULL REFERENCES creators(handle),
    captured_at    TEXT NOT NULL,
    follower_count INTEGER NOT NULL,
    PRIMARY KEY (handle, captured_at)
);

CREATE TABLE IF NOT EXISTS api_calls (
    id          INTEGER PRIMARY KEY,
    provider    TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    status_code INTEGER,
    key_index   INTEGER,
    called_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    posts_found INTEGER NOT NULL DEFAULT 0,
    posts_new   INTEGER NOT NULL DEFAULT 0,
    error_text  TEXT
);
"""


def connect(path: str = "clipping.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_creator(conn: sqlite3.Connection, creator: Creator, now: datetime | None = None) -> str:
    """Store the creator's current profile and append a follower-count observation.

    Returns the creator's current tier.
    """
    now = now or utcnow()
    tier = classify(creator.follower_count).value
    conn.execute(
        """
        INSERT INTO creators (handle, ig_user_id, full_name, is_verified, is_private,
                              follower_count, tier, profile_pic_url, last_profile_check_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(handle) DO UPDATE SET
            ig_user_id            = excluded.ig_user_id,
            full_name             = excluded.full_name,
            is_verified           = excluded.is_verified,
            is_private            = excluded.is_private,
            follower_count        = excluded.follower_count,
            tier                  = excluded.tier,
            profile_pic_url       = excluded.profile_pic_url,
            last_profile_check_at = excluded.last_profile_check_at
        """,
        (creator.handle, creator.ig_user_id, creator.full_name, int(creator.is_verified),
         int(creator.is_private), creator.follower_count, tier, creator.profile_pic_url,
         to_iso(now)),
    )
    # History, not a snapshot: a second check in the same second is a no-op, not a duplicate.
    conn.execute(
        "INSERT OR IGNORE INTO follower_history (handle, captured_at, follower_count) "
        "VALUES (?,?,?)",
        (creator.handle, to_iso(now), creator.follower_count),
    )
    return tier


def upsert_post(
    conn: sqlite3.Connection,
    post: Post,
    *,
    follower_count: int,
    campaign_id: str = UNATTRIBUTED,
    now: datetime | None = None,
) -> bool:
    """Insert or update a post. Returns True the first time a post is seen.

    discovered_at, creator_followers_at_post and tier_at_post are written once and never
    updated, so a creator who grows later cannot rewrite an earlier campaign's report.
    campaign_id stays updatable so a new campaign can claim historical posts.
    """
    now = now or utcnow()
    is_new = conn.execute(
        "SELECT 1 FROM posts WHERE shortcode = ?", (post.shortcode,)
    ).fetchone() is None

    conn.execute(
        """
        INSERT INTO posts (shortcode, ig_media_id, creator_handle, url, media_type, caption,
                           taken_at, thumbnail_url, campaign_id, discovery_source,
                           discovered_at, creator_followers_at_post, tier_at_post)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(shortcode) DO UPDATE SET
            ig_media_id   = excluded.ig_media_id,
            url           = excluded.url,
            media_type    = excluded.media_type,
            caption       = excluded.caption,
            thumbnail_url = excluded.thumbnail_url,
            campaign_id   = excluded.campaign_id
        """,
        (post.shortcode, post.ig_media_id, post.creator_handle, post.url, post.media_type,
         post.caption, to_iso(post.taken_at), post.thumbnail_url, campaign_id,
         post.discovery_source, to_iso(now), follower_count, classify(follower_count).value),
    )

    conn.executemany(
        "INSERT OR IGNORE INTO post_hashtags (shortcode, hashtag) VALUES (?,?)",
        [(post.shortcode, tag) for tag in post.hashtags],
    )
    return is_new


def record_metrics(
    conn: sqlite3.Connection, shortcode: str, metrics: Metrics, now: datetime | None = None
) -> None:
    """Append an engagement observation. Always an insert, never an update."""
    now = now or utcnow()
    conn.execute(
        """INSERT INTO metrics (shortcode, captured_at, like_count, comment_count,
                                view_count, play_count)
           VALUES (?,?,?,?,?,?)""",
        (shortcode, to_iso(now), metrics.like_count, metrics.comment_count,
         metrics.view_count, metrics.play_count),
    )


# Post age in days -> minimum hours between captures. Engagement is volatile in the first
# week and effectively static later, so polling decays with age instead of running hourly.
_REFRESH_RULES = ((7, 6), (30, 24), (90, 168))
_FREEZE_AFTER_DAYS = 90


def posts_due_for_refresh(conn: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    """Shortcodes whose metrics are stale enough to be worth spending an API call on."""
    now = now or utcnow()
    rows = conn.execute(
        """
        SELECT p.shortcode,
               (julianday(?) - julianday(p.taken_at))               AS age_days,
               (julianday(?) - julianday(MAX(m.captured_at))) * 24  AS hours_since
        FROM posts p
        LEFT JOIN metrics m ON m.shortcode = p.shortcode
        WHERE p.metrics_frozen = 0
        GROUP BY p.shortcode
        """,
        (to_iso(now), to_iso(now)),
    ).fetchall()

    due = []
    for row in rows:
        if row["age_days"] > _FREEZE_AFTER_DAYS:
            continue
        if row["hours_since"] is None:  # never measured
            due.append(row["shortcode"])
            continue
        interval = next(
            (hours for max_age, hours in _REFRESH_RULES if row["age_days"] <= max_age), None
        )
        if interval is not None and row["hours_since"] >= interval:
            due.append(row["shortcode"])
    return due


def freeze_stale_posts(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Stop tracking posts past the freeze horizon. Returns how many were frozen."""
    now = now or utcnow()
    cur = conn.execute(
        """UPDATE posts SET metrics_frozen = 1
           WHERE metrics_frozen = 0 AND julianday(?) - julianday(taken_at) > ?""",
        (to_iso(now), _FREEZE_AFTER_DAYS),
    )
    return cur.rowcount


def start_run(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, status) VALUES (?, 'running')",
        (to_iso(now or utcnow()),),
    )
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str = "ok",
    posts_found: int = 0,
    posts_new: int = 0,
    error_text: str | None = None,
    now: datetime | None = None,
) -> None:
    conn.execute(
        """UPDATE runs SET finished_at = ?, status = ?, posts_found = ?, posts_new = ?,
                           error_text = ? WHERE id = ?""",
        (to_iso(now or utcnow()), status, posts_found, posts_new, error_text, run_id),
    )


REPORT_COLUMNS = (
    "creator", "tier", "followers", "campaign", "likes", "comments",
    "hashtags", "media_type", "posted_at", "url",
)


def report_rows(conn: sqlite3.Connection) -> list[dict]:
    """One row per post, carrying its most recent metrics. The shape the report uses."""
    rows = conn.execute(
        """
        SELECT p.creator_handle, p.tier_at_post, p.creator_followers_at_post,
               p.campaign_id, p.media_type, p.taken_at, p.url, p.shortcode,
               m.like_count, m.comment_count,
               (SELECT GROUP_CONCAT(hashtag, ' ') FROM post_hashtags
                 WHERE shortcode = p.shortcode) AS hashtags
        FROM posts p
        LEFT JOIN metrics m ON m.shortcode = p.shortcode
             AND m.captured_at = (SELECT MAX(captured_at) FROM metrics
                                  WHERE shortcode = p.shortcode)
        ORDER BY m.like_count DESC, p.taken_at DESC
        """
    ).fetchall()

    return [
        {
            "creator": r["creator_handle"],
            "tier": r["tier_at_post"],
            "followers": r["creator_followers_at_post"],
            "campaign": r["campaign_id"],
            "likes": r["like_count"] or 0,
            "comments": r["comment_count"] or 0,
            "hashtags": r["hashtags"] or "",
            "media_type": r["media_type"] or "",
            # Date only, matching what a live run reports, so the two cannot disagree.
            "posted_at": (r["taken_at"] or "")[:10],
            "url": r["url"],
        }
        for r in rows
    ]


def campaign_totals(conn: sqlite3.Connection) -> list[dict]:
    """Per-campaign rollup, for the summary tab and the dashboard."""
    rows = conn.execute(
        """
        SELECT p.campaign_id AS campaign, COUNT(*) AS posts,
               COUNT(DISTINCT p.creator_handle) AS creators,
               SUM(p.creator_followers_at_post)  AS reach,
               SUM(COALESCE(m.like_count, 0))    AS likes,
               SUM(COALESCE(m.comment_count, 0)) AS comments
        FROM posts p
        LEFT JOIN metrics m ON m.shortcode = p.shortcode
             AND m.captured_at = (SELECT MAX(captured_at) FROM metrics
                                  WHERE shortcode = p.shortcode)
        GROUP BY p.campaign_id
        ORDER BY posts DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def log_api_call(
    conn: sqlite3.Connection,
    provider: str,
    endpoint: str,
    status_code: int | None,
    key_index: int | None = None,
    now: datetime | None = None,
) -> None:
    conn.execute(
        """INSERT INTO api_calls (provider, endpoint, status_code, key_index, called_at)
           VALUES (?,?,?,?,?)""",
        (provider, endpoint, status_code, key_index, to_iso(now or utcnow())),
    )
