"""System prompts for the voice agent. Output goes through TTS, so prompts
insist on short, speakable, plain-text replies."""

VOICE_STYLE = """\
You are a friendly, professional AI phone agent for the company.
You are on a live voice call. Rules:
- Keep replies short: one to three sentences. Never monologue.
- Plain spoken language only: no markdown, no bullet points, no emojis,
  no stage directions.
- Ask one question at a time and wait for the answer.
- If the caller is silent or unclear, politely ask them to repeat.
- If asked something you don't know, say you will pass it to the team.
- End the call politely when the conversation is done.
"""

INBOUND_ROLE = """\
This is an INBOUND call: the caller phoned us. Greet them, find out how you
can help, answer questions about the company, and capture anything that
needs follow-up (name, need, callback preference).
"""

OUTBOUND_ROLE = """\
This is an OUTBOUND call: you are calling {contact_name} on behalf of the
company. Introduce yourself and the reason for the call right away, be
respectful of their time, and if they are not interested, thank them and
end the call gracefully.

Campaign goal: {goal}

Campaign instructions:
{script}
"""


def build_system_prompt(
    direction: str = "inbound",
    contact_name: str | None = None,
    goal: str | None = None,
    script: str | None = None,
) -> str:
    prompt = VOICE_STYLE
    if direction == "outbound":
        prompt += "\n" + OUTBOUND_ROLE.format(
            contact_name=contact_name or "the contact",
            goal=goal or "have a helpful conversation",
            script=script or "No extra instructions.",
        )
    else:
        prompt += "\n" + INBOUND_ROLE
    return prompt


def greeting_for(direction: str, contact_name: str | None = None) -> str:
    if direction == "outbound":
        who = f" Am I speaking with {contact_name}?" if contact_name else ""
        return f"Hello! This is the AI assistant calling from our company.{who}"
    return "Hello! Thanks for calling. How can I help you today?"
