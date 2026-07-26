# Karpathy-Inspired Claude Code Guidelines

A single file to improve Claude Code behavior, derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls. Source: https://github.com/multica-ai/andrej-karpathy-skills

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks (typo fixes, obvious one-liners), use judgment — not every change needs the full rigor.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports/variables/functions that YOUR changes made unused. Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```

## Project-Specific Guidelines

**Ngôn ngữ**: tài liệu, comment giải thích "vì sao", và hội thoại viết **tiếng Việt**;
giữ nguyên thuật ngữ kỹ thuật tiếng Anh (interrupt, checkpointer, fan-out, guard…).
Tên biến/hàm và docstring API viết tiếng Anh.

**Safety property — ba lớp, không được làm mỏng đi lớp nào.**
LLM không bao giờ bật một cờ, không bao giờ post một bút toán. Cờ do Python deterministic tính
từ sự thật trong DB. Write action đi qua guard code + DB constraint + test. Response model của
agent **không được có** field approval/flag/status — structured output *là* ranh giới an ninh.
Trước khi thêm field vào bất kỳ Pydantic response model nào, hỏi: LLM có thể dùng nó để tự cấp
quyền không?

**Đo trước, tối ưu sau.** Kỹ thuật mới chỉ merge khi có số trước/sau trên eval set, và mọi metric
LLM phải báo cáo kèm baseline không-LLM (`keywords_vi`). Không thắng baseline ≥15 điểm thì node đó
không cần LLM. Kỹ thuật bị bỏ → **ghi lại lý do**, đó là nội dung portfolio chứ không phải thất bại.

**Verify bằng lệnh chạy được, không phải cảm giác.** `make check` (ruff + mypy + pytest) trước khi
nói xong. Cần Postgres thì `make dev` trước. Mọi hằng số không hiển nhiên trong code phải có comment
ghi **ngày + phép đo + tên file report** biện minh cho nó.

**Không tự ý mở scope.** Kế hoạch nằm ở `docs/Plan_Project_F_2026-07-25.md`, thiết kế ở
`docs/project-f-system-design.md`. Milestone có **gate** — không đạt gate thì không mở milestone
sau. Danh sách "cố tình KHÔNG làm" ở §4 của plan là quyết định đã chốt, không phải thiếu sót:
supervisor agent, Redis, Qdrant, OCR riêng, auth/multi-tenant, tích hợp ERP thật.

**Pin version LangGraph — không bump giữa chừng.** `langgraph==1.2.9` và
`langgraph-checkpoint-postgres==3.1.0` pin chặt vì correctness của HITL durability rơi vào đó.
Re-pin là quyết định có chủ đích kèm `test_hitl_survives_restart.py` xanh, không phải `uv pip -U`.

**Stack**: LangGraph (3 graph: enrichment / batch / approval) · Postgres 17 + pgvector (system of
record **và** checkpoint, hai schema `app` / `langgraph`) · LiteLLM gateway với model-chain
fallback × key rotation · FastAPI · Langfuse (SDK v3, env-gated). Không Redis, không Qdrant.
