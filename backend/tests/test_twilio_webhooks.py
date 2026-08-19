from app.config import settings


def test_inbound_returns_stream_twiml(client, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://example.ngrok.app")
    resp = client.post(
        "/twilio/inbound",
        data={"From": "+447700900000", "To": "+447888862925", "CallSid": "CA123"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    body = resp.text
    assert "<Connect>" in body
    # no query string: Twilio refuses the handshake on a <Stream> URL that has
    # one, and the call drops with error 31920
    assert 'url="wss://example.ngrok.app/twilio/media"' in body
    assert "?" not in body.split("<Stream")[1].split(">")[0]
    assert '<Parameter name="direction" value="inbound" />' in body
    # real numbers must reach the call row instead of the literal "web-call"
    assert '<Parameter name="from_number" value="+447700900000" />' in body
    assert '<Parameter name="to_number" value="+447888862925" />' in body


def test_outbound_answer_carries_context(client, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://example.ngrok.app")
    resp = client.post(
        "/twilio/outbound-answer?call_id=11111111-1111-1111-1111-111111111111"
        "&contact_id=abc&campaign_id=def",
        data={},
    )
    body = resp.text
    assert '<Parameter name="direction" value="outbound" />' in body
    assert '<Parameter name="call_id" value="11111111-1111-1111-1111-111111111111" />' in body
    assert '<Parameter name="contact_id" value="abc" />' in body
    assert '<Parameter name="campaign_id" value="def" />' in body


def test_status_callback_marks_no_answer(client, db):
    from app.models import Call

    call = Call(direction="outbound", status="initiated", twilio_sid="CA999")
    db.add(call)
    db.commit()

    resp = client.post("/twilio/status", data={"CallSid": "CA999", "CallStatus": "no-answer"})
    assert resp.status_code == 204
    db.refresh(call)
    assert call.status == "no_answer"


def _running_campaign(db, n=2):
    from app.models import Campaign, CampaignContact, Contact

    contacts = [Contact(name=f"C{i}", phone=f"+1555111{i:04d}") for i in range(n)]
    campaign = Campaign(name="Dial", status="running")
    db.add_all([*contacts, campaign])
    db.flush()
    for i, c in enumerate(contacts):
        db.add(CampaignContact(campaign_id=campaign.id, contact_id=c.id, position=i))
    db.commit()
    return campaign, contacts


def test_status_callback_by_call_id_advances_campaign(client, db):
    """Regression: an unanswered call used to have no row at all.

    The row was only created when the media stream connected, so busy /
    no-answer / failed callbacks found nothing, the failure was dropped, and
    the contact sat in `calling` forever with nothing to advance it.
    """
    from app.models import Call, CampaignContact
    from app.services import dialer

    campaign, _contacts = _running_campaign(db, 2)
    claimed = dialer.claim_next_contact(db, campaign.id)
    assert claimed is not None

    # row as the dialer creates it at origination: no SID yet
    call = Call(
        direction="outbound",
        status="initiated",
        campaign_id=campaign.id,
        contact_id=claimed[0],
    )
    db.add(call)
    db.commit()

    resp = client.post(
        f"/twilio/status?call_id={call.id}",
        data={"CallSid": "CA-late", "CallStatus": "no-answer"},
    )
    assert resp.status_code == 204

    db.refresh(call)
    assert call.status == "no_answer"
    assert call.disposition == "failed"
    assert call.twilio_sid == "CA-late"

    cc = db.get(CampaignContact, (campaign.id, claimed[0]))
    assert cc.status == "failed"
    # the queue moved on instead of wedging
    assert dialer.next_pending_contact(db, campaign.id) is not None


def test_status_callback_completed_advances_once(client, db):
    from app.models import Call, CampaignContact
    from app.services import dialer

    campaign, _contacts = _running_campaign(db, 2)
    claimed = dialer.claim_next_contact(db, campaign.id)
    call = Call(
        direction="outbound",
        status="in_progress",
        campaign_id=campaign.id,
        contact_id=claimed[0],
    )
    db.add(call)
    db.commit()

    for _ in range(2):
        resp = client.post(
            f"/twilio/status?call_id={call.id}",
            data={"CallSid": "CA1", "CallStatus": "completed", "CallDuration": "42"},
        )
        assert resp.status_code == 204

    cc = db.get(CampaignContact, (campaign.id, claimed[0]))
    assert cc.status == "done"
    db.refresh(campaign)
    assert campaign.status == "running"  # second contact still queued, not skipped


def test_status_callback_ringing_does_not_advance(client, db):
    from app.models import Call, CampaignContact
    from app.services import dialer

    campaign, _contacts = _running_campaign(db, 1)
    claimed = dialer.claim_next_contact(db, campaign.id)
    call = Call(
        direction="outbound",
        status="initiated",
        campaign_id=campaign.id,
        contact_id=claimed[0],
    )
    db.add(call)
    db.commit()

    client.post(f"/twilio/status?call_id={call.id}", data={"CallStatus": "ringing"})
    db.refresh(call)
    assert call.status == "ringing"
    assert db.get(CampaignContact, (campaign.id, claimed[0])).status == "calling"


def test_status_callback_unknown_call_is_204(client):
    """Publicly reachable endpoint: garbage must not 500."""
    resp = client.post("/twilio/status?call_id=not-a-uuid", data={"CallStatus": "completed"})
    assert resp.status_code == 204


def _contact(client, phone: str) -> str:
    return client.post("/api/contacts", json={"name": "T", "phone": phone}).json()["id"]


def _real_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "dialer_mode", "twilio")
    monkeypatch.setattr(settings, "public_base_url", "https://example.ngrok.app")


# These three replace a single test that asserted 503 for the wrong reason: it
# relied on Twilio being unconfigured, but `.env` now has valid credentials, so
# it would have placed a real call the moment PUBLIC_BASE_URL was set. Each one
# now pins the *reason* for the refusal, and the conftest landmine proves no
# HTTP request was made.


def test_outbound_call_blocked_in_simulated_mode(client):
    cid = _contact(client, "+15557770001")
    resp = client.post("/api/calls/outbound", json={"contact_id": cid})
    assert resp.status_code == 503
    assert "simulated" in resp.json()["detail"]


def test_outbound_call_blocked_when_allowlist_empty(client, monkeypatch):
    _real_mode(monkeypatch)
    cid = _contact(client, "+15557770002")
    resp = client.post("/api/calls/outbound", json={"contact_id": cid})
    assert resp.status_code == 503
    assert "OUTBOUND_ALLOWLIST" in resp.json()["detail"]


def test_outbound_call_blocked_for_non_allowlisted_number(client, monkeypatch):
    _real_mode(monkeypatch)
    monkeypatch.setattr(settings, "outbound_allowlist", "+447700900123")
    cid = _contact(client, "+15557770003")
    resp = client.post("/api/calls/outbound", json={"contact_id": cid})
    assert resp.status_code == 503
    assert "not in OUTBOUND_ALLOWLIST" in resp.json()["detail"]
