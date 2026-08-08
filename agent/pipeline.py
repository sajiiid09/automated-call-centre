"""Shared voice pipeline: transport-agnostic STT → LLM → TTS.

Works with any Pipecat transport (SmallWebRTC for browser calls now,
Twilio Media Streams later) — see ARCHITECTURE.md.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import Frame, LLMRunFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams

from agent.faq_gate import FaqGate, KnowledgeLookup
from agent.transcript import OnTurn, TranscriptObserver

GEMINI_MODEL = "gemini-3.5-flash-lite"
DEEPGRAM_TTS_VOICE = "aura-2-thalia-en"


@dataclass
class CallConfig:
    system_prompt: str
    greeting: str
    deepgram_api_key: str
    gemini_api_key: str


def default_transport_params() -> TransportParams:
    return TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(),
    )


def build_task(
    transport: BaseTransport,
    config: CallConfig,
    on_turn: OnTurn,
    *,
    knowledge: KnowledgeLookup | None = None,
) -> PipelineTask:
    stt = DeepgramSTTService(api_key=config.deepgram_api_key)
    tts = DeepgramTTSService(
        api_key=config.deepgram_api_key,
        settings=DeepgramTTSService.Settings(voice=DEEPGRAM_TTS_VOICE),
    )
    llm = GoogleLLMService(
        api_key=config.gemini_api_key,
        settings=GoogleLLMService.Settings(model=GEMINI_MODEL),
    )

    # System message only. The agent speaks first by being handed its greeting
    # as a TTSSpeakFrame on connect (see the transports' on_client_connected),
    # not by being prompted into improvising one.
    context = LLMContext(messages=[{"role": "system", "content": config.system_prompt}])
    aggregators = LLMContextAggregatorPair(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            # Downstream of where the caller's turn is committed to the context,
            # upstream of the only consumer of the resulting context frame.
            FaqGate(knowledge),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    return PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=False,
        ),
        observers=[TranscriptObserver(on_turn)],
    )


def greeting_frames(config: CallConfig) -> list[Frame]:
    """Frames that make the agent speak first, for on_client_connected.

    A TTSSpeakFrame is spoken verbatim, so the opening line is exactly what the
    operator wrote on /knowledge and costs no LLM round trip. It still lands in
    the LLM context as the assistant's first message (append_to_context) and in
    the transcript, so the conversation continues normally. Falls back to
    prompting the LLM only if there is no greeting to speak.

    Both transports must use this — if one greets and the other doesn't, that
    transport opens on silence.
    """
    if config.greeting and config.greeting.strip():
        return [TTSSpeakFrame(config.greeting.strip())]
    return [LLMRunFrame()]


async def run_task(
    task: PipelineTask,
    kickoff: Callable[[], Awaitable[None]] | None = None,
) -> None:
    runner = PipelineRunner(handle_sigint=False)
    if kickoff is not None:
        await kickoff()
    await runner.run(task)
