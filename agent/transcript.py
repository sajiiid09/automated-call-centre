"""Observer that turns pipeline frames into transcript turns.

Caller turns come from final ``TranscriptionFrame``s emitted by the STT
service. Agent turns are ``TTSTextFrame``s buffered until the bot stops
speaking, so interrupted speech is recorded only up to the interruption.
"""

from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService

OnTurn = Callable[[str, str], Awaitable[None]]  # (role, content)


class TranscriptObserver(BaseObserver):
    def __init__(self, on_turn: OnTurn):
        super().__init__()
        self._on_turn = on_turn
        self._agent_buffer: list[str] = []
        self._seen: set[int] = set()

    async def _flush_agent(self):
        if self._agent_buffer:
            text = " ".join(t.strip() for t in self._agent_buffer if t.strip())
            self._agent_buffer = []
            if text:
                await self._on_turn("agent", text)

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        # frames are observed once per hop; dedupe by frame id
        if frame.id in self._seen:
            return

        if isinstance(frame, TranscriptionFrame) and isinstance(data.source, STTService):
            self._seen.add(frame.id)
            if frame.text.strip():
                await self._on_turn("caller", frame.text.strip())
        elif isinstance(frame, TTSTextFrame) and isinstance(data.source, TTSService):
            self._seen.add(frame.id)
            self._agent_buffer.append(frame.text)
        elif isinstance(frame, (BotStoppedSpeakingFrame, EndFrame, CancelFrame)):
            self._seen.add(frame.id)
            await self._flush_agent()
