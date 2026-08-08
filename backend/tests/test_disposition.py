"""Direction-branched disposition labelling."""

import json
import types
import uuid
from datetime import datetime, timezone

import pytest

from app.models import Call, TranscriptTurn
from app.services import disposition
from app.services.disposition import (
    INBOUND_DISPOSITIONS,
    OUTBOUND_DISPOSITIONS,
    classify_call,
    vocabulary_for,
)


def _call(db, direction: str, *, turns: bool = True) -> Call:
    call = Call(
        direction=direction,
        status="completed",
        started_at=datetime.now(timezone.utc),
        from_number="+15550001111",
        to_number="+15550002222",
    )
    db.add(call)
    db.flush()
    if turns:
        db.add(TranscriptTurn(call_id=call.id, role="agent", content="How can I help?"))
        db.add(TranscriptTurn(call_id=call.id, role="caller", content="What are your hours?"))
    db.commit()
    return call


def _fake_gemini(monkeypatch, payload: dict) -> list[str]:
    """Replace the genai client and capture the prompt it was given."""
    seen: list[str] = []

    class _Models:
        def generate_content(self, *, model, contents, config):
            seen.append(contents)
            return types.SimpleNamespace(text=json.dumps(payload))

    class _Client:
        def __init__(self, **kwargs):
            self.models = _Models()

    monkeypatch.setattr(disposition.genai, "Client", _Client)
    return seen


def test_vocabulary_is_chosen_by_direction():
    assert vocabulary_for("outbound")[0] == OUTBOUND_DISPOSITIONS
    assert vocabulary_for("inbound")[0] == INBOUND_DISPOSITIONS
    # anything unrecognised is treated as inbound, the CX default
    assert vocabulary_for("")[0] == INBOUND_DISPOSITIONS


def test_inbound_call_gets_a_cx_label(db, shared_session, monkeypatch):
    prompts = _fake_gemini(monkeypatch, {"disposition": "resolved", "summary": "Hours given."})
    call = _call(db, "inbound")

    classify_call(call.id)
    db.refresh(call)

    assert call.disposition == "resolved"
    assert call.disposition_summary == "Hours given."
    assert "inbound customer-service" in prompts[0]


def test_outbound_call_keeps_the_sales_vocabulary(db, shared_session, monkeypatch):
    prompts = _fake_gemini(monkeypatch, {"disposition": "interested", "summary": "Wants a demo."})
    call = _call(db, "outbound")

    classify_call(call.id)
    db.refresh(call)

    assert call.disposition == "interested"
    assert "sales/support" in prompts[0]


def test_a_label_from_the_wrong_vocabulary_is_rejected(db, shared_session, monkeypatch):
    """An outbound label on an inbound call is a model error, not a result."""
    _fake_gemini(monkeypatch, {"disposition": "interested", "summary": "..."})
    call = _call(db, "inbound")

    classify_call(call.id)
    db.refresh(call)

    assert call.disposition is None
    assert call.disposition_summary is None


@pytest.mark.parametrize(
    ("direction", "expected"), [("inbound", "abandoned"), ("outbound", "failed")]
)
def test_empty_transcript_short_circuits(db, shared_session, monkeypatch, direction, expected):
    prompts = _fake_gemini(monkeypatch, {"disposition": "resolved", "summary": "..."})
    call = _call(db, direction, turns=False)

    classify_call(call.id)
    db.refresh(call)

    assert call.disposition == expected
    assert prompts == []  # no API call spent to say "nothing happened"


def test_missing_call_is_a_no_op(db, shared_session, monkeypatch):
    _fake_gemini(monkeypatch, {"disposition": "resolved", "summary": "..."})
    classify_call(uuid.uuid4())  # must not raise
