"""Command line entry point."""

import json
import sys
from pathlib import Path

import httpx
import typer

from clipping import doctor as doctor_mod
from clipping import normalize, store
from clipping.config import ConfigError, load_campaigns, load_roster, load_settings
from clipping.matcher import match
from clipping.providers import PROVIDERS, by_key
from clipping.tiering import classify

app = typer.Typer(add_completion=False, help="Instagram influencer clipping and tracking.")

# Windows consoles default to a legacy codepage, where printing a Korean hashtag raises
# UnicodeEncodeError. Do this before anything is written.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TICK, CROSS, DOT = "PASS", "FAIL", "  - "


@app.callback()
def main() -> None:
    """Instagram influencer clipping and tracking.

    This callback exists so Typer keeps subcommand names. Without it a single-command app
    collapses and `clipping doctor` is rejected, which would break once run and export land.
    """


def _rule(title: str) -> None:
    typer.echo(f"\n{title}\n{'-' * max(len(title), 60)}")


def _check_config() -> bool:
    """Report configuration health. Returns False if something is fatally wrong."""
    _rule("Configuration")
    healthy = True

    try:
        settings = load_settings()
        typer.echo(f"{DOT}brand handle       : @{settings.brand_handle}")
        typer.echo(f"{DOT}usable API keys    : {len(settings.rapidapi_keys)}")
        if settings.duplicate_keys_dropped:
            typer.echo(
                f"{DOT}WARNING            : {settings.duplicate_keys_dropped} duplicate "
                "key(s) dropped. Identical keys give no failover and no extra quota."
            )
        if len(settings.rapidapi_keys) == 1:
            typer.echo(
                f"{DOT}note               : only one key, so rotation has nowhere to go."
            )
        typer.echo(f"{DOT}call budget/run    : {settings.max_calls_per_run}")
        typer.echo(f"{DOT}database           : {settings.db_path}")
        typer.echo(
            f"{DOT}Google Sheet       : "
            f"{'configured' if settings.google_sheet_id else 'not configured'}"
        )
    except ConfigError as exc:
        typer.echo(f"{DOT}{CROSS} settings: {exc}")
        return False

    for label, loader in (("campaigns", load_campaigns), ("roster", load_roster)):
        try:
            items = loader()
            typer.echo(f"{DOT}{label:<19}: {len(items)} loaded")
        except ConfigError as exc:
            typer.echo(f"{DOT}{CROSS} {label}: {exc}")
            healthy = False

    return healthy


@app.command()
def doctor(
    handle: str = typer.Option(
        doctor_mod.DEFAULT_HANDLE, help="Handle to probe with. Use a real one."
    ),
    shortcode: str = typer.Option(
        doctor_mod.DEFAULT_SHORTCODE, help="Post shortcode for the single-post endpoint."
    ),
    save_fixtures: bool = typer.Option(
        False, "--save-fixtures", help="Write successful responses to tests/fixtures/."
    ),
    skip_network: bool = typer.Option(
        False, "--skip-network", help="Check configuration only; make no API calls."
    ),
) -> None:
    """Check configuration and report which provider endpoints actually answer."""
    config_ok = _check_config()

    if skip_network:
        typer.echo("\nSkipped network probes.")
        raise typer.Exit(0 if config_ok else 1)
    if not config_ok:
        typer.echo("\nFix the configuration above before probing endpoints.")
        raise typer.Exit(1)

    settings = load_settings()
    results = doctor_mod.run_probes(
        settings, handle=handle, shortcode=shortcode, save_fixtures=save_fixtures
    )
    grouped = doctor_mod.summarise(results)
    usable = []

    for spec in PROVIDERS:
        rows = grouped.get(spec.key, [])
        _rule(f"{spec.label}  ({spec.host})")
        if spec.note:
            typer.echo(f"{DOT}note: {spec.note}")

        for r in rows:
            mark = TICK if r.ok else CROSS
            status = r.status if r.status is not None else "---"
            typer.echo(
                f"  {mark}  {r.capability:<8} {r.path:<18} "
                f"[{status}] {r.diagnosis.verdict.value}"
                + (f"  {r.elapsed_ms}ms" if r.ok else "")
            )
            if r.ok and r.top_level_keys:
                typer.echo(f"          keys: {', '.join(r.top_level_keys)}")
            elif r.diagnosis.advice:
                typer.echo(f"          {r.diagnosis.advice}")

        if doctor_mod.is_usable(rows):
            usable.append(spec.label)
        else:
            typer.echo(f"{DOT}{doctor_mod.provider_summary(rows)}")
            typer.echo(f"{DOT}subscribe at {spec.signup_url}")

    _rule("Verdict")
    if usable:
        typer.echo(f"  Usable provider(s): {', '.join(usable)}")
        if save_fixtures:
            typer.echo("  Responses saved to tests/fixtures/ for adapter development.")
        raise typer.Exit(0)

    typer.echo("  No provider can currently fetch tagged posts and profiles.")
    typer.echo("  A RapidAPI key alone is not enough; each API needs its own subscription.")
    raise typer.Exit(1)


