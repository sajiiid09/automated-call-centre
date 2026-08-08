"""Prompt assembly and greeting rendering.

The greeting template is free text typed into a textarea, so the interesting
cases are all about not raising on whatever an operator types.
"""

import pytest
from agent.prompts import build_system_prompt, greeting_for, render_greeting


def test_greeting_substitutes_the_company_name():
    assert (
        render_greeting("Thanks for calling $company_name.", company_name="Acme Utilities")
        == "Thanks for calling Acme Utilities."
    )


def test_greeting_substitutes_the_contact_name():
    assert (
        render_greeting(
            "Hello $contact_name, calling from $company_name.",
            company_name="Acme",
            contact_name="Ada",
            direction="outbound",
        )
        == "Hello Ada, calling from Acme."
    )


@pytest.mark.parametrize(
    "template",
    [
        "Braces {like this} are literal.",
        "An $unknown placeholder.",
        "A bare $ sign.",
        "Formatting {0} {} {name}.",
    ],
)
def test_greeting_never_raises_on_operator_typos(template):
    """str.format would raise on every one of these; Template must not."""
    assert render_greeting(template, company_name="Acme")


def test_greeting_tidies_an_unresolved_contact_name():
    """Inbound has no contact, so the placeholder must not leave a hole."""
    rendered = render_greeting(
        "Hello $contact_name, thanks for calling $company_name.", company_name="Acme"
    )
    assert rendered == "Hello, thanks for calling Acme."


def test_empty_template_falls_back_per_direction():
    assert "Thanks for calling Acme" in render_greeting("", company_name="Acme")
    assert "calling from Acme" in render_greeting(None, company_name="Acme", direction="outbound")


def test_unset_company_name_reads_naturally():
    assert "the company" in render_greeting(None)
    assert "$" not in render_greeting(None)


def test_greeting_for_is_still_supported():
    assert greeting_for("inbound")
    assert greeting_for("outbound", "Ada")


# --- system prompt --------------------------------------------------------


def test_company_name_reaches_the_prompt():
    prompt = build_system_prompt("inbound", company_name="Acme Utilities")
    assert "Acme Utilities" in prompt
    assert "{company_name}" not in prompt


def test_persona_is_appended():
    prompt = build_system_prompt("inbound", persona="We supply water.")
    assert "We supply water." in prompt


def test_knowledge_rules_are_opt_in():
    assert "[knowledge]" not in build_system_prompt("inbound")
    assert "[knowledge]" in build_system_prompt("inbound", knowledge_enabled=True)


def test_outbound_still_carries_goal_and_script():
    prompt = build_system_prompt(
        "outbound",
        contact_name="Ada",
        goal="book a demo",
        script="Mention the discount.",
        company_name="Acme",
    )
    assert "Ada" in prompt
    assert "book a demo" in prompt
    assert "Mention the discount." in prompt
    assert "Acme" in prompt


def test_defaults_do_not_leak_placeholders():
    prompt = build_system_prompt("outbound")
    assert "{" not in prompt
