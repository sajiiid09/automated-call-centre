"""CallSession: who gets classified, who advances a campaign, and what the
agent is told about the company."""

from datetime import datetime, timezone

from app.models import AgentProfile, Call, Campaign, CampaignContact, Contact
from app.services import call_session as call_session_module
from app.services.call_session import CallSession


def _finished_call(db, direction="inbound", campaign_id=None, contact_id=None) -> Call:
    call = Call(
        direction=direction,
        status="in_progress",
        campaign_id=campaign_id,
        contact_id=contact_id,
        started_at=datetime.now(timezone.utc),
    )
    db.add(call)
    db.commit()
    return call


def _capture(monkeypatch):
    """Replace the classify/advance seams and record what was called."""
    seen = {"classified": [], "advanced": []}

    import app.services.disposition as disposition_module

    monkeypatch.setattr(
        disposition_module, "classify_call", lambda call_id: seen["classified"].append(call_id)
    )
    monkeypatch.setattr(
        CallSession, "_advance_campaign", lambda self, ok: seen["advanced"].append(ok)
    )
    return seen


async def test_inbound_call_is_classified_without_touching_a_campaign(
    db, shared_session, monkeypatch
):
    """The regression this whole change exists to fix."""
    seen = _capture(monkeypatch)
    call = _finished_call(db, "inbound")

    session = CallSession(direction="inbound", call_id=call.id)
    await session.finish("completed")

    assert seen["classified"] == [call.id]
    assert seen["advanced"] == []


async def test_campaign_call_still_advances_the_queue(db, shared_session, monkeypatch):
    seen = _capture(monkeypatch)
    contact = Contact(name="Ada", phone="+15550001111")
    campaign = Campaign(name="Q3")
    db.add_all([contact, campaign])
    db.flush()
    db.add(CampaignContact(campaign_id=campaign.id, contact_id=contact.id))
    call = _finished_call(db, "outbound", campaign_id=campaign.id, contact_id=contact.id)

    session = CallSession(
        direction="outbound",
        call_id=call.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
    )
    await session.finish("completed")

    assert seen["classified"] == [call.id]
    assert seen["advanced"] == [True]


async def test_pstn_campaign_call_leaves_advancing_to_the_status_callback(
    db, shared_session, monkeypatch
):
    seen = _capture(monkeypatch)
    contact = Contact(name="Ada", phone="+15550001111")
    campaign = Campaign(name="Q3")
    db.add_all([contact, campaign])
    db.flush()
    call = _finished_call(db, "outbound", campaign_id=campaign.id, contact_id=contact.id)

    session = CallSession(
        direction="outbound",
        call_id=call.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        is_twilio=True,
    )
    await session.finish("completed")

    assert seen["classified"] == [call.id]
    assert seen["advanced"] == []


async def test_finish_without_a_call_row_is_a_no_op(db, shared_session, monkeypatch):
    seen = _capture(monkeypatch)
    await CallSession(direction="inbound").finish("failed")
    assert seen["classified"] == []


# --- config assembly ------------------------------------------------------


async def test_build_config_uses_the_agent_profile(db, shared_session):
    db.add(
        AgentProfile(
            id=1,
            company_name="Acme Utilities",
            greeting_template="Thanks for calling $company_name. What can I do?",
            persona="We supply water and electricity.",
        )
    )
    db.commit()

    config = await CallSession(direction="inbound").build_config()

    assert config.greeting == "Thanks for calling Acme Utilities. What can I do?"
    assert "Acme Utilities" in config.system_prompt
    assert "We supply water and electricity." in config.system_prompt
    # the knowledge rules only appear once the KB is wired in
    assert "[knowledge]" in config.system_prompt


async def test_build_config_falls_back_to_a_default_greeting(db, shared_session):
    db.add(AgentProfile(id=1, company_name="Acme Utilities", greeting_template=""))
    db.commit()

    config = await CallSession(direction="inbound").build_config()

    assert "Acme Utilities" in config.greeting
    assert "$" not in config.greeting


async def test_knowledge_lookup_is_inert_before_build_config(db, shared_session):
    """The gate must get a usable answer even if the profile never loaded."""
    knowledge = await CallSession(direction="inbound").knowledge_lookup("what are your hours")

    assert knowledge.faq_answer is None
    assert knowledge.chunks == []


async def test_greeting_frames_speak_the_greeting_verbatim(db, shared_session):
    from agent.pipeline import greeting_frames
    from pipecat.frames.frames import LLMRunFrame, TTSSpeakFrame

    db.add(
        AgentProfile(
            id=1,
            company_name="Acme Utilities",
            greeting_template="Thanks for calling $company_name.",
        )
    )
    db.commit()

    config = await CallSession(direction="inbound").build_config()
    frames = greeting_frames(config)

    assert len(frames) == 1
    assert isinstance(frames[0], TTSSpeakFrame)
    assert frames[0].text == "Thanks for calling Acme Utilities."

    # and with nothing to say, fall back to prompting the LLM rather than
    # opening the call on silence
    config.greeting = ""
    assert isinstance(greeting_frames(config)[0], LLMRunFrame)


def test_mark_call_failed_is_terminal(db, shared_session):
    call = _finished_call(db, "outbound")
    call_session_module.mark_call_failed(call.id, "Number not allowlisted")
    db.refresh(call)

    assert call.status == "failed"
    assert call.disposition == "failed"
    assert call.disposition_summary == "Number not allowlisted"
