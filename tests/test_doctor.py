import httpx
import pytest
import respx

from clipping.doctor import (
    Diagnosis,
    Verdict,
    diagnose,
    is_usable,
    probe,
    provider_summary,
)
from clipping.providers import SOCIAL_API

# Bodies exactly as the RapidAPI gateway returns them; these strings are what the
# diagnosis logic keys off, so a change here means a change in real behaviour.
NOT_SUBSCRIBED = '{"message":"You are not subscribed to this API."}'
NO_SUCH_API = '{"message":"API doesn\'t exists"}'
NO_SUCH_ENDPOINT = '{"message":"Endpoint \'/v1/info\' does not exist"}'
TOO_MANY = '{"message":"Too many requests"}'


@pytest.mark.parametrize("status,body,expected", [
    (200, '{"data":{}}', Verdict.OK),
    (403, NOT_SUBSCRIBED, Verdict.NOT_SUBSCRIBED),
    (404, NO_SUCH_API, Verdict.NO_SUCH_API),
    (404, NO_SUCH_ENDPOINT, Verdict.NO_SUCH_ENDPOINT),
    (429, TOO_MANY, Verdict.RATE_LIMITED),
    (401, '{"message":"Invalid API key"}', Verdict.AUTH_FAILED),
    (503, "upstream unavailable", Verdict.SERVER_ERROR),
    (418, "teapot", Verdict.UNEXPECTED),
])
def test_diagnose_maps_gateway_responses(status, body, expected):
    assert diagnose(status, body).verdict is expected


def test_delisted_api_is_distinguished_from_unpaid_subscription():
    # Both are 40x with a JSON message, and they need opposite responses: one means pay,
    # the other means the provider is gone and the fallback must take over.
    assert diagnose(404, NO_SUCH_API).verdict is Verdict.NO_SUCH_API
    assert diagnose(403, NOT_SUBSCRIBED).verdict is Verdict.NOT_SUBSCRIBED


def test_failures_carry_actionable_advice():
    assert "Pricing tab" in diagnose(403, NOT_SUBSCRIBED).advice
    assert "different provider" in diagnose(404, NO_SUCH_API).advice
    assert diagnose(200, "{}").advice == ""


def test_endpoint_query_substitutes_placeholders():
    endpoint = SOCIAL_API.endpoint("profile")
    assert endpoint.query("iunik_official", "ABC") == {
        "username_or_id_or_url": "iunik_official"
    }
    assert SOCIAL_API.endpoint("post").query("x", "ABC123") == {"code_or_id_or_url": "ABC123"}


def test_every_capability_is_declared():
    assert {e.capability for e in SOCIAL_API.endpoints} == {
        "profile", "tagged", "posts", "post"
    }


# --- is_usable ---

class FakeResult:
    def __init__(self, capability, ok):
        self.capability = capability
        self.ok = ok


def test_provider_needs_both_profile_and_tagged_to_be_usable():
    assert is_usable([FakeResult("profile", True), FakeResult("tagged", True)])
    assert not is_usable([FakeResult("profile", True), FakeResult("tagged", False)])
    assert not is_usable([FakeResult("posts", True), FakeResult("post", True)])


# --- provider_summary ---

def row(verdict):
    r = FakeResult("profile", verdict is Verdict.OK)
    r.diagnosis = Diagnosis(verdict, "")
    return r


def test_unsubscribed_explains_that_sibling_429s_are_not_a_real_quota_limit():
    # An unsubscribed account gets 403 once then 429 on the rest. Telling someone to
    # raise their plan when they have no subscription would send them the wrong way.
    summary = provider_summary([row(Verdict.NOT_SUBSCRIBED), row(Verdict.RATE_LIMITED)])
    assert "No active subscription" in summary
    assert "not a real quota limit" in summary


def test_genuine_rate_limiting_still_advises_raising_the_plan():
    summary = provider_summary([row(Verdict.RATE_LIMITED), row(Verdict.RATE_LIMITED)])
    assert "raise the plan" in summary
    assert "subscription" not in summary


@pytest.mark.parametrize("verdict,expected", [
    (Verdict.NO_SUCH_API, "no longer exists"),
    (Verdict.AUTH_FAILED, "key was rejected"),
    (Verdict.NO_SUCH_ENDPOINT, "paths are wrong"),
    (Verdict.TRANSPORT_ERROR, "Could not reach"),
])
def test_provider_summary_covers_each_failure_mode(verdict, expected):
    assert expected in provider_summary([row(verdict)])


def test_delisting_outranks_everything_else():
    # If the listing is gone, subscribing is pointless; that message must win.
    summary = provider_summary([row(Verdict.NOT_SUBSCRIBED), row(Verdict.NO_SUCH_API)])
    assert "no longer exists" in summary


# --- probe, with the network mocked ---

@respx.mock
def test_probe_records_shape_on_success():
    respx.get(url__startswith=f"https://{SOCIAL_API.host}/v1/info").mock(
        return_value=httpx.Response(200, json={"data": {"username": "x"}, "status": "ok"})
    )
    with httpx.Client() as client:
        result = probe(client, SOCIAL_API, "profile", "fake_key", "someone", "ABC")

    assert result.ok
    assert result.top_level_keys == ["data", "status"]
    assert result.status == 200


@respx.mock
def test_probe_reports_unsubscribed_without_raising():
    respx.get(url__startswith=f"https://{SOCIAL_API.host}/v1/tagged").mock(
        return_value=httpx.Response(403, text=NOT_SUBSCRIBED)
    )
    with httpx.Client() as client:
        result = probe(client, SOCIAL_API, "tagged", "fake_key", "someone", "ABC")

    assert not result.ok
    assert result.diagnosis.verdict is Verdict.NOT_SUBSCRIBED


@respx.mock
def test_probe_flags_a_200_that_is_not_json():
    respx.get(url__startswith=f"https://{SOCIAL_API.host}/v1/info").mock(
        return_value=httpx.Response(200, text="<html>maintenance</html>")
    )
    with httpx.Client() as client:
        result = probe(client, SOCIAL_API, "profile", "fake_key", "someone", "ABC")

    assert not result.ok
    assert result.diagnosis.verdict is Verdict.UNEXPECTED


@respx.mock
def test_probe_survives_a_transport_failure():
    respx.get(url__startswith=f"https://{SOCIAL_API.host}/v1/info").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )
    with httpx.Client() as client:
        result = probe(client, SOCIAL_API, "profile", "fake_key", "someone", "ABC")

    assert result.diagnosis.verdict is Verdict.TRANSPORT_ERROR
    assert result.status is None


@respx.mock
def test_probe_sends_the_rapidapi_headers():
    route = respx.get(url__startswith=f"https://{SOCIAL_API.host}/v1/info").mock(
        return_value=httpx.Response(200, json={})
    )
    with httpx.Client() as client:
        probe(client, SOCIAL_API, "profile", "secret_key", "someone", "ABC")

    sent = route.calls.last.request
    assert sent.headers["x-rapidapi-key"] == "secret_key"
    assert sent.headers["x-rapidapi-host"] == SOCIAL_API.host
