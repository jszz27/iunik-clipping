# Clipping

Automated Instagram influencer tracking. It finds posts that tag or mention a brand, records
engagement over time, classifies each creator by follower tier, and attributes posts to
marketing campaigns by hashtag.

Built for `@iunik_official`, but the brand handle is configuration, not code.

## Status

Work in progress. The parts that need no network access are complete and tested. The
provider adapters, discovery pipeline, and Google Sheets export are not built yet.

| Component | State |
|---|---|
| Tier classification | done |
| Campaign attribution | done |
| SQLite storage and history | done |
| Config and validation | done |
| RapidAPI adapters | not started |
| Discovery pipeline | not started |
| Google Sheets export | not started |
| Scheduling | not started |

`Plan.md` holds the full architecture, the endpoint research, and the remaining build steps.

## How it works

Two discovery channels feed one deduplicating pipeline. The tagged-posts feed catches posts
where the brand is tagged in the media. A roster crawl polls creators you have contracts
with and filters their recent posts, which catches caption-only mentions that never appear
in the tagged feed.

Two design choices are worth calling out:

- **Attribution is frozen at discovery.** A post keeps the follower count and tier the
  creator had when it was found, so someone going viral later cannot rewrite an earlier
  campaign's report.
- **Metrics accumulate rather than overwrite.** Every check appends a row, which is what
  makes growth curves and fair campaign comparison possible.

## Tiers

| Tier | Followers |
|---|---|
| Nano | under 1,000 |
| Micro | 1,000 to 10,000 |
| Mid | 10,001 to 100,000 |
| Macro | 100,001 to 1,000,000 |
| Mega | over 1,000,000 |

## Setup

Requires Python 3.12 or newer.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # macOS / Linux
```

Copy `.env.example` to `.env` and fill it in. `.env` is gitignored; never commit it.

A RapidAPI key on its own is not enough. Each API must be subscribed to individually from
its own RapidAPI page, even on a free tier. Without a subscription every request returns
`403 You are not subscribed to this API`.

## Configuration

`config/campaigns.yaml` maps hashtags to campaigns. A post is attributed when it carries one
of the campaign's hashtags and its date falls inside the window. Highest priority wins ties.

```yaml
campaigns:
  - id: spring_glow_2026
    name: "Spring Glow Launch"
    hashtags: ["iunikspringglow", "이유닉스프링"]
    start: 2026-03-01
    end: 2026-04-30
    priority: 10
```

Hashtags are casefolded and Unicode-normalized on load, so `#IUnikSpringGlow` in the file
matches `#iunikspringglow` in a caption. Non-Latin hashtags are supported.

`config/roster.yaml` lists creators under contract:

```yaml
roster:
  - handle: some_creator
    note: "Spring Glow, 3-post package"
```

Every post stores all of its hashtags, not just campaign ones, so a campaign launched next
month can claim historical posts without spending any API calls.

## Trying it

There is no data-fetching command yet, because the provider adapters are not built. To see
the parts that do work, run the demo. It uses synthetic creators and an in-memory database,
makes no network calls, and writes no files.

```bash
.venv/Scripts/python.exe demo.py
```

It walks through campaign loading, tier classification, hashtag extraction and attribution,
two deduplicated ingestion runs, and the refresh schedule.

## Tests

```bash
.venv/Scripts/python.exe -m pytest
```

## Licence

MIT. See [LICENSE](LICENSE).
