from app.models import Campaign, CampaignContact, Contact
from app.services import dialer


def _campaign_with_contacts(db, n=2):
    contacts = [Contact(name=f"C{i}", phone=f"+1555000{i:04d}") for i in range(n)]
    campaign = Campaign(name="Test", status="draft")
    db.add_all([*contacts, campaign])
    db.flush()
    for i, c in enumerate(contacts):
        db.add(CampaignContact(campaign_id=campaign.id, contact_id=c.id, position=i))
    db.commit()
    return campaign, contacts


def test_dialer_flow(db):
    campaign, _contacts = _campaign_with_contacts(db, 2)

    dialer.start_campaign(db, campaign.id)
    assert campaign.status == "running"

    first = dialer.next_pending_contact(db, campaign.id)
    assert first is not None

    dialer.mark_calling(db, campaign.id, first.contact_id)
    assert first.status == "calling"

    dialer.advance_after_call(db, campaign.id, first.contact_id, call_ok=True)
    assert first.status == "done"
    assert campaign.status == "running"  # one contact left

    second = dialer.next_pending_contact(db, campaign.id)
    dialer.mark_calling(db, campaign.id, second.contact_id)
    dialer.advance_after_call(db, campaign.id, second.contact_id, call_ok=False)
    assert second.status == "failed"
    assert campaign.status == "completed"  # queue drained


def test_start_requires_contacts(db):
    campaign = Campaign(name="Empty", status="draft")
    db.add(campaign)
    db.commit()
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        dialer.start_campaign(db, campaign.id)


def test_stop_and_requeue_calling(db):
    campaign, _contacts = _campaign_with_contacts(db, 1)
    dialer.start_campaign(db, campaign.id)
    cc = dialer.next_pending_contact(db, campaign.id)
    dialer.mark_calling(db, campaign.id, cc.contact_id)

    dialer.stop_campaign(db, campaign.id)
    assert campaign.status == "stopped"

    # restart re-queues the contact stuck in 'calling'
    dialer.start_campaign(db, campaign.id)
    assert cc.status == "pending"


def test_claim_next_contact_is_exclusive(db):
    """Sequential dialing: nothing else may be claimed while one call is live."""
    campaign, _contacts = _campaign_with_contacts(db, 2)
    dialer.start_campaign(db, campaign.id)

    first = dialer.claim_next_contact(db, campaign.id)
    assert first is not None
    assert dialer.claim_next_contact(db, campaign.id) is None

    dialer.advance_after_call(db, campaign.id, first[0], call_ok=True)
    second = dialer.claim_next_contact(db, campaign.id)
    assert second is not None and second[0] != first[0]


def test_claim_follows_position(db):
    campaign, contacts = _campaign_with_contacts(db, 3)
    dialer.start_campaign(db, campaign.id)

    claimed = []
    for _ in range(3):
        got = dialer.claim_next_contact(db, campaign.id)
        claimed.append(got[0])
        dialer.advance_after_call(db, campaign.id, got[0], call_ok=True)
    assert claimed == [c.id for c in contacts]


def test_claim_ignores_non_running_campaign(db):
    campaign, _ = _campaign_with_contacts(db, 1)
    assert dialer.claim_next_contact(db, campaign.id) is None  # still draft


def test_advance_is_idempotent(db):
    """A PSTN call can be reported terminal by more than one source."""
    campaign, _contacts = _campaign_with_contacts(db, 2)
    dialer.start_campaign(db, campaign.id)
    cid, _, _ = dialer.claim_next_contact(db, campaign.id)

    dialer.advance_after_call(db, campaign.id, cid, call_ok=True)
    # a second, contradictory report must not flip the result or skip a contact
    dialer.advance_after_call(db, campaign.id, cid, call_ok=False)

    cc = db.get(CampaignContact, (campaign.id, cid))
    assert cc.status == "done"
    assert campaign.status == "running"  # the other contact is still queued


def test_advance_ignores_contact_that_is_not_calling(db):
    campaign, _contacts = _campaign_with_contacts(db, 1)
    dialer.start_campaign(db, campaign.id)
    cc = dialer.next_pending_contact(db, campaign.id)

    dialer.advance_after_call(db, campaign.id, cc.contact_id, call_ok=True)
    assert cc.status == "pending"
    assert campaign.status == "running"


def test_campaign_start_stop_endpoints(client):
    cid = client.post("/api/contacts", json={"name": "A", "phone": "+15559990001"}).json()["id"]
    camp = client.post(
        "/api/campaigns", json={"name": "Endpoint test", "contact_ids": [cid]}
    ).json()

    started = client.post(f"/api/campaigns/{camp['id']}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "running"

    again = client.post(f"/api/campaigns/{camp['id']}/start")
    assert again.status_code == 409

    stopped = client.post(f"/api/campaigns/{camp['id']}/stop")
    assert stopped.json()["status"] == "stopped"


def test_start_requires_confirmation_in_real_mode(client, monkeypatch):
    from app.config import settings

    cid = client.post("/api/contacts", json={"name": "A", "phone": "+15559990002"}).json()["id"]
    camp = client.post("/api/campaigns", json={"name": "Real", "contact_ids": [cid]}).json()

    monkeypatch.setattr(settings, "dialer_mode", "twilio")
    monkeypatch.setattr(settings, "public_base_url", "https://example.ngrok.app")

    blocked = client.post(f"/api/campaigns/{camp['id']}/start")
    assert blocked.status_code == 409
    assert "real phone calls" in blocked.json()["detail"]

    ok = client.post(f"/api/campaigns/{camp['id']}/start", json={"confirm_real": True})
    assert ok.status_code == 200
    assert ok.json()["status"] == "running"
    assert ok.json()["dialing_mode"] == "twilio"


def test_campaign_exposes_simulated_mode_by_default(client):
    cid = client.post("/api/contacts", json={"name": "B", "phone": "+15559990003"}).json()["id"]
    camp = client.post("/api/campaigns", json={"name": "Sim", "contact_ids": [cid]}).json()
    assert camp["dialing_mode"] == "simulated"
    # no confirmation needed when nothing real can be dialed
    assert client.post(f"/api/campaigns/{camp['id']}/start").status_code == 200
