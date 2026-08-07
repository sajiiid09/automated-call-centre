"""Background supervisor that places real campaign calls.

The only server-initiated actor in the backend. It polls running campaigns,
claims one contact at a time, and originates a PSTN call. Everything else in
the system reacts to an inbound request; this does not.

Design notes:
- Polling, not event chaining. A dropped webhook, an ngrok flap, or a process
  restart must not strand a campaign, and nothing else would repair it.
- One call in flight per campaign, enforced by `dialer.claim_next_contact`.
- Every DB touch runs in a thread with its own session, so the audio event
  loop is never blocked and no request-scoped session leaks in here.
- Single worker only. The claim is safe under concurrency, but N supervisors
  polling is wasteful and the stale reap can race with itself.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Call, Campaign, CampaignContact
from app.services import call_session, dialer, telephony

_tasks: set[asyncio.Task] = set()


def _running_campaign_ids() -> list[uuid.UUID]:
    with SessionLocal() as db:
        return list(db.scalars(select(Campaign.id).where(Campaign.status == "running")))


def _reap_stale(campaign_id: uuid.UUID) -> None:
    """Release contacts whose call is over, or that never got anywhere.

    This is what makes a mid-campaign restart self-healing: the media stream
    died with the process, leaving a row at `in_progress` with no live leg.
    After the timeout it is force-failed and the queue resumes on its own.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.dial_stale_call_seconds)
    with SessionLocal() as db:
        stuck = db.scalars(
            select(CampaignContact).where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.status == "calling",
            )
        ).all()
        for cc in stuck:
            call = db.scalars(
                select(Call)
                .where(Call.campaign_id == campaign_id, Call.contact_id == cc.contact_id)
                .order_by(Call.started_at.desc().nulls_last())
                .limit(1)
            ).first()
            if call is None:
                continue
            terminal = call.status in telephony.TERMINAL_CALL_STATUSES
            expired = call.started_at is not None and call.started_at < cutoff
            if terminal or expired:
                if expired and not terminal:
                    logger.warning(f"Reaping stale call {call.id} on campaign {campaign_id}")
                    call.status = "failed"
                    call.disposition = call.disposition or "failed"
                    call.disposition_summary = (
                        call.disposition_summary or "Call abandoned (no result reported)"
                    )
                    db.commit()
                dialer.advance_after_call(db, campaign_id, cc.contact_id, terminal and not expired)


def _claim(campaign_id: uuid.UUID):
    with SessionLocal() as db:
        return dialer.claim_next_contact(db, campaign_id)


def _release(campaign_id: uuid.UUID, contact_id: uuid.UUID, summary: str) -> None:
    """Give up on a contact we claimed but could not call."""
    with SessionLocal() as db:
        dialer.advance_after_call(db, campaign_id, contact_id, False)
    logger.warning(f"Campaign {campaign_id}: skipped contact {contact_id} — {summary}")


async def dial_next_for_campaign(campaign_id: uuid.UUID) -> bool:
    """Place at most one call. Returns True if a call was originated."""
    await asyncio.to_thread(_reap_stale, campaign_id)

    claimed = await asyncio.to_thread(_claim, campaign_id)
    if claimed is None:
        return False
    contact_id, name, phone = claimed

    try:
        await telephony.assert_dialable(phone)
    except Exception as exc:
        # Blocked by a guardrail (allowlist, daily cap). Fail the contact
        # rather than skipping it, or the campaign never completes.
        detail = getattr(exc, "detail", str(exc))
        call_id = await asyncio.to_thread(
            call_session.create_outbound_call_row,
            contact_id,
            campaign_id,
            settings.twilio_phone_number,
            phone,
        )
        await asyncio.to_thread(call_session.mark_call_failed, call_id, str(detail))
        await asyncio.to_thread(_release, campaign_id, contact_id, str(detail))
        return False

    call_id = await asyncio.to_thread(
        call_session.create_outbound_call_row,
        contact_id,
        campaign_id,
        settings.twilio_phone_number,
        phone,
    )
    try:
        sid = await telephony.originate_call(phone, call_id, contact_id, campaign_id)
    except Exception as exc:
        # A single Twilio 4xx (unverified number on trial is the common one)
        # must not wedge the whole campaign.
        logger.exception(f"Origination failed for {name} <{phone}>")
        await asyncio.to_thread(
            call_session.mark_call_failed, call_id, f"Origination failed: {exc}"
        )
        await asyncio.to_thread(_release, campaign_id, contact_id, "origination failed")
        return False

    await asyncio.to_thread(call_session.attach_twilio_sid, call_id, sid)
    logger.info(f"Campaign {campaign_id}: dialing {name} <{phone}> sid={sid}")
    return True


async def tick() -> None:
    for campaign_id in await asyncio.to_thread(_running_campaign_ids):
        await dial_next_for_campaign(campaign_id)


async def run_forever() -> None:
    logger.info("Campaign dial supervisor started")
    while True:
        await asyncio.sleep(settings.dial_poll_seconds)
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            # one bad campaign must never kill the loop
            logger.exception("Campaign dial supervisor tick failed")


def should_run() -> bool:
    return settings.dialer_supervisor_enabled and telephony.dialing_mode() == "twilio"


def start() -> None:
    """Spawn the supervisor, keeping a strong ref so it is not GC'd."""
    if not should_run():
        logger.info("Campaign dial supervisor not started (simulated mode)")
        return
    task = asyncio.create_task(run_forever())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def stop() -> None:
    for task in list(_tasks):
        task.cancel()
    for task in list(_tasks):
        try:
            await task
        except asyncio.CancelledError:
            pass
