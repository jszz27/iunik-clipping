"""One ingestion run, shared by the CLI and the web UI so they cannot drift apart."""

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from clipping import normalize, store
from clipping.config import Settings
from clipping.matcher import match
from clipping.providers import BATCH_SIZE, ProviderSpec
from clipping.tiering import classify

QUOTA_HEADER = "X-RateLimit-Requests-Remaining"
LIMIT_HEADER = "X-RateLimit-Requests-Limit"


@dataclass
class RunResult:
    target: str = ""
    next_cursor: str | None = None
    rows: list[dict] = field(default_factory=list)
    discovered: int = 0
    tiered: int = 0
    skipped: int = 0
    stored: int = 0
    calls: int = 0
    quota_remaining: int | None = None
    quota_limit: int | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "next_cursor": self.next_cursor,
            "rows": self.rows,
            "discovered": self.discovered,
            "tiered": self.tiered,
            "skipped": self.skipped,
            "stored": self.stored,
            "calls": self.calls,
            "quota_remaining": self.quota_remaining,
            "quota_limit": self.quota_limit,
            "errors": self.errors,
        }


class Fetcher:
    """Minimal provider access. Key rotation and caching are still to be built."""

    def __init__(self, spec: ProviderSpec, key: str):
        self.spec = spec
        self.key = key
        self.calls = 0
        self.quota_remaining: int | None = None
        self.quota_limit: int | None = None

    def get(self, client: httpx.Client, capability: str, handle: str = "",
            shortcode: str = "", cursor: str | None = None) -> dict:
        endpoint = self.spec.endpoint(capability)
        params = endpoint.query(handle, shortcode)
        if cursor:
            params["pagination_token"] = cursor
        response = client.get(
            f"https://{self.spec.host}{endpoint.path}",
            params=params,
            headers={"x-rapidapi-key": self.key, "x-rapidapi-host": self.spec.host},
            timeout=30.0,
        )
        self.calls += 1
        # The gateway reports remaining quota on billable responses only, so this keeps
        # its last known value after a 404, which costs nothing and reports nothing.
        for header, attr in (
            (QUOTA_HEADER, "quota_remaining"), (LIMIT_HEADER, "quota_limit")
        ):
            if header in response.headers:
                try:
                    setattr(self, attr, int(response.headers[header]))
                except ValueError:
                    pass
        response.raise_for_status()
        return response.json()


def ingest(
    settings: Settings,
    campaigns: list,
    spec: ProviderSpec,
    *,
    target: str | None = None,
    fixture: str | None = None,
    max_profiles: int = 5,
    batch_size: int = BATCH_SIZE,
    cursor: str | None = None,
    dry_run: bool = False,
) -> RunResult:
    """Discover tagged posts for one target account, tier creators, attribute, store.

    target overrides the configured brand handle, so any account can be tracked at
    runtime without editing configuration.

    Exactly batch_size posts are processed per execution. The providers return a fixed
    page of 21 and ignore count and limit parameters, so the cap is applied here after
    parsing rather than asked of the API.

    Follower counts are absent from every feed response, so each creator costs one extra
    profile call. That is the dominant expense, hence max_profiles.
    """
    handle_target = (target or settings.brand_handle or "").lstrip("@").strip().casefold()
    if not handle_target:
        raise ValueError("No target account. Pass target, or set BRAND_HANDLE in .env.")

    result = RunResult(target=handle_target)
    fetcher = Fetcher(spec, settings.rapidapi_keys[0])

    with httpx.Client() as client:
        if fixture:
            payload = json.loads(Path(fixture).read_text(encoding="utf-8"))
        else:
            payload = fetcher.get(client, "tagged", handle=handle_target, cursor=cursor)

        parsed = normalize.parse_feed(payload, "tagged")[:batch_size]
        result.next_cursor = normalize.next_cursor(payload)
        handles = list(dict.fromkeys(p.creator_handle for p, _ in parsed))
        result.discovered = len(parsed)

        profiles = {}
        for handle in handles[:max_profiles]:
            try:
                profiles[handle] = normalize.parse_profile(
                    fetcher.get(client, "profile", handle=handle)
                )
            except (httpx.HTTPError, ValueError) as exc:
                result.errors.append(f"@{handle}: {type(exc).__name__}")

    result.calls = fetcher.calls
    result.quota_remaining = fetcher.quota_remaining
    result.quota_limit = fetcher.quota_limit
    result.tiered = len(profiles)
    result.skipped = len(handles) - len(profiles)

    conn = None if dry_run else store.connect(settings.db_path)
    run_id = None if conn is None else store.start_run(conn)

    for post, metrics in parsed:
        creator = profiles.get(post.creator_handle)
        if creator is None:
            continue  # no follower count, so the tier would be a guess
        campaign = match(post.hashtags, post.taken_at.date(), campaigns)
        result.rows.append({
            "creator": post.creator_handle,
            "tier": classify(creator.follower_count).value,
            "followers": creator.follower_count,
            "campaign": campaign,
            "likes": metrics.like_count,
            "comments": metrics.comment_count,
            "hashtags": " ".join(post.hashtags),
            "media_type": post.media_type or "",
            "posted_at": post.taken_at.strftime("%Y-%m-%d"),
            "url": post.url,
        })
        if conn is not None:
            store.upsert_creator(conn, creator)
            store.upsert_post(conn, post, follower_count=creator.follower_count,
                              campaign_id=campaign)
            store.record_metrics(conn, post.shortcode, metrics)
            result.stored += 1

    if conn is not None:
        store.finish_run(conn, run_id, posts_found=result.discovered,
                         posts_new=result.stored)
        conn.commit()
        conn.close()

    return result
