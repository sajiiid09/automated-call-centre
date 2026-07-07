from app.config import settings


def test_inbound_returns_stream_twiml(client, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://example.ngrok.app")
    resp = client.post("/twilio/inbound", data={"From": "+447700900000", "CallSid": "CA123"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    body = resp.text
    assert "<Connect>" in body
    assert 'url="wss://example.ngrok.app/twilio/media?direction=inbound"' in body


def test_outbound_answer_carries_context(client, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://example.ngrok.app")
    resp = client.post(
        "/twilio/outbound-answer?contact_id=abc&campaign_id=def",
        data={},
    )
    body = resp.text
    assert "direction=outbound" in body
    assert "contact_id=abc" in body
    assert "campaign_id=def" in body


def test_status_callback_marks_no_answer(client, db):
    from app.models import Call

    call = Call(direction="outbound", status="initiated", twilio_sid="CA999")
    db.add(call)
    db.commit()

    resp = client.post("/twilio/status", data={"CallSid": "CA999", "CallStatus": "no-answer"})
    assert resp.status_code == 204
    db.refresh(call)
    assert call.status == "no_answer"


def test_outbound_call_requires_twilio(client):
    cid = client.post("/api/contacts", json={"name": "T", "phone": "+15557770001"}).json()["id"]
    resp = client.post("/api/calls/outbound", json={"contact_id": cid})
    assert resp.status_code == 503  # Twilio not configured in tests
