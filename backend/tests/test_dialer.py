from app.models import Campaign, CampaignContact, Contact
from app.services import dialer


def _campaign_with_contacts(db, n=2):
    contacts = [Contact(name=f"C{i}", phone=f"+1555000{i:04d}") for i in range(n)]
    campaign = Campaign(name="Test", status="draft")
    db.add_all([*contacts, campaign])
    db.flush()
    for c in contacts:
        db.add(CampaignContact(campaign_id=campaign.id, contact_id=c.id))
    db.commit()
    return campaign, contacts


def test_dialer_flow(db):
    campaign, contacts = _campaign_with_contacts(db, 2)

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
    campaign, contacts = _campaign_with_contacts(db, 1)
    dialer.start_campaign(db, campaign.id)
    cc = dialer.next_pending_contact(db, campaign.id)
    dialer.mark_calling(db, campaign.id, cc.contact_id)

    dialer.stop_campaign(db, campaign.id)
    assert campaign.status == "stopped"

    # restart re-queues the contact stuck in 'calling'
    dialer.start_campaign(db, campaign.id)
    assert cc.status == "pending"


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
