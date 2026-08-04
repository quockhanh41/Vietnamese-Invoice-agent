"""Per-invoice prompt size, estimated before the call is made (roadmap 6.6).

Its own module because it must rebuild EXACTLY what `classify()` sends without
importing the graph: a copy of the prompt that drifts would silently estimate a
prompt nobody sends. `tests/test_llm_budget.py` pins it against the real one by
capturing what reaches litellm.
"""

from typing import Any

import litellm

from invoice_agent.agents.classification import SYSTEM_PROMPT, describe
from invoice_agent.agents.models import ClassificationResult
from invoice_agent.extraction.models import InvoiceData
from invoice_agent.extraction.sanitize import wrap
from invoice_agent.llm.structured import _schema_instruction


def classify_messages(invoice: InvoiceData) -> list[dict[str, Any]]:
    """The messages `classify()` would send for this invoice.

    Mirrors classification.classify + call_structured + gateway._generate:
    system prompt, wrapped invoice text, then the JSON schema instruction.
    `vendor` context is omitted — it is at most one short line and is absent
    for every invoice on its vendor's first sighting.
    """
    prompt = f"{wrap(describe(invoice))}\n\n{_schema_instruction(ClassificationResult)}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def estimate_prompt_tokens(invoice: InvoiceData, *, model: str) -> int:
    """Input tokens the classify call would spend.

    Per model, because the tokenizers differ — M3-A measured 26 vs 34 tokens for
    the same Vietnamese sentence. Returns 0 rather than raising when litellm has
    no tokenizer, the same rule gateway._count_tokens follows.
    """
    try:
        return int(litellm.token_counter(model=model, messages=classify_messages(invoice)))
    except Exception:
        return 0
