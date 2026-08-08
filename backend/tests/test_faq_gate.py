"""FaqGate: does a knowledge-base hit really bypass the LLM?

These drive the processor directly — no audio, no Deepgram, no Gemini. The
central assertion is that on a hit the LLMContextFrame is *not* forwarded,
because that frame is the only thing GoogleLLMService runs inference on.
"""

import asyncio

import pytest
from agent.faq_gate import RAG_MARKER, FaqGate, TurnKnowledge, is_worth_looking_up
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    StartFrame,
    TTSSpeakFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from pipecat.utils.asyncio.task_manager import TaskManager


class _Recorder(FrameProcessor):
    """Terminal processor that keeps every frame it is handed."""

    def __init__(self):
        super().__init__(enable_direct_mode=True)
        self.frames: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self.frames.append(frame)

    def of(self, cls) -> list[Frame]:
        return [f for f in self.frames if isinstance(f, cls)]


async def _make_gate(lookup):
    """A started gate linked to a recorder, ready for process_frame.

    Direct mode keeps frames on the calling coroutine instead of a per-processor
    queue, so assertions can run straight after the await.
    """
    task_manager = TaskManager(loop=asyncio.get_running_loop())
    setup = FrameProcessorSetup(
        clock=SystemClock(), task_manager=task_manager, pipeline_worker=None
    )

    gate = FaqGate(lookup, enable_direct_mode=True)
    recorder = _Recorder()
    gate.link(recorder)
    await gate.setup(setup)
    await recorder.setup(setup)

    # StartFrame flips the internal started flag; without it push_frame drops.
    await gate.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
    recorder.frames.clear()
    return gate, recorder


def _context(user_text: str) -> LLMContext:
    return LLMContext(
        messages=[
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": user_text},
        ]
    )


async def _push(gate, context) -> None:
    await gate.process_frame(LLMContextFrame(context=context), FrameDirection.DOWNSTREAM)


# --- the bypass ---------------------------------------------------------


async def test_faq_hit_speaks_answer_and_bypasses_llm():
    async def lookup(_text):
        return TurnKnowledge(faq_answer="We are open nine to five.", faq_id="x", faq_score=0.93)

    gate, recorder = await _make_gate(lookup)
    await _push(gate, _context("what are your opening hours"))

    spoken = recorder.of(TTSSpeakFrame)
    assert len(spoken) == 1
    assert spoken[0].text == "We are open nine to five."
    # the whole point: the LLM never sees a context frame for this turn
    assert recorder.of(LLMContextFrame) == []


async def test_faq_answer_is_appended_to_context():
    """append_to_context keeps the canned answer in history for follow-ups."""

    async def lookup(_text):
        return TurnKnowledge(faq_answer="Nine to five.", faq_id="x", faq_score=0.9)

    gate, recorder = await _make_gate(lookup)
    await _push(gate, _context("opening hours please"))

    assert recorder.of(TTSSpeakFrame)[0].append_to_context is True


# --- the miss path ------------------------------------------------------


async def test_miss_injects_knowledge_before_the_user_turn():
    async def lookup(_text):
        return TurnKnowledge(chunks=["Refunds take 14 days.", "Custom orders are final sale."])

    gate, recorder = await _make_gate(lookup)
    context = _context("do you refund custom orders")
    await _push(gate, context)

    assert len(recorder.of(LLMContextFrame)) == 1
    assert recorder.of(TTSSpeakFrame) == []

    messages = context.get_messages()
    rag = [m for m in messages if str(m.get("content", "")).startswith(RAG_MARKER)]
    assert len(rag) == 1
    assert "Refunds take 14 days." in rag[0]["content"]
    # positioned immediately before the caller's turn
    assert messages.index(rag[0]) == len(messages) - 2
    assert messages[-1]["role"] == "user"


async def test_knowledge_message_does_not_accumulate_across_turns():
    chunks = iter([["first turn fact"], ["second turn fact"], ["third turn fact"]])

    async def lookup(_text):
        return TurnKnowledge(chunks=next(chunks))

    gate, _ = await _make_gate(lookup)
    context = _context("first question")

    await _push(gate, context)
    after_one = len(context.get_messages())

    context.add_message({"role": "user", "content": "second question"})
    await _push(gate, context)
    context.add_message({"role": "user", "content": "third question"})
    await _push(gate, context)

    rag = [m for m in context.get_messages() if str(m.get("content", "")).startswith(RAG_MARKER)]
    assert len(rag) == 1
    assert "third turn fact" in rag[0]["content"]
    # one user message added per turn, and exactly one knowledge message total
    assert len(context.get_messages()) == after_one + 2


async def test_hit_after_miss_strips_the_stale_knowledge_message():
    results = iter(
        [
            TurnKnowledge(chunks=["some excerpt"]),
            TurnKnowledge(faq_answer="Nine to five.", faq_id="x", faq_score=0.95),
        ]
    )

    async def lookup(_text):
        return next(results)

    gate, _ = await _make_gate(lookup)
    context = _context("first question")
    await _push(gate, context)
    context.add_message({"role": "user", "content": "opening hours"})
    await _push(gate, context)

    assert not [
        m for m in context.get_messages() if str(m.get("content", "")).startswith(RAG_MARKER)
    ]


# --- fail-open ----------------------------------------------------------


async def test_lookup_exception_falls_through_to_the_llm():
    async def lookup(_text):
        raise RuntimeError("embeddings API is down")

    gate, recorder = await _make_gate(lookup)
    await _push(gate, _context("what are your opening hours"))

    assert len(recorder.of(LLMContextFrame)) == 1
    assert recorder.of(TTSSpeakFrame) == []


async def test_no_lookup_configured_is_a_passthrough():
    gate, recorder = await _make_gate(None)
    await _push(gate, _context("what are your opening hours"))

    assert len(recorder.of(LLMContextFrame)) == 1


@pytest.mark.parametrize("utterance", ["yes", "ok", "thanks", "mm-hmm", "hi", "", "what?"])
async def test_backchannels_never_reach_the_lookup(utterance):
    called = False

    async def lookup(_text):
        nonlocal called
        called = True
        return TurnKnowledge()

    gate, recorder = await _make_gate(lookup)
    await _push(gate, _context(utterance))

    assert called is False
    assert len(recorder.of(LLMContextFrame)) == 1


def test_is_worth_looking_up():
    assert is_worth_looking_up("what are your opening hours")
    assert not is_worth_looking_up("  yes.  ")
    assert not is_worth_looking_up("ok")
    assert not is_worth_looking_up("")


# --- barge-in -----------------------------------------------------------


async def test_barge_in_during_lookup_drops_the_stale_answer():
    started = asyncio.Event()
    release = asyncio.Event()

    async def lookup(_text):
        started.set()
        await release.wait()
        return TurnKnowledge(faq_answer="Nine to five.", faq_id="x", faq_score=0.99)

    gate, recorder = await _make_gate(lookup)
    pushing = asyncio.create_task(
        gate.process_frame(
            LLMContextFrame(context=_context("what are your hours")), FrameDirection.DOWNSTREAM
        )
    )

    await started.wait()
    await gate.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    release.set()
    await pushing

    # the caller moved on: answer the new turn with the LLM, don't speak the old one
    assert recorder.of(TTSSpeakFrame) == []
    assert len(recorder.of(LLMContextFrame)) == 1