def _get(client: httpx.Client, spec, capability: str, key: str, handle: str = "",
         shortcode: str = "") -> dict:
    """One provider call. The full client with key rotation and caching is still to come."""
    endpoint = spec.endpoint(capability)
    response = client.get(
        f"https://{spec.host}{endpoint.path}",
        params=endpoint.query(handle, shortcode),
        headers={"x-rapidapi-key": key, "x-rapidapi-host": spec.host},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


@app.command()
def run(
    provider: str = typer.Option("scraper_2025", help="Provider key to use."),
    fixture: Path = typer.Option(
        None, help="Replay a saved tagged response instead of calling the API."
    ),
    max_profiles: int = typer.Option(
        5, help="Cap profile lookups. Each one costs an API call."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not write to the database."),
) -> None:
    """Discover tagged posts, tier the creators, attribute campaigns, store the results."""
    settings = load_settings()
    campaigns = load_campaigns()
    spec = by_key(provider)
    if spec is None:
        typer.echo(f"Unknown provider '{provider}'.")
        raise typer.Exit(1)

    key = settings.rapidapi_keys[0]
    calls = 0

    with httpx.Client() as client:
        if fixture:
            payload = json.loads(Path(fixture).read_text(encoding="utf-8"))
            typer.echo(f"Replaying {fixture}, no API call spent.")
        else:
            payload = _get(client, spec, "tagged", key, handle=settings.brand_handle)
            calls += 1

        parsed = normalize.parse_feed(payload, "tagged")
        handles = list(dict.fromkeys(p.creator_handle for p, _ in parsed))
        _rule(f"Discovered {len(parsed)} tagged posts from {len(handles)} creators")

        # Follower counts are absent from the tagged response, so tiering costs one
        # profile call per creator. That is the dominant expense in the whole pipeline.
        profiles: dict[str, object] = {}
        for handle in handles[:max_profiles]:
            try:
                profiles[handle] = normalize.parse_profile(
                    _get(client, spec, "profile", key, handle=handle)
                )
                calls += 1
            except (httpx.HTTPError, ValueError) as exc:
                typer.echo(f"  could not read @{handle}: {type(exc).__name__}")

    skipped = len(handles) - len(profiles)

    conn = None if dry_run else store.connect(settings.db_path)
    run_id = None if conn is None else store.start_run(conn)
    stored = 0

    typer.echo(f"  {'creator':<24} {'tier':<7} {'followers':>10} {'likes':>8} "
               f"{'cmts':>6}  campaign")
    typer.echo(f"  {'-' * 24} {'-' * 7} {'-' * 10} {'-' * 8} {'-' * 6}  {'-' * 18}")

    for post, metrics in parsed:
        creator = profiles.get(post.creator_handle)
        if creator is None:
            continue  # no follower count, so the tier would be a guess
        campaign = match(post.hashtags, post.taken_at.date(), campaigns)
        typer.echo(
            f"  @{post.creator_handle:<23} {classify(creator.follower_count).value:<7} "
            f"{creator.follower_count:>10,} {metrics.like_count:>8,} "
            f"{metrics.comment_count:>6,}  {campaign}"
        )
        if conn is not None:
            store.upsert_creator(conn, creator)
            store.upsert_post(conn, post, follower_count=creator.follower_count,
                              campaign_id=campaign)
            store.record_metrics(conn, post.shortcode, metrics)
            stored += 1

    if conn is not None:
        store.finish_run(conn, run_id, posts_found=len(parsed), posts_new=stored)
        conn.commit()

    _rule("Summary")
    typer.echo(f"  API calls spent    : {calls}")
    typer.echo(f"  posts discovered   : {len(parsed)}")
    typer.echo(f"  creators tiered    : {len(profiles)}")
    if skipped:
        typer.echo(
            f"  creators skipped   : {skipped} (no profile lookup, so no follower count)"
        )
    typer.echo(f"  rows written       : {'0 (dry run)' if dry_run else stored}")


if __name__ == "__main__":
    app()
