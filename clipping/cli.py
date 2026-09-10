"""Command line entry point."""

import sys

import typer

from clipping import doctor as doctor_mod
from clipping.config import ConfigError, load_campaigns, load_roster, load_settings
from clipping.providers import PROVIDERS

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


if __name__ == "__main__":
    app()
