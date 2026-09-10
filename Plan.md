# Instagram Clipping & Influencer Tracking System — Architecture Plan

## Context

IUNIK (`@iunik_official`) runs paid influencer campaigns on Instagram. Today there is no
automated way to know which creators actually posted, how those posts performed, or which
campaign each post belongs to. Proving campaign ROI means manually opening the tagged feed
and copying numbers into a spreadsheet.

This system automates that loop. On a schedule it discovers posts that tag or mention the
brand, extracts the creator, the engagement, and the hashtags, attributes each post to a
campaign, classifies the creator by follower tier, and publishes the result to a Google
Sheet the marketing team already knows how to read.

The working directory `C:\Hyeri\Clipping` is empty apart from `CLAUDE.md`, so this is a
greenfield build with no existing code to reuse.

---

## Decisions already made

| Question | Decision |
|---|---|
| Tier gap 100K–1M | Add a **Macro** tier (5 tiers total) |
| Storage | **SQLite** as source of truth, **Google Sheets** as the export surface |
| Discovery | **Tagged-posts feed** + **influencer roster crawl** (no hashtag search, no stories) |
| Metrics | **Full history** — one row per post per check |

---

## Two concerns to state up front

**1. The two API keys supplied are the same string, and they were pasted into a chat.**
The key ending `…3912` was given twice. That means there is no failover capacity and no quota
doubling. Treat this key as compromised, rotate it in the RapidAPI dashboard, and subscribe
a second RapidAPI account for a genuinely distinct second key. The code will read an
arbitrary number of keys from `.env`, so adding the second one later is a one-line config
change. `.env` will be gitignored from the first commit.

**2. Caption-only @mentions do not appear in the tagged-posts feed.**
Instagram's tagged feed contains posts where the brand was tagged in the media or added as
a collaborator. A creator who writes "thanks @iunik_official" in the caption without tagging
the photo will never show up there. This is exactly why the roster crawl is in scope: you
already know who you paid, so polling their recent posts and filtering for your handle or
campaign hashtag closes the gap. Expect the roster to catch posts the tagged feed misses,
and vice versa. Both feed the same deduplicating pipeline.

Worth knowing for later: Meta's official Graph API exposes a `tags` edge and a
`mentioned_media` edge for Business accounts, plus `business_discovery` for follower counts
of other business/creator accounts. That path is more stable than any scraper but requires a
Meta app, a linked Facebook Page, and app review. The provider layer below is designed so
Graph API can be added as one more adapter without touching the pipeline.

---

## System architecture

```
Windows Task Scheduler (hourly)
            │
            ▼
     cli.py  run
            │
   ┌────────┼────────────────┬──────────────┐
   ▼        ▼                ▼              ▼
discovery  enrichment    attribution     persistence
   │        │                │              │
   │   profile lookup    hashtag →      SQLite  ──▶  sheets.py ──▶ Google Sheet
   │   + tier assign     campaign       (truth)
   ▼        ▼                ▼
      providers/  (RapidAPI adapters behind one interface)
            │
       client.py  — key rotation, backoff, cache, budget guard
```

**Tech stack**

- **Python 3.12** (already installed at `C:\Users\steve\AppData\Local\Programs\Python\Python312`)
- `httpx` — HTTP with timeouts and connection pooling
- `pydantic` v2 — validate and normalize provider JSON into canonical models
- `sqlite3` (stdlib) — storage, no server to run
- `tenacity` — retry and exponential backoff
- `gspread` + `google-auth` — Google Sheets export via a service account
- `PyYAML` — campaign and roster config
- `typer` — CLI
- `pytest` + `respx` — tests with mocked HTTP
- Scheduling: **Windows Task Scheduler**, not cron, since this is Windows 11

---

## Module layout

```
C:\Hyeri\Clipping\
  Plan.md                     ← this plan
  .env                        ← API keys, gitignored
  .env.example
  .gitignore
  pyproject.toml
  config/
    campaigns.yaml            ← campaign → hashtag mapping
    roster.yaml               ← contracted influencer handles
  clipping/
    __init__.py
    config.py                 ← env + YAML loading, validation
    models.py                 ← Post, Creator, Metrics, Campaign (pydantic)
    client.py                 ← key rotation, retry, cache, budget guard
    providers/
      base.py                 ← Provider protocol
      social_api.py           ← primary adapter
      scraper_2025.py         ← fallback adapter
    discovery.py              ← tagged feed + roster crawl → candidates
    enrichment.py             ← profile lookup, follower count
    tiering.py                ← follower count → tier
    matcher.py                ← hashtags → campaign
    store.py                  ← SQLite schema + upserts
    sheets.py                 ← Google Sheets export
    cli.py                    ← run / refresh / export / doctor / backfill
  tests/
    fixtures/                 ← recorded provider JSON
    test_tiering.py
    test_matcher.py
    test_normalize.py
```

