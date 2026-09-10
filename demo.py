"""Run the offline pipeline against synthetic data, to see it work without an API key.

    python demo.py

Everything here uses an in-memory database and made-up creators. It touches no network
and writes no files. Real discovery needs the provider adapters, which are not built yet.
"""

import sys
from datetime import datetime, timedelta, timezone

from clipping import store
from clipping.config import load_campaigns
from clipping.matcher import extract_hashtags, match
from clipping.models import Creator, Metrics, Post
from clipping.tiering import classify

# Windows consoles default to a legacy codepage; printing Korean would raise
# UnicodeEncodeError on a non-Korean locale without this.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NOW = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)

# Synthetic creators, one per tier, so every branch of the tier logic is visible.
CREATORS = [
    ("nano_kim", 840),
    ("micro_lee", 8_400),
    ("mid_park", 45_000),
    ("macro_choi", 320_000),
    ("mega_jung", 4_100_000),
]

CAPTIONS = [
    "협찬 받았어요 #이유닉스프링 #skincare",           # Korean campaign tag
    "loving this #IUnikSpringGlow #glassskin",         # mixed case, same campaign
    "day 3 of the routine #iunikpartner",              # different tag, same campaign
    "my everyday picks #iunik #kbeauty",               # always-on campaign
    "just a random post #unrelated",                   # matches nothing
]


def rule(title):
    print(f"\n{'=' * 66}\n{title}\n{'=' * 66}")


def main():
    campaigns = load_campaigns()
    rule("1. CAMPAIGNS LOADED FROM config/campaigns.yaml")
    for c in campaigns:
        print(f"  {c.id:<18} priority={c.priority:<3} {c.start} to {c.end}")
        print(f"  {'':18} hashtags: {', '.join(sorted(c.hashtags))}")

    rule("2. TIER CLASSIFICATION")
    for handle, followers in CREATORS:
        print(f"  {handle:<12} {followers:>10,} followers  ->  {classify(followers).value}")

    rule("3. HASHTAG EXTRACTION AND CAMPAIGN ATTRIBUTION")
    for caption in CAPTIONS:
        tags = extract_hashtags(caption)
        cid = match(tags, NOW.date(), campaigns)
        print(f"  {caption}")
        print(f"    tags    : {tags}")
        print(f"    campaign: {cid}\n")

    conn = store.connect(":memory:")

    rule("4. TWO INGESTION RUNS, EIGHT HOURS APART")
    for run_no, (when, likes) in enumerate([(NOW - timedelta(hours=8), 40), (NOW, 310)], 1):
        run_id = store.start_run(conn, now=when)
        new = 0
        for i, (handle, followers) in enumerate(CREATORS):
            store.upsert_creator(conn, Creator(handle=handle, follower_count=followers),
                                 now=when)
            caption = CAPTIONS[i]
            tags = extract_hashtags(caption)
            post = Post(shortcode=f"POST{i}", creator_handle=handle,
                        taken_at=NOW - timedelta(days=2), caption=caption, hashtags=tags)
            new += store.upsert_post(conn, post, follower_count=followers,
                                     campaign_id=match(tags, post.taken_at.date(), campaigns),
                                     now=when)
            store.record_metrics(conn, post.shortcode,
                                 Metrics(like_count=likes * (i + 1), comment_count=i + 2),
                                 now=when)
        store.finish_run(conn, run_id, posts_found=len(CREATORS), posts_new=new, now=when)
        print(f"  run {run_no}: {len(CREATORS)} posts seen, {new} new")

    def q(sql):
        return conn.execute(sql).fetchall()

    posts = q("SELECT COUNT(*) c FROM posts")[0]["c"]
    metrics = q("SELECT COUNT(*) c FROM metrics")[0]["c"]
    print(f"\n  posts rows  : {posts}  (deduplicated, stays flat)")
    print(f"  metric rows : {metrics}  (history, grows every run)")

    rule("5. WHAT THE MARKETING REPORT WOULD SHOW")
    print(f"  {'creator':<12} {'tier':<7} {'campaign':<18} {'likes':>7} {'comments':>9}")
    print(f"  {'-' * 12} {'-' * 7} {'-' * 18} {'-' * 7} {'-' * 9}")
    for r in q("""SELECT p.creator_handle h, p.tier_at_post t, p.campaign_id c,
                         m.like_count l, m.comment_count cm
                  FROM posts p
                  JOIN metrics m ON m.shortcode = p.shortcode
                  WHERE m.captured_at = (SELECT MAX(captured_at) FROM metrics
                                         WHERE shortcode = p.shortcode)
                  ORDER BY m.like_count DESC"""):
        print(f"  {r['h']:<12} {r['t']:<7} {r['c']:<18} {r['l']:>7,} {r['cm']:>9}")

    rule("6. ENGAGEMENT GROWTH, WHICH A SNAPSHOT TABLE COULD NOT SHOW")
    for r in q("""SELECT captured_at, like_count FROM metrics
                  WHERE shortcode = 'POST4' ORDER BY captured_at"""):
        print(f"  {r['captured_at']}   {r['like_count']:>8,} likes")

    rule("7. WHAT WOULD BE REFRESHED ON THE NEXT RUN")
    print(f"  due in 7 hours : {store.posts_due_for_refresh(conn, now=NOW + timedelta(hours=7))}")
    print(f"  due in 1 hour  : {store.posts_due_for_refresh(conn, now=NOW + timedelta(hours=1))}")
    print("\n  Posts under a week old refresh every 6 hours, so one hour later nothing is due.")

    print("\nDone. No network calls, no files written.\n")


if __name__ == "__main__":
    main()
