"""Check what actually works before trusting anything else.

Probes every declared endpoint and says, per endpoint, whether it answered and what to do
if it did not. The gateway's error messages are specific enough to tell "you have not paid"
apart from "this API no longer exists", which are very different problems with very
different fixes.
"""

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import httpx

from clipping.config import Settings
from clipping.providers import PROVIDERS, ProviderSpec

# A real handle and post are needed because some endpoints 404 on nonsense input.
DEFAULT_HANDLE = "instagram"
DEFAULT_SHORTCODE = "C0000000000"

FIXTURE_DIR = Path("tests/fixtures")


class Verdict(str, Enum):
    OK = "ok"
    NOT_SUBSCRIBED = "not subscribed"
    NO_SUCH_API = "api delisted"
    NO_SUCH_ENDPOINT = "wrong path"
    RATE_LIMITED = "rate limited"
    AUTH_FAILED = "bad key"
    SERVER_ERROR = "provider down"
    TRANSPORT_ERROR = "unreachable"
    UNEXPECTED = "unexpected"

    @property
    def ok(self) -> bool:
        return self is Verdict.OK


@dataclass(frozen=True)
class Diagnosis:
    verdict: Verdict
    advice: str


def diagnose(status: int, body: str) -> Diagnosis:
    """Turn an HTTP status and body into a verdict and a next action.

    The message strings matched here are RapidAPI gateway responses observed directly.
    """
    lowered = (body or "").lower()

    if status == 200:
        return Diagnosis(Verdict.OK, "")
    if status == 401:
        return Diagnosis(Verdict.AUTH_FAILED, "Key rejected. Check RAPIDAPI_KEYS in .env.")
    if "api doesn't exist" in lowered:
        return Diagnosis(
            Verdict.NO_SUCH_API,
            "This API is no longer listed on RapidAPI. Pick a different provider.",
        )
    if "not subscribed" in lowered:
        return Diagnosis(
            Verdict.NOT_SUBSCRIBED,
            "Subscribe on the API's Pricing tab. A free tier is enough to validate.",
        )
    if status == 404:
        return Diagnosis(
            Verdict.NO_SUCH_ENDPOINT,
            "Host is live but this path is not. Correct it against the RapidAPI playground.",
        )
    if status == 429:
        return Diagnosis(
            Verdict.RATE_LIMITED,
            "Quota exhausted or throttled. Wait, raise the plan, or add a second key.",
        )
    if 500 <= status < 600:
        return Diagnosis(Verdict.SERVER_ERROR, "Provider-side failure. Retry later.")
    return Diagnosis(Verdict.UNEXPECTED, f"Unhandled status {status}.")


@dataclass
class ProbeResult:
    provider: str
    capability: str
    path: str
    status: int | None
    diagnosis: Diagnosis
    elapsed_ms: int
    top_level_keys: list[str]

    @property
    def ok(self) -> bool:
        return self.diagnosis.verdict.ok


def _shape(payload) -> list[str]:
    """Top-level keys, so a working response can be eyeballed without dumping it."""
    if isinstance(payload, dict):
        return list(payload)[:12]
    if isinstance(payload, list):
        return [f"list[{len(payload)}]"]
    return [type(payload).__name__]


def probe(
    client: httpx.Client,
    spec: ProviderSpec,
    capability: str,
    key: str,
    handle: str,
    shortcode: str,
    save_fixtures: bool = False,
) -> ProbeResult:
    endpoint = spec.endpoint(capability)
    url = f"https://{spec.host}{endpoint.path}"
    headers = {"x-rapidapi-key": key, "x-rapidapi-host": spec.host}

    try:
        response = client.get(
            url, params=endpoint.query(handle, shortcode), headers=headers, timeout=25.0
        )
    except httpx.HTTPError as exc:
        return ProbeResult(
            spec.key, capability, endpoint.path, None,
            Diagnosis(Verdict.TRANSPORT_ERROR, f"{type(exc).__name__}: {exc}"), 0, [],
        )

    diagnosis = diagnose(response.status_code, response.text)
    keys: list[str] = []

    if diagnosis.verdict.ok:
        try:
            payload = response.json()
        except ValueError:
            diagnosis = Diagnosis(
                Verdict.UNEXPECTED, "200 but the body is not JSON. Provider may be broken."
            )
        else:
            keys = _shape(payload)
            if save_fixtures:
                target = FIXTURE_DIR / spec.key / f"{capability}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
                )

    return ProbeResult(
        spec.key, capability, endpoint.path, response.status_code, diagnosis,
        int(response.elapsed.total_seconds() * 1000), keys,
    )


def run_probes(
    settings: Settings,
    handle: str = DEFAULT_HANDLE,
    shortcode: str = DEFAULT_SHORTCODE,
    save_fixtures: bool = False,
    providers: tuple[ProviderSpec, ...] = PROVIDERS,
) -> list[ProbeResult]:
    key = settings.rapidapi_keys[0]
    results: list[ProbeResult] = []
    with httpx.Client(follow_redirects=True) as client:
        for spec in providers:
            for endpoint in spec.endpoints:
                results.append(
                    probe(client, spec, endpoint.capability, key, handle, shortcode,
                          save_fixtures)
                )
    return results


def summarise(results: list[ProbeResult]) -> dict[str, list[ProbeResult]]:
    grouped: dict[str, list[ProbeResult]] = {}
    for r in results:
        grouped.setdefault(r.provider, []).append(r)
    return grouped


def is_usable(results: list[ProbeResult]) -> bool:
    """A provider is usable only if profile and tagged both work; the rest is optional."""
    working = {r.capability for r in results if r.ok}
    return {"profile", "tagged"} <= working


def provider_summary(results: list[ProbeResult]) -> str:
    """One line saying why a provider is unusable, judged across all its endpoints.

    Per-endpoint advice can mislead on its own. An unsubscribed account gets 403 on the
    first request and 429 on the rest, and telling someone to raise their plan when they
    have no subscription at all sends them the wrong way.
    """
    verdicts = {r.diagnosis.verdict for r in results}

    if Verdict.NO_SUCH_API in verdicts:
        return "This listing no longer exists on RapidAPI. Choose a different provider."
    if Verdict.AUTH_FAILED in verdicts:
        return "The API key was rejected. Check RAPIDAPI_KEYS in .env."
    if Verdict.NOT_SUBSCRIBED in verdicts:
        note = "No active subscription."
        if Verdict.RATE_LIMITED in verdicts:
            note += (
                " The 429s here are the gateway throttling unsubscribed requests, "
                "not a real quota limit."
            )
        return note
    if Verdict.RATE_LIMITED in verdicts:
        return "Quota exhausted or throttled. Wait, raise the plan, or add a second key."
    if Verdict.NO_SUCH_ENDPOINT in verdicts:
        return "Subscribed, but some paths are wrong. Correct them from the playground."
    if Verdict.TRANSPORT_ERROR in verdicts:
        return "Could not reach the host. Check network connectivity."
    return "Some endpoints did not answer."
