# ADR 0005 — Structured Output Pydantic Model as Primary Security Boundary for Prompt Injection

**Date**: 2026-08-02 · **Status**: Accepted
**Context**: M6-A · **Related**: `src/invoice_agent/agents/models.py`, `src/invoice_agent/extraction/sanitize.py`, `src/invoice_agent/tools/guards.py`, design §9.2, spec §3

## Context

Prompt injection attacks against LLM-based applications attempt to hijack model execution to approve unauthorised actions, bypass validation filters, or alter financial records. Standard detection approaches rely on keyword filters or secondary injection classification models, which add latency and can be bypassed by novel or Vietnamese-language instruction phrasing.

In this architecture (design §9.2), prompt injection is designed to be **inexpressible** through the agent response models rather than detected. Agent responses are strictly parsed into Pydantic models with `extra="forbid"` and `frozen=True`.

## Decision

1. **Structured Output as the Security Boundary:** The application does not use prompt injection detection models or keyword blocklists. Instead, schema enforcement (`extra="forbid"`) at the `call_structured` seam serves as the primary technical boundary against overt prompt injections.
2. **No Execution Channel:** Agent response models (`ClassificationResult`, `AuditVerdict`) are strictly prohibited from carrying approval, flag, or status fields.
3. **Deterministic Flag Layer for Legal-Channel Attacks:** Attacks attempting category laundering (e.g. asking to be classified under "OFFICE_SUPPLIES" to dodge a Marketing budget) succeed at the LLM schema boundary because `category` is a legal model output. Security against category laundering is enforced deterministically by Python in the database flag layer (`BUDGET_BREACH`), independent of LLM classification choices.

## Evidence & Measured Results

- **30/30 Inexpressibility Measured:** Across 30 test cases covering 6 injection payloads across 4 document text surfaces + Classifier reasoning channel, 0 payloads obtained approval decisions or posted journal entries.
- **Negative Control & Category Shift:** 18/24 carrier/twin pairs classified identically; 6/24 showed category shifts (e.g. `category_laundering`). This measured proof confirms that while LLM response schemas stop overt field injection (`extra="forbid"` rejects unknown fields like `approval_status`), category laundering passes the schema boundary and is stopped by Python's budget reconciliation logic (`BUDGET_BREACH`).
- **Mutation Verification (4/4 RED):**
  1. **Removing `extra="forbid"`:** Went RED (`StructuredOutputError` was missed, allowing unmodeled fields).
  2. **Breaking `sanitize.wrap()`:** Went RED (`test_sanitize.py` boundary tests failed).
  3. **Reading flags from in-memory state instead of `app.invoice_flags`:** Went RED (`BUDGET_BREACH` suppressed).
  4. **Removing `invoice_id` check in `guards.py`:** Went RED (`test_never_post_without_approval.py` failed).

## Consequences

- Zero maintenance overhead for prompt injection detectors or keyword lists.
- Strict requirement that no agent response model may gain approval/status fields.
- Full reliance on deterministic Python code for financial and authorization controls.
