"""Turn one provider's JSON into the canonical models.

Written against recorded live responses in tests/fixtures/, not documentation. Where a
field is missing or null in real data, that is handled here rather than in the pipeline.
"""

from datetime import datetime, timezone

from clipping.matcher import extract_hashtags
from clipping.models import Creator, Metrics, Post

# media_type is numeric in the payload; product_type is more descriptive when present.
MEDIA_TYPES = {1: "photo", 2: "video", 8: "carousel"}


def _caption(item: dict) -> str:
    return ((item.get("caption") or {}).get("text")) or ""


def parse_profile(payload: dict) -> Creator:
    """Read a /user-info/ response."""
    d = payload.get("data") or payload
    username = d.get("username")
    if not username:
        raise ValueError("profile response has no username")

    return Creator(
        handle=username,
        follower_count=d.get("follower_count") or 0,
        ig_user_id=str(d["id"]) if d.get("id") is not None else None,
        full_name=d.get("full_name"),
        is_verified=bool(d.get("is_verified")),
        is_private=bool(d.get("is_private")),
        profile_pic_url=d.get("profile_pic_url"),
    )


def parse_item(item: dict, discovery_source: str) -> tuple[Post, Metrics] | None:
    """Read one media item. Returns None when it cannot be attributed to a creator."""
    code = item.get("code")
    username = (item.get("user") or {}).get("username")
    taken_at = item.get("taken_at")
    if not code or not username or taken_at is None:
        return None

    caption = _caption(item)
    post = Post(
        shortcode=code,
        creator_handle=username,
        taken_at=datetime.fromtimestamp(int(taken_at), tz=timezone.utc),
        ig_media_id=str(item["id"]) if item.get("id") is not None else None,
        media_type=item.get("product_type") or MEDIA_TYPES.get(item.get("media_type")),
        caption=caption,
        thumbnail_url=item.get("thumbnail_url"),
        hashtags=extract_hashtags(caption),
        discovery_source=discovery_source,
    )

    # Real responses return null for like_count on some posts, typically where the
    # creator has hidden likes. Null is not zero, but the column is not nullable, so
    # it is stored as zero and the distinction is deliberately dropped.
    metrics = Metrics(
        like_count=item.get("like_count") or 0,
        comment_count=item.get("comment_count") or 0,
        view_count=item.get("view_count"),
        play_count=item.get("play_count"),
    )
    return post, metrics


def parse_feed(payload: dict, discovery_source: str) -> list[tuple[Post, Metrics]]:
    """Read a /user-tag/ or /user-reels/ page. Unparseable items are skipped."""
    items = (payload.get("data") or {}).get("items") or []
    parsed = [parse_item(i, discovery_source) for i in items]
    return [p for p in parsed if p is not None]


def next_cursor(payload: dict) -> str | None:
    return payload.get("pagination_token") or None
