"""Command line entry point."""

import sys
from pathlib import Path

import typer

from clipping import doctor as doctor_mod
from clipping import pipeline, store
from clipping.config import ConfigError, load_campaigns, load_roster, load_settings
from clipping.providers import PROVIDERS, by_key

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


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address. Keep it local."),
    port: int = typer.Option(8000, help="Port to listen on."),
) -> None:
    """Open the dashboard in a browser: run the pipeline, inspect it, export to Sheets."""
    try:
        import uvicorn
    except ImportError:
        typer.echo('Web UI not installed. Run: pip install -e ".[web]"')
        raise typer.Exit(1)

    typer.echo(f"Dashboard at http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run("clipping.web:app", host=host, port=port, log_level="warning")


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

    if fixture:
        typer.echo(f"Replaying {fixture}, no API call spent for the tagged page.")

    result = pipeline.ingest(
        settings, campaigns, spec,
        fixture=str(fixture) if fixture else None,
        max_profiles=max_profiles,
        dry_run=dry_run,
    )

    _rule(f"Discovered {result.discovered} tagged posts")
    typer.echo(f"  {'creator':<24} {'tier':<7} {'followers':>10} {'likes':>8} "
               f"{'cmts':>6}  campaign")
    typer.echo(f"  {'-' * 24} {'-' * 7} {'-' * 10} {'-' * 8} {'-' * 6}  {'-' * 18}")
    for r in result.rows:
        typer.echo(
            f"  @{r['creator']:<23} {r['tier']:<7} {r['followers']:>10,} "
            f"{r['likes']:>8,} {r['comments']:>6,}  {r['campaign']}"
        )

    for err in result.errors:
        typer.echo(f"  could not read {err}")

    _rule("Summary")
    typer.echo(f"  API calls spent    : {result.calls}")
    typer.echo(f"  posts discovered   : {result.discovered}")
    typer.echo(f"  creators tiered    : {result.tiered}")
    if result.skipped:
        typer.echo(
            f"  creators skipped   : {result.skipped} "
            "(no profile lookup, so no follower count)"
        )
    typer.echo(f"  rows written       : {'0 (dry run)' if dry_run else result.stored}")
    if result.quota_remaining is not None:
        limit = f" of {result.quota_limit}" if result.quota_limit else ""
        typer.echo(f"  API quota left     : {result.quota_remaining}{limit}")


@app.command()
def reattribute(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without writing."
    ),
) -> None:
    """Recompute campaigns for stored posts after editing campaigns.yaml. No API calls."""
    try:
        campaigns = load_campaigns()
    except ConfigError as exc:
        typer.echo(f"campaigns.yaml: {exc}")
        raise typer.Exit(1)

    settings = load_settings()
    conn = store.connect(settings.db_path)
    try:
        changes = store.reattribute(conn, campaigns, dry_run=dry_run)
        total = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    finally:
        conn.close()

    if not changes:
        typer.echo(f"No changes. All {total} stored post(s) already match campaigns.yaml.")
        return

    _rule(f"{len(changes)} of {total} post(s) would change" if dry_run
          else f"{len(changes)} of {total} post(s) re-attributed")
    for c in changes:
        typer.echo(f"  @{c['creator']:<24} {c['before']:<18} -> {c['after']}")
    if dry_run:
        typer.echo("\nNothing written. Re-run without --dry-run to apply.")


if __name__ == "__main__":
    app()
