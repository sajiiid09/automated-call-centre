"""Glue between a live voice pipeline and the database.

Owns the lifecycle of one call: creates the ``calls`` row, persists
transcript turns as they happen, and finalizes status/duration when the
pipeline ends. DB writes run in a thread so they never block the audio loop.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from loguru import logger

from agent.pipeline import CallConfig, build_task, default_transport_params, run_task
from agent.prompts import build_system_prompt, greeting_for
from app.config import settings
from app.db import SessionLocal
from app.models import Call, Campaign, Contact, TranscriptTurn


def _create_call_row(
    direction: str,
    contact_id: uuid.UUID | None,
    campaign_id: uuid.UUID | None,
) -> uuid.UUID:
    with SessionLocal() as db:
        call = Call(
            direction=direction,
            contact_id=contact_id,
            campaign_id=campaign_id,
            status="in_progress",
            started_at=datetime.now(timezone.utc),
            from_number="web-call",
            to_number="web-call",
        )
        db.add(call)
        db.commit()
        return call.id


def _save_turn(call_id: uuid.UUID, role: str, content: str) -> None:
    with SessionLocal() as db:
        db.add(TranscriptTurn(call_id=call_id, role=role, content=content))
        db.commit()


def _finalize_call(call_id: uuid.UUID, status: str) -> None:
    with SessionLocal() as db:
        call = db.get(Call, call_id)
        if call is None:
            return
        call.status = status
        call.ended_at = datetime.now(timezone.utc)
        if call.started_at is not None:
            call.duration_seconds = int((call.ended_at - call.started_at).total_seconds())
        db.commit()


def _load_call_context(
    contact_id: uuid.UUID | None, campaign_id: uuid.UUID | None
) -> tuple[str | None, str | None, str | None]:
    """Returns (contact_name, campaign_goal, campaign_script)."""
    with SessionLocal() as db:
        contact = db.get(Contact, contact_id) if contact_id else None
        campaign = db.get(Campaign, campaign_id) if campaign_id else None
        return (
            contact.name if contact else None,
            campaign.goal if campaign else None,
            campaign.script_prompt if campaign else None,
        )


class CallSession:
    """One live voice call bound to a DB call row."""

    def __init__(
        self,
        direction: str = "inbound",
        contact_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
    ):
        self.direction = direction
        self.contact_id = contact_id
        self.campaign_id = campaign_id
        self.call_id: uuid.UUID | None = None

    @property
    def is_campaign_call(self) -> bool:
        return self.campaign_id is not None and self.contact_id is not None

    async def start(self) -> uuid.UUID:
        self.call_id = await asyncio.to_thread(
            _create_call_row, self.direction, self.contact_id, self.campaign_id
        )
        if self.is_campaign_call:
            await asyncio.to_thread(self._mark_calling)
        return self.call_id

    def _mark_calling(self) -> None:
        from app.services import dialer

        with SessionLocal() as db:
            dialer.mark_calling(db, self.campaign_id, self.contact_id)

    def _advance_campaign(self, call_ok: bool) -> None:
        from app.services import dialer

        with SessionLocal() as db:
            dialer.advance_after_call(db, self.campaign_id, self.contact_id, call_ok)

    async def on_turn(self, role: str, content: str) -> None:
        if self.call_id is not None:
            await asyncio.to_thread(_save_turn, self.call_id, role, content)

    async def finish(self, status: str = "completed") -> None:
        if self.call_id is None:
            return
        await asyncio.to_thread(_finalize_call, self.call_id, status)
        if self.is_campaign_call:
            from app.services.disposition import classify_call

            await asyncio.to_thread(classify_call, self.call_id)
            await asyncio.to_thread(self._advance_campaign, status == "completed")

    async def build_config(self) -> CallConfig:
        contact_name, goal, script = await asyncio.to_thread(
            _load_call_context, self.contact_id, self.campaign_id
        )
        return CallConfig(
            system_prompt=build_system_prompt(
                direction=self.direction,
                contact_name=contact_name,
                goal=goal,
                script=script,
            ),
            greeting=greeting_for(self.direction, contact_name),
            deepgram_api_key=settings.deepgram_api_key,
            gemini_api_key=settings.gemini_api_key,
        )


async def run_call_pipeline(session: CallSession, task) -> None:
    """Run a built pipeline task to completion and finalize the call row."""
    try:
        await run_task(task)
        await session.finish("completed")
    except Exception:
        logger.exception(f"Voice pipeline failed for call {session.call_id}")
        await session.finish("failed")


def build_session_task(transport, config: CallConfig, session: CallSession):
    return build_task(transport, config, session.on_turn)