---

## Data model (SQLite)

Six tables. The two history tables are what make growth curves and fair campaign
comparison possible.

```sql
creators(handle PK, ig_user_id, full_name, is_verified, is_private,
         follower_count, tier, profile_pic_url, last_profile_check_at)

posts(shortcode PK, ig_media_id, creator_handle FK, url, media_type,
      caption, taken_at, thumbnail_url, campaign_id, discovery_source,
      discovered_at, creator_followers_at_post, tier_at_post, metrics_frozen)

post_hashtags(shortcode, hashtag, PRIMARY KEY(shortcode, hashtag))

metrics(id PK, shortcode FK, captured_at, like_count, comment_count,
        view_count, play_count)

follower_history(handle, captured_at, follower_count, PRIMARY KEY(handle, captured_at))

api_calls(id PK, provider, endpoint, status_code, key_index, called_at)
runs(id PK, started_at, finished_at, status, posts_found, posts_new, error_text)
```

All timestamps are ISO 8601 UTC, for example `2026-09-10T14:03:00Z`.

Two deliberate choices:

- **`tier_at_post` is frozen onto the post row.** A nano creator who later goes viral must
  not retroactively become a Macro in last quarter's campaign report. The `creators` table
  holds the current tier; the post row holds the tier as of discovery.
- **Every hashtag is stored, not just campaign ones.** When you launch a campaign next month
  you can re-run attribution over historical posts without re-fetching anything.

---

## RapidAPI provider strategy

RapidAPI Instagram scrapers change response shapes and disappear without notice. That is the
single biggest operational risk in this project, and it drives the adapter design: the
pipeline talks to a `Provider` protocol with four methods, never to a URL.

```python
class Provider(Protocol):
    def get_tagged_posts(self, handle: str, cursor: str | None) -> RawPage: ...
    def get_user_posts(self, handle: str, cursor: str | None) -> RawPage: ...
    def get_profile(self, handle: str) -> RawProfile: ...
    def get_post(self, shortcode: str) -> RawPost: ...
```

**Recommended primary — Instagram Scraper API (`social-api1-instagram`)**, host
`instagram-scraper-api2.p.rapidapi.com`. It exposes `GET /v1/tagged`, `GET /v1/posts`,
`GET /v1/info`, and `GET /v1/post_info`, which is exactly the four-method surface above with
no gaps. Cursor pagination is consistent across endpoints.

**Recommended fallback — Instagram Scraper 2025 (`DavidGelling`)**, host
`instagram-scraper-20251.p.rapidapi.com`. It carries an equivalent User Tagged Posts, User
Info, and User Posts set, so it can stand in without a pipeline change.

The first implementation step is `cli.py doctor`, which pings both hosts with your key and
prints which endpoints actually answer and what their response shape is. Confirm both
subscriptions in the RapidAPI dashboard before writing adapters against them, because
pricing and endpoint availability shift between the published docs and the live product.

**Field mapping** (both providers, normalized into `models.Post`):

| Canonical field | Source |
|---|---|
| Influencer handle | `user.username` |
| Post URL | built from `code` / `shortcode` as `instagram.com/p/{code}` |
| Media details | `media_type`, `image_versions`, `video_url`, `carousel_media` |
| Hashtags | regex over `caption.text`, plus any provider-supplied tag array |
| Like count | `like_count` |
| Comment count | `comment_count` |
| Follower count | `follower_count` from the profile endpoint, not the post |

Hashtag extraction runs on the raw caption with a Unicode-aware pattern so Korean tags such
as `#이유닉` parse correctly. An ASCII-only regex would silently drop them.

**Cost control.** Profile lookups dominate quota, so they are cached for 24 hours per
creator. Metric refresh decays with post age: posts under 7 days refresh hourly, 7–30 days
daily, over 30 days weekly, then `metrics_frozen` is set and they stop costing anything.
A per-run call budget aborts the run with a clear message rather than silently draining the
plan.

---

## Tier logic

```
follower_count <  1_000                      → Nano
1_000      <= fc <=    10_000                → Micro
10_001     <= fc <=   100_000                → Mid
100_001    <= fc <= 1_000_000                → Macro
                fc >  1_000_000              → Mega
```

Exactly `1_000_000` lands in Macro, since Mega is specified as strictly greater than 1M.
Boundary values get explicit unit tests.

---

## Campaign attribution

`config/campaigns.yaml`:

```yaml
campaigns:
  - id: spring_glow_2026
    name: "Spring Glow Launch"
    hashtags: ["iunikspringglow", "이유닉스프링", "iunikpartner"]
    start: 2026-03-01
    end: 2026-04-30
    priority: 10
```

