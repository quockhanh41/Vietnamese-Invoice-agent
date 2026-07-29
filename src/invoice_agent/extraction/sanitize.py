"""Invoice text is DATA. It never becomes instruction (design doc §9.2).

Everything a vendor wrote — descriptions, names, addresses — travels inside an
escaped `<invoice_data>` block and is never concatenated into a system prompt.
Escaping is what makes the closing tag unforgeable: a payload containing
`</invoice_data>` arrives as `&lt;/invoice_data&gt;` and cannot end the block.

No nonce delimiter. Escaping already defeats a forged close, and per CLAUDE.md
the real security boundary is that the response model carries no
approval/flag/status field — the model has no CHANNEL to act on an instruction
even if it decides to follow one. A nonce would harden a layer that is not
load-bearing.

Callers: `llm/structured.py` (M3-B — validation errors quote model output
verbatim, see its docstring) and `classify`/`audit` (M4).
"""

OPEN_TAG = "<invoice_data>"
CLOSE_TAG = "</invoice_data>"


def escape(text: str) -> str:
    """XML-escape the three characters that can open a tag.

    `&` FIRST, always: escaping `<` first turns `<` into `&lt;` and the
    following ampersand pass would rewrite it to `&amp;lt;`.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap(text: str) -> str:
    """Escape, then fence. The result is safe to concatenate into a user prompt."""
    return f"{OPEN_TAG}{escape(text)}{CLOSE_TAG}"
