from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models import Call, Campaign, CampaignContact, Contact
from app.services import campaign_runner, dialer, telephony


@pytest.fixture
def dialable(monkeypatch):
    """Real mode with every guardrail satisfied, but no network."""
    monkeypatch.setattr(settings, "dialer_mode", "twilio")
    monkeypatch.setattr(settings, "public_base_url", "https://example.ngrok.app")
    monkeypatch.setattr(settings, "twilio_phone_number", "+447700900000")
    monkeypatch.setattr(telephony, "_count_outbound_today", lambda: 0)


def _campaign(db, n=2, status="running", phones=None):
    contacts = [
        Contact(name=f"C{i}", phone=(phones[i] if phones else f"+1555222{i:04d}")) for i in range(n)
    ]
    campaign = Campaign(name="Runner", status=status)
    db.add_all([*contacts, campaign])
    db.flush()
    for i, c in enumerate(contacts):
        db.add(CampaignContact(campaign_id=campaign.id, contact_id=c.id, position=i))
    db.commit()
    return campaign, contacts


def _record_originations(monkeypatch):
    calls = []

    async def fake(phone, call_id, contact_id=None, campaign_id=None):
        calls.append((phone, call_id, contact_id, campaign_id))
        return f"CA{len(calls)}"

    monkeypatch.setattr(telephony, "originate_call", fake)
    return calls


async def test_tick_originates_exactly_one_call(db, shared_session, monkeypatch, dialable):
    campaign, _contacts = _campaign(db, 2, phones=["+447700900123", "+447700900124"])
    monkeypatch.setattr(settings, "outbound_allowlist", "+447700900123,+447700900124")
    placed = _record_originations(monkeypatch)

    await campaign_runner.dial_next_for_campaign(campaign.id)

    assert len(placed) == 1
    assert placed[0][0] == "+447700900123"  # position order

    db.expire_all()
    rows = db.query(CampaignContact).filter_by(campaign_id=campaign.id).all()
    assert sorted(r.status for r in rows) == ["calling", "pending"]

    call = db.query(Call).filter_by(campaign_id=campaign.id).one()
    assert call.status == "initiated"
    assert call.twilio_sid == "CA1"
    assert call.to_number == "+447700900123"
    assert call.from_number == "+447700900000"


async def test_tick_skips_campaign_with_call_in_flight(db, shared_session, monkeypatch, dialable):
    campaign, _contacts = _campaign(db, 2, phones=["+447700900123", "+447700900124"])
    monkeypatch.setattr(settings, "outbound_allowlist", "+447700900123,+447700900124")
    placed = _record_originations(monkeypatch)

    await campaign_runner.dial_next_for_campaign(campaign.id)
    await campaign_runner.dial_next_for_campaign(campaign.id)

    assert len(placed) == 1  # sequential dialing holds


async def test_tick_advances_when_origination_raises(db, shared_session, monkeypatch, dialable):
    """A trial account rejecting an unverified number must not wedge the queue."""
    campaign, contacts = _campaign(db, 2, phones=["+447700900123", "+447700900124"])
    monkeypatch.setattr(settings, "outbound_allowlist", "+447700900123,+447700900124")

    async def boom(*args, **kwargs):
        raise RuntimeError("Twilio 400: unverified number")

    monkeypatch.setattr(telephony, "originate_call", boom)

    await campaign_runner.dial_next_for_campaign(campaign.id)

    db.expire_all()
    cc = db.get(CampaignContact, (campaign.id, contacts[0].id))
    assert cc.status == "failed"
    call = db.query(Call).filter_by(contact_id=contacts[0].id).one()
    assert call.status == "failed"
    # the next contact is reachable rather than stranded behind the failure
    assert dialer.next_pending_contact(db, campaign.id) is not None


async def test_non_allowlisted_contact_fails_instead_of_wedging(
    db, shared_session, monkeypatch, dialable
):
    campaign, contacts = _campaign(db, 1, phones=["+15550009999"])
    monkeypatch.setattr(settings, "outbound_allowlist", "+447700900123")
    placed = _record_originations(monkeypatch)

    await campaign_runner.dial_next_for_campaign(campaign.id)

    assert placed == []
    db.expire_all()
    cc = db.get(CampaignContact, (campaign.id, contacts[0].id))
    assert cc.status == "failed"
    db.refresh(campaign)
    assert campaign.status == "completed"  # queue drained, campaign not stuck


async def test_tick_ignores_non_running_campaign(db, shared_session, monkeypatch, dialable):
    campaign, _ = _campaign(db, 1, status="draft", phones=["+447700900123"])
    monkeypatch.setattr(settings, "outbound_allowlist", "+447700900123")
    placed = _record_originations(monkeypatch)

    await campaign_runner.dial_next_for_campaign(campaign.id)
    assert placed == []


async def test_reap_stale_calling_contact(db, shared_session, monkeypatch, dialable):
    """A process restart kills the media stream; the row must not hang."""
    campaign, _contacts = _campaign(db, 1, phones=["+447700900123"])
    monkeypatch.setattr(settings, "outbound_allowlist", "+447700900123")
    claimed = dialer.claim_next_contact(db, campaign.id)

    stale = Call(
        direction="outbound",
        status="in_progress",
        campaign_id=campaign.id,
        contact_id=claimed[0],
        started_at=datetime.now(timezone.utc)
        - timedelta(seconds=settings.dial_stale_call_seconds + 60),
    )
    db.add(stale)
    db.commit()

    await campaign_runner.dial_next_for_campaign(campaign.id)

    db.expire_all()
    cc = db.get(CampaignContact, (campaign.id, claimed[0]))
    assert cc.status == "failed"
    db.refresh(stale)
    assert stale.status == "failed"


def test_supervisor_does_not_run_in_simulated_mode():
    assert campaign_runner.should_run() is False