Matching: normalize both sides (strip `#`, casefold, Unicode NFKC), intersect the post's tag
set with each campaign's tag set, keep only campaigns whose window contains `taken_at`, and
on a tie take the highest `priority`. No match sets `campaign_id` to `unattributed` rather
than null, so unattributed posts stay visible in the sheet instead of hidden.

---

## Implementation plan

Each step names its own verification. Nothing is done without the check passing.

**0. Scaffold** — create the package layout, `pyproject.toml`, `.gitignore`, `.env.example`,
and `git init`.
→ verify: `pip install -e .` succeeds and `.env` is ignored by git.

**1. Config + models** — `config.py` loads keys and YAML; `models.py` defines the canonical
types.
→ verify: unit test loads a sample `campaigns.yaml` and rejects a malformed one.

**2. `doctor` command** — ping both RapidAPI hosts, print status and a truncated response
body per endpoint.
→ verify: run it against the live key; confirm which of the four endpoints answer on each
provider before building adapters.

**3. HTTP client** — key rotation, backoff on 429 and 5xx, SQLite response cache, call budget.
→ verify: `respx` tests prove a 429 rotates to the next key and that a cache hit issues zero
network calls.

**4. Primary adapter + normalizers** — map the primary provider's JSON onto the canonical
models, driven by the real responses recorded in step 2.
→ verify: golden-file tests over `tests/fixtures/`. These tests are the early-warning system
for provider schema drift.

**5. Storage** — schema creation, idempotent upserts, history appends.
→ verify: ingesting the same post twice yields one `posts` row and two `metrics` rows.

**6. Tiering + matcher** — pure functions, no I/O.
→ verify: boundary tests at 999 / 1,000 / 10,000 / 10,001 / 100,000 / 100,001 / 1,000,000 /
1,000,001, and a matcher test with a Korean hashtag.

**7. Discovery pipeline** — tagged feed plus roster crawl, deduplicated by shortcode, with
`discovery_source` recorded so you can see which channel found what.
→ verify: `run --dry-run` against the live API prints discovered posts and writes nothing.

**8. Google Sheets export** — service-account auth, one row per post with current metrics
plus a campaign summary tab.
→ verify: run `export`, open the sheet, confirm rows and tier counts match a `SELECT`
against SQLite.

**9. Fallback adapter** — the second provider behind the same protocol, with automatic
failover when the primary returns errors or fails validation.
→ verify: force the primary to fail in a test and confirm the run completes via the fallback.

**10. Scheduling** — a `run.ps1` wrapper plus a documented Windows Task Scheduler task,
hourly, logging to `logs/` with rotation.
→ verify: let the scheduled task fire twice unattended; check that `runs` has two rows and
the sheet updated.

---

## End-to-end verification

After step 10, the acceptance test is:

1. `python -m clipping doctor` — every configured endpoint reports OK.
2. `python -m clipping run --dry-run` — prints discovered posts with creator, tier, hashtags,
   and matched campaign, writing nothing.
3. `python -m clipping run` — populates SQLite. Querying post counts grouped by tier returns
   a sane distribution across all five tiers.
4. Run it again an hour later — the `posts` row count is stable while the `metrics` row count
   doubles. This proves deduplication and history are both working.
5. `python -m clipping export` — the Google Sheet shows every post with a clickable URL, and
   campaign totals match the database.
6. Pick one real post by hand, open it on Instagram, and compare likes, comments, and the
   creator's follower count against the row. This is the only check that catches a provider
   returning well-formed but wrong data.

---

## Out of scope for this build

Story mentions, hashtag-wide discovery of non-roster creators, comment sentiment analysis,
and automated payment reconciliation. Each is a clean addition later; none is needed to
prove campaign ROI.

---

# Addendum — Endpoint research, verified 2026-09-10

The provider recommendation above was written from documentation. It has now been tested
against the live RapidAPI gateway using the supplied key. Three findings change the picture.

## Finding 1 — the key has no active subscription

Every Instagram API tested returned the same response:

```
[403] instagram-scraper-api2.p.rapidapi.com/v1/info
      {"message":"You are not subscribed to this API."}
```

A RapidAPI key alone grants nothing. Each API must be subscribed to individually from its
RapidAPI page, even on a free tier. Nothing in this project can fetch a single post until
that is done.

A deliberately invalid key returns the **identical** 403, so this response cannot confirm
whether the supplied key is valid. That question stays open until a subscription exists.

## Finding 2 — provider churn is real and already visible

The gateway distinguishes an unlisted API (`404 API doesn't exists`) from a listed but
unsubscribed one (`403 not subscribed`). Sweeping seventeen candidate hosts:

