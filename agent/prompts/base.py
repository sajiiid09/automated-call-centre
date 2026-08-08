"""System prompts for the voice agent. Output goes through TTS, so prompts
insist on short, speakable, plain-text replies."""

from string import Template

DEFAULT_COMPANY_NAME = "the company"
DEFAULT_INBOUND_GREETING = "Hello! Thanks for calling $company_name. How can I help you today?"
DEFAULT_OUTBOUND_GREETING = (
    "Hello! This is the AI assistant calling from $company_name. Am I speaking with $contact_name?"
)

VOICE_STYLE = """\
You are a friendly, professional AI phone agent for {company_name}.
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
can help, answer questions about {company_name}, and capture anything that
needs follow-up (name, need, callback preference).
"""

OUTBOUND_ROLE = """\
This is an OUTBOUND call: you are calling {contact_name} on behalf of
{company_name}. Introduce yourself and the reason for the call right away, be
respectful of their time, and if they are not interested, thank them and
end the call gracefully.

Campaign goal: {goal}

Campaign instructions:
{script}
"""

KNOWLEDGE_RULES = """\
You may be shown excerpts from the company knowledge base, marked
"[knowledge]", just before the caller's message. Rules for using them:
- Treat them as the authoritative answer and prefer them over your own guess.
- If they do not cover what was asked, say you will pass it to the team.
  Never invent a policy, price, date, or phone number.
- Never read a URL, file name, or document title aloud, and never mention
  that you are reading from documents.
"""

PERSONA_HEADER = """\
Additional instructions about who you are and how this company works:
"""


def build_system_prompt(
    direction: str = "inbound",
    contact_name: str | None = None,
    goal: str | None = None,
    script: str | None = None,
    *,
    company_name: str | None = None,
    persona: str | None = None,
    knowledge_enabled: bool = False,
) -> str:
    company = (company_name or "").strip() or DEFAULT_COMPANY_NAME
    prompt = VOICE_STYLE.format(company_name=company)
    if direction == "outbound":
        prompt += "\n" + OUTBOUND_ROLE.format(
            company_name=company,
            contact_name=contact_name or "the contact",
            goal=goal or "have a helpful conversation",
            script=script or "No extra instructions.",
        )
    else:
        prompt += "\n" + INBOUND_ROLE.format(company_name=company)

    if knowledge_enabled:
        prompt += "\n" + KNOWLEDGE_RULES
    if persona and persona.strip():
        prompt += "\n" + PERSONA_HEADER + persona.strip() + "\n"
    return prompt


def render_greeting(
    template: str | None,
    *,
    company_name: str | None = None,
    contact_name: str | None = None,
    direction: str = "inbound",
) -> str:
    """Fill $company_name / $contact_name in an operator-authored greeting.

    Uses string.Template rather than str.format on purpose: the template is
    free text typed into a textarea on /knowledge, and safe_substitute never
    raises on a stray brace, an unknown placeholder, or a missing one.
    """
    if not template or not template.strip():
        template = (
            DEFAULT_OUTBOUND_GREETING if direction == "outbound" else DEFAULT_INBOUND_GREETING
        )

    filled = Template(template).safe_substitute(
        company_name=(company_name or "").strip() or DEFAULT_COMPANY_NAME,
        contact_name=(contact_name or "").strip(),
    )
    # An unresolved $contact_name leaves a hole ("with ?"); tidy the seams so
    # TTS doesn't read a stranded fragment.
    return " ".join(filled.split()).replace(" ?", "?").replace(" .", ".").replace(" ,", ",")


def greeting_for(direction: str, contact_name: str | None = None) -> str:
    """Back-compat wrapper: the old hardcoded greetings, no profile involved."""
    return render_greeting(None, contact_name=contact_name, direction=direction)
