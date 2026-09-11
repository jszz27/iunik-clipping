"""Load settings from .env and campaign/roster definitions from config/*.yaml.

Every failure raises ConfigError with a message that names the file and says how to fix it.
Configuration is edited by hand by the marketing team, so a precise error is worth more
than a tolerant parser that silently drops a campaign.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

from clipping.matcher import Campaign, normalize

DEFAULT_ENV = Path(".env")
DEFAULT_CAMPAIGNS = Path("config/campaigns.yaml")
DEFAULT_ROSTER = Path("config/roster.yaml")


class ConfigError(Exception):
    """Configuration is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    rapidapi_keys: tuple[str, ...]
    brand_handle: str
    max_calls_per_run: int
    db_path: str
    google_sheet_id: str | None
    google_service_account_json: str | None
    google_oauth_client_json: str | None
    duplicate_keys_dropped: int


@dataclass(frozen=True)
class RosterEntry:
    handle: str
    note: str = ""


def load_env_file(path: Path = DEFAULT_ENV) -> dict[str, str]:
    """Parse a KEY=VALUE file. Blank lines and # comments are ignored."""
    if not Path(path).exists():
        return {}
    values: dict[str, str] = {}
    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{path} line {lineno}: expected KEY=VALUE, got {raw!r}")
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_settings(
    env_path: Path = DEFAULT_ENV, environ: dict[str, str] | None = None
) -> Settings:
    """Build Settings from the process environment, falling back to the .env file."""
    env = {**load_env_file(env_path), **(environ if environ is not None else os.environ)}

    raw_keys = [k.strip() for k in env.get("RAPIDAPI_KEYS", "").split(",") if k.strip()]
    if not raw_keys:
        raise ConfigError(
            f"RAPIDAPI_KEYS is empty. Copy .env.example to {env_path} and add at least one "
            "RapidAPI key. Note that a key also needs an active subscription to each API."
        )

    # Duplicates are dropped: repeating one key gives no extra quota and no failover, so
    # rotation must not be fooled into thinking it has two independent keys.
    keys = tuple(dict.fromkeys(raw_keys))

    try:
        max_calls = int(env.get("MAX_CALLS_PER_RUN", "500"))
    except ValueError as exc:
        raise ConfigError(f"MAX_CALLS_PER_RUN must be a whole number: {exc}") from exc
    if max_calls < 1:
        raise ConfigError(f"MAX_CALLS_PER_RUN must be at least 1, got {max_calls}")

    return Settings(
        rapidapi_keys=keys,
        brand_handle=env.get("BRAND_HANDLE", "iunik_official").lstrip("@").casefold(),
        max_calls_per_run=max_calls,
        db_path=env.get("DB_PATH", "clipping.db"),
        google_sheet_id=env.get("GOOGLE_SHEET_ID") or None,
        google_service_account_json=env.get("GOOGLE_SERVICE_ACCOUNT_JSON") or None,
        google_oauth_client_json=env.get("GOOGLE_OAUTH_CLIENT_JSON") or None,
        duplicate_keys_dropped=len(raw_keys) - len(keys),
    )


def _read_yaml(path: Path, top_key: str) -> list:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"{p} not found. See config/ for the expected layout.")
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p} is not valid YAML: {exc}") from exc
    if doc is None:
        return []
    if not isinstance(doc, dict) or top_key not in doc:
        raise ConfigError(f"{p} must contain a top-level '{top_key}:' key.")
    entries = doc[top_key] or []
    if not isinstance(entries, list):
        raise ConfigError(f"{p}: '{top_key}' must be a list, got {type(entries).__name__}.")
    return entries


def _as_date(value, path: Path, campaign_id: str, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(
                f"{path}: campaign '{campaign_id}' has an unparseable {field} "
                f"({value!r}). Use YYYY-MM-DD."
            ) from exc
    raise ConfigError(f"{path}: campaign '{campaign_id}' is missing {field}. Use YYYY-MM-DD.")


def load_campaigns(path: Path = DEFAULT_CAMPAIGNS) -> list[Campaign]:
    campaigns: list[Campaign] = []
    seen: set[str] = set()

    for index, entry in enumerate(_read_yaml(path, "campaigns"), 1):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: campaign #{index} must be a mapping with an 'id'.")

        cid = str(entry.get("id") or "").strip()
        if not cid:
            raise ConfigError(f"{path}: campaign #{index} is missing 'id'.")
        if cid in seen:
            raise ConfigError(f"{path}: duplicate campaign id '{cid}'.")
        seen.add(cid)

        raw_tags = entry.get("hashtags") or []
        if isinstance(raw_tags, str):
            raise ConfigError(
                f"{path}: campaign '{cid}' hashtags must be a list, not a single string."
            )
        tags = {normalize(t) for t in raw_tags if str(t).strip()}
        if not tags:
            raise ConfigError(
                f"{path}: campaign '{cid}' has no hashtags, so nothing could ever match it."
            )

        start = _as_date(entry.get("start"), path, cid, "start")
        end = _as_date(entry.get("end"), path, cid, "end")
        if start > end:
            raise ConfigError(
                f"{path}: campaign '{cid}' starts ({start}) after it ends ({end})."
            )

        try:
            priority = int(entry.get("priority", 0))
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"{path}: campaign '{cid}' priority must be a whole number."
            ) from exc

        campaigns.append(Campaign(
            id=cid,
            name=str(entry.get("name") or cid),
            hashtags=frozenset(tags),
            start=start,
            end=end,
            priority=priority,
        ))

    return campaigns


def load_roster(path: Path = DEFAULT_ROSTER) -> list[RosterEntry]:
    roster: list[RosterEntry] = []
    seen: set[str] = set()

    for index, entry in enumerate(_read_yaml(path, "roster"), 1):
        if isinstance(entry, str):
            raise ConfigError(
                f"{path}: roster #{index} is a bare string. Use '- handle: {entry}' instead."
            )
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: roster #{index} must be a mapping with a 'handle'.")

        handle = str(entry.get("handle") or "").strip().lstrip("@").casefold()
        if not handle:
            raise ConfigError(f"{path}: roster #{index} is missing 'handle'.")
        if handle in seen:
            continue  # listing a creator twice is harmless, just crawl them once
        seen.add(handle)
        roster.append(RosterEntry(handle=handle, note=str(entry.get("note") or "")))

    return roster