**Live on RapidAPI:** `instagram-scraper-api2`, `instagram-social-api`,
`instagram-scraper-20251`, `instagram-looter2`, `instagram-scraper-stable-api`,
`instagram-api-fast-reliable-data-scraper`, `instagram-statistics-api`,
`instagram-scraper-v21`, `instagram-data1`, `instagram230`, `instagram-social`,
`social-api4`, `instagram-scraper2`, `instagram-bulk-profile-scrapper`

**Gone:** `instagram120`, `instagram-premium-api-2023`, `instagram-scraper-20231`,
`rocketapi-for-instagram`

RocketAPI is the cautionary case. It was a widely recommended RapidAPI Instagram provider and
has now migrated off the marketplace entirely. This is exactly the risk the `Provider`
protocol exists to absorb, and it justifies building the fallback adapter in step 9 rather
than deferring it.

## Finding 3 — the two providers first recommended share a backend

`instagram-scraper-api2` and `instagram-social-api` both expose the same `/v1/info`,
`/v1/tagged`, `/v1/posts` path family. They are near-certainly thin wrappers over one shared
backend, so pairing them as primary and fallback buys **no real redundancy** — they would
fail together. The fallback must come from a different path family.

## Revised recommendation

| Role | API | Host | Why |
|---|---|---|---|
| Primary | Instagram Scraper API | `instagram-scraper-api2.p.rapidapi.com` | Only RapidAPI family with all four required methods on documented paths. The `scrapify-ig` Python wrapper exposes `get_tagged_medias_chunk` against it, independently confirming tagged support. |
| Fallback | Instagram Scraper 2025 | `instagram-scraper-20251.p.rapidapi.com` | Different path family, so it fails independently. Carries User Tagged Posts, User Info, User Posts. |

Primary endpoint map:

| Need | Endpoint |
|---|---|
| Tagged posts | `GET /v1/tagged?username_or_id_or_url=iunik_official` |
| Profile + follower count | `GET /v1/info?username_or_id_or_url={handle}` |
| Roster crawl | `GET /v1/posts?username_or_id_or_url={handle}` |
| Single post refresh | `GET /v1/post_info?code_or_id_or_url={shortcode}` |

Path validity behind the paywall is still unverified — the gateway rejects on subscription
before it routes. Step 2 (`doctor`) confirms these paths the moment a subscription exists.

## Non-RapidAPI option worth knowing about

HikerAPI sells direct at `api.hikerapi.com`, not through RapidAPI. Its live OpenAPI spec
confirms an exact match for all four methods:

| Need | Endpoint |
|---|---|
| Tagged posts | `GET /v2/user/tag/medias` |
| Profile + follower count | `GET /v2/user/by/username` |
| Roster crawl | `GET /v1/user/medias/chunk` |
| Single post refresh | `GET /v2/media/info/by/code` |

Pricing is $0.60–$1.00 per 1,000 requests, and it authenticates with an `x-access-key`
header. This is the strongest technical option found, verified from the machine-readable
spec rather than marketing copy. It is **not** proposed as a replacement, since RapidAPI is
a stated constraint. The adapter design means it can be added later as one more `Provider`
implementation without touching the pipeline.

## Correction to the refresh schedule

The cost-control section above specifies hourly refresh for posts under 7 days old. At a
50-influencer roster with roughly 40 posts in that window, that alone is about 28,800 calls
per month and dominates everything else.

Revised default: **refresh fresh posts every 6 hours, not hourly.** Where the provider
returns like and comment counts inside the feed response, harvest them there instead of
issuing a per-post call.

Estimated steady-state volume with the revised schedule:

| Workload | Calls/month |
|---|---|
| Tagged feed, hourly | ~1,000 |
| Roster crawl, daily × 50 | ~1,500 |
| Profile refresh, daily × 50 | ~1,500 |
| Metric refresh, 6-hourly | ~4,800 |
| **Total** | **~9,000** |

That lands in a paid tier on most listings, roughly $10–30 per month. Size the subscription
against this number rather than a free tier.

## Immediate next actions

1. Subscribe to **Instagram Scraper API** on RapidAPI, free tier is enough to validate.
2. Subscribe to **Instagram Scraper 2025** as the independent fallback.
3. Rotate the exposed key and issue a genuinely distinct second one.
4. Run `doctor` (step 2) to confirm the four paths and record real response shapes as test
   fixtures.

## Known issue for step 7 (CLI)

Windows consoles do not default to UTF-8. On this machine `sys.stdout.encoding` is `cp949`,
and on a non-Korean Windows locale it would be `cp1252`, where printing a Korean hashtag
raises `UnicodeEncodeError` and kills the run. Storage and matching are unaffected, since
the data round-trips correctly; only console output is at risk. The CLI must reconfigure
stdout to UTF-8 before printing captions or hashtags.
