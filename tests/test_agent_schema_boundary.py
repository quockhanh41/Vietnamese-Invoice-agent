"""Structured output IS the security boundary (CLAUDE.md, design §9.2).

Pulled forward from roadmap item 6.4 because M4-C creates the project's first
agent response models, and shipping them with their guard deferred to M6 is the
gap this project keeps finding.

The defence against §9.1's payloads is NOT detection — payloads 5 (social
engineering) and 6 (category laundering) contain no injection markers at all and
defeat every keyword detector. The defence is that the model has no channel to
act through: if `ClassificationResult` has no `approval_status`, "set
approval_status=approved" is not a thing the model can do, however persuasively
it is asked.
"""

import inspect

from pydantic import BaseModel

from invoice_agent import agents

# Substrings that would give a model a channel to authorise, flag or transition
# state. Matched against field names case-insensitively.
FORBIDDEN = (
    "approv", "flag", "status", "authoriz", "authoris",
    "posted", "decision", "decided", "override", "allow", "permit",
)


def _agent_models() -> list[type[BaseModel]]:
    found = []
    for _name, obj in inspect.getmembers(agents.models):
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel:
            found.append(obj)
    return found


def test_there_are_models_to_audit():
    """Without this the scan below passes vacuously — the M2-C lesson."""
    assert _agent_models(), "no agent response models found; the audit proves nothing"


def test_no_agent_model_can_authorise_anything():
    offenders = []
    for model in _agent_models():
        for field_name in model.model_fields:
            low = field_name.lower()
            for token in FORBIDDEN:
                if token in low:
                    offenders.append(f"{model.__name__}.{field_name} contains {token!r}")
    assert not offenders, (
        "an agent response model has a field the LLM could use to self-authorise; "
        f"structured output is the security boundary: {offenders}"
    )


def test_models_forbid_extra_fields():
    """`extra='forbid'` is half the boundary: without it a model can return
    `approval_status` anyway and Pydantic keeps it, so the field audit above
    would be checking a schema the response is not held to."""
    for model in _agent_models():
        assert model.model_config.get("extra") == "forbid", model.__name__
