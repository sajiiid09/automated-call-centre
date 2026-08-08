"""FAQ fast path and RAG injection, as a Pipecat frame processor.

Sits between the user context aggregator and the LLM. For each caller turn it
asks an injected callback what the knowledge base knows:

- **FAQ hit** — the stored answer is spoken verbatim and the ``LLMContextFrame``
  is *not* forwarded. ``GoogleLLMService`` runs inference on that frame and
  nothing else, so dropping it is a complete LLM bypass: the caller hears a
  deterministic answer without paying for a generation.
- **miss** — retrieved excerpts are injected into the shared ``LLMContext`` as
  a marked system message just before the caller's turn, and the frame is
  forwarded as usual.

Everything here is fail-open. Any error, timeout, or barge-in falls through to
the LLM; the audio pipeline must never stall waiting on the knowledge base.

This module imports only pipecat and the standard library. The lookup is passed
in as a callback so the ``agent`` package stays independent of the backend.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Prefix that marks the injected excerpts message. Matched on content rather
# than object identity, because the aggregators rebuild the message list.
RAG_MARKER = "[knowledge]"

RAG_PREAMBLE = (
    f"{RAG_MARKER} Excerpts from the company knowledge base. Use them only if "
    "they answer the caller's question; otherwise ignore them and say you will "
    "pass it to the team."
)

# Utterances that can never map to a useful FAQ. Looking these up would burn a
# round trip per backchannel.
_STOP_WORDS = {
    "yes",
    "yeah",
    "yep",
    "no",
    "nope",
    "ok",
    "okay",
    "sure",
    "right",
    "hello",
    "hi",
    "hey",
    "thanks",
    "thank you",
    "bye",
    "goodbye",
    "mhm",
    "mm-hmm",
    "uh-huh",
    "sorry",
    "what",
    "pardon",
    "please",
}
_MIN_WORDS = 2


@dataclass
class TurnKnowledge:
    """What the knowledge base found for one caller utterance."""

    faq_answer: str | None = None
    faq_id: str | None = None
    faq_score: float | None = None
    chunks: list[str] = field(default_factory=list)


KnowledgeLookup = Callable[[str], Awaitable[TurnKnowledge]]


def is_worth_looking_up(text: str) -> bool:
    """Cheap filter so backchannels don't cost a lookup."""
    cleaned = text.strip().strip(".!?,").lower()
    if not cleaned or cleaned in _STOP_WORDS:
        return False
    return len(cleaned.split()) >= _MIN_WORDS


def _role(message) -> str | None:
    """Role of a context message, or None for provider-specific ones.

    LLMContext can hold LLMSpecificMessage objects alongside plain dicts, and
    those have no .get(), so every read goes through here.
    """
    return message.get("role") if isinstance(message, dict) else None


def _is_rag(message) -> bool:
    if _role(message) != "system":
        return False
    content = message.get("content")
    return isinstance(content, str) and content.startswith(RAG_MARKER)


def last_user_text(context) -> str:
    """Text of the most recent user message in an LLMContext."""
    for message in reversed(list(context.get_messages())):
        if _role(message) != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        # multimodal content is a list of parts; keep the text ones
        if isinstance(content, list):
            return " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
    return ""


def strip_rag(context) -> None:
    """Drop the previous turn's excerpts so context doesn't grow every turn."""
    context.set_messages([m for m in list(context.get_messages()) if not _is_rag(m)])


def inject_rag(context, chunks: list[str]) -> None:
    """Replace the excerpts message, positioned just before the caller's turn.

    Mid-list system messages are safe for Gemini: only ``messages[0]`` becomes
    ``system_instruction``, later ones are converted to user content.
    """
    # get_messages() hands back the live list, so copy before mutating.
    messages = [m for m in list(context.get_messages()) if not _is_rag(m)]
    if not chunks:
        context.set_messages(messages)
        return

    insert_at = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        if _role(messages[i]) == "user":
            insert_at = i
            break

    body = RAG_PREAMBLE + "\n\n---\n" + "\n---\n".join(chunks)
    messages.insert(insert_at, {"role": "system", "content": body})
    context.set_messages(messages)


class FaqGate(FrameProcessor):
    """Answer known questions without the LLM; ground the rest with excerpts."""

    def __init__(self, lookup: KnowledgeLookup | None = None, **kwargs):
        super().__init__(**kwargs)
        self._lookup = lookup
        # Bumped whenever the caller starts speaking. A lookup that resolves
        # against a stale epoch belongs to a turn that no longer exists, so its
        # answer is dropped rather than spoken over the new one.
        self._epoch = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, (InterruptionFrame, UserStartedSpeakingFrame)):
            self._epoch += 1

        if not (isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM):
            await self.push_frame(frame, direction)
            return

        if self._lookup is None:
            await self.push_frame(frame, direction)
            return

        text = last_user_text(frame.context)
        if not is_worth_looking_up(text):
            await self.push_frame(frame, direction)
            return

        epoch = self._epoch
        try:
            knowledge = await self._lookup(text)
        except Exception:
            logger.exception("FaqGate: knowledge lookup failed, falling through to the LLM")
            await self.push_frame(frame, direction)
            return

        if epoch != self._epoch:
            logger.debug("FaqGate: caller barged in during lookup, dropping the result")
            await self.push_frame(frame, direction)
            return

        if knowledge.faq_answer:
            logger.info(
                f"FaqGate: hit faq={knowledge.faq_id} score={knowledge.faq_score:.3f} "
                f"— bypassing the LLM"
            )
            strip_rag(frame.context)
            # TTSSpeakFrame defaults append_to_context=True, so the spoken answer
            # still lands in LLMContext as an assistant message and the follow-up
            # turn has it in history. Not forwarding the context frame is what
            # suppresses the generation.
            await self.push_frame(TTSSpeakFrame(knowledge.faq_answer), direction)
            return

        inject_rag(frame.context, knowledge.chunks)
        await self.push_frame(frame, direction)
