import uuid
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.config import settings
from app.services import telephony

# Captured at import, before the autouse no_real_dialing fixture swaps it for a
# landmine. respx intercepts the transport, so no packet leaves the machine.
_REAL_POST = telephony._post_twilio


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setattr(settings, "dialer_mode", "twilio")
    monkeypatch.setattr(settings, "public_base_url", "https://example.ngrok.app")
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(settings, "twilio_auth_token", "token")
    monkeypatch.setattr(settings, "twilio_phone_number", "+447700900000")
    monkeypatch.setattr(settings, "outbound_allowlist", "+447700900123")
    monkeypatch.setattr(telephony, "_post_twilio", _REAL_POST)


# --- mode detection ---


def test_auto_mode_is_simulated_without_public_url(monkeypatch):
    monkeypatch.setattr(settings, "dialer_mode", "auto")
    monkeypatch.setattr(settings, "public_base_url", "")
    assert telephony.dialing_mode() == "simulated"


def test_auto_mode_rejects_http_base_url(monkeypatch):
    """http:// would be rewritten to an invalid stream URL and go silent."""
    monkeypatch.setattr(settings, "dialer_mode", "auto")
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(settings, "twilio_auth_token", "token")
    monkeypatch.setattr(settings, "twilio_phone_number", "+447700900000")
    monkeypatch.setattr(settings, "public_base_url", "http://example.ngrok.app")
    assert telephony.dialing_mode() == "simulated"


def test_auto_mode_is_twilio_when_fully_configured(monkeypatch):
    monkeypatch.setattr(settings, "dialer_mode", "auto")
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(settings, "twilio_auth_token", "token")
    monkeypatch.setattr(settings, "twilio_phone_number", "+447700900000")
    monkeypatch.setattr(settings, "public_base_url", "https://example.ngrok.app")
    assert telephony.dialing_mode() == "twilio"


def test_is_dialable_requires_allowlist_membership(monkeypatch, real_mode):
    assert telephony.is_dialable("+447700900123") is True
    assert telephony.is_dialable("+15550000000") is False


# --- origination payload ---


@respx.mock
async def test_originate_repeats_status_callback_event(real_mode):
    """Regression: a space-joined string only ever delivers `completed`."""
    route = respx.post("https://api.twilio.com/2010-04-01/Accounts/ACtest/Calls.json").mock(
        return_value=httpx.Response(201, json={"sid": "CA123"})
    )
    call_id = uuid.uuid4()
    sid = await telephony.originate_call("+447700900123", call_id)

    assert sid == "CA123"
    body = parse_qs(route.calls.last.request.content.decode())
    assert body["StatusCallbackEvent"] == ["initiated", "ringing", "answered", "completed"]


@respx.mock
async def test_originate_threads_call_id_through_both_urls(real_mode):
    """Webhooks must find our row without waiting for the SID to persist."""
    route = respx.post("https://api.twilio.com/2010-04-01/Accounts/ACtest/Calls.json").mock(
        return_value=httpx.Response(201, json={"sid": "CA123"})
    )
    call_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    await telephony.originate_call("+447700900123", call_id, campaign_id=campaign_id)

    body = parse_qs(route.calls.last.request.content.decode())
    assert f"call_id={call_id}" in body["Url"][0]
    assert "/twilio/outbound-answer?" in body["Url"][0]
    assert body["StatusCallback"][0].endswith(f"/twilio/status?call_id={call_id}")
    assert f"campaign_id={campaign_id}" in body["Url"][0]
    assert body["From"] == ["+447700900000"]
    assert body["To"] == ["+447700900123"]
    assert body["Timeout"] == [str(settings.dial_ring_timeout_seconds)]


@respx.mock
async def test_originate_raises_on_twilio_error(real_mode):
    respx.post("https://api.twilio.com/2010-04-01/Accounts/ACtest/Calls.json").mock(
        return_value=httpx.Response(400, text="unverified number")
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await telephony.originate_call("+447700900123", uuid.uuid4())
    assert exc.value.status_code == 502


# --- guardrails ---


async def test_assert_dialable_blocks_empty_allowlist(monkeypatch, real_mode):
    monkeypatch.setattr(settings, "outbound_allowlist", "")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await telephony.assert_dialable("+447700900123")
    assert "OUTBOUND_ALLOWLIST" in exc.value.detail


async def test_assert_dialable_blocks_in_simulated_mode():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await telephony.assert_dialable("+447700900123")
    assert "simulated" in exc.value.detail


async def test_assert_dialable_enforces_daily_cap(monkeypatch, real_mode):
    monkeypatch.setattr(settings, "max_outbound_calls_per_day", 2)
    monkeypatch.setattr(telephony, "_count_outbound_today", lambda: 2)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await telephony.assert_dialable("+447700900123")
    assert "Daily outbound cap" in exc.value.detail
