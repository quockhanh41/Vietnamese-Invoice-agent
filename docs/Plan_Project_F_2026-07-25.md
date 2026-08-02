# 🧾 Roadmap Project F — Vietnamese Invoice & Expense Agent

> **Ngày lập**: 2026-07-25 · **Cập nhật §1**: 2026-07-27 · **Trạng thái**: bản sống — cập nhật sau mỗi milestone
> **Tiền đề**: đọc kèm [`project-f-system-design.md`](project-f-system-design.md) (thiết kế đầy đủ),
> [`project-b-additional-candidates.md`](../../phase-5-agentic-ai/project-b-additional-candidates.md)
> (vì sao chọn F — nằm ngoài repo này, ở `phase-5-agentic-ai/`),
> [`project-f-phase5-mapping.md`](project-f-phase5-mapping.md) (bản đồ 7 kỹ năng GĐ5 —
> **lưu ý: design doc đã sửa lại 5 điểm trong file này**).
> **✅ Đã làm 2026-07-26 (M0.1)**: repo `vietnamese-invoice-agent` đã `git init` riêng và file này
> đã chuyển thành `docs/Plan_Project_F_2026-07-25.md`, theo đúng convention của Project A
> (vietnamese-legal-bot). Đường dẫn cũ `project-f-roadmap.md` ở root **không còn tồn tại**.

---

## 0. Bảy quyết định khung (đã chốt với user 2026-07-25)

| Quyết định | Lựa chọn | Hệ quả lên plan |
|---|---|---|
| **Định vị** | Portfolio production-grade, capstone GĐ7 | Mục tiêu: demo public + số đo eval + ADR thuyết phục nhà tuyển dụng VN. KHÔNG làm auth, multi-tenant, tích hợp ERP thật. |
| **Kiến trúc graph** | **Ba graph ngay từ đầu** | Không dựng 1 graph rồi refactor — phân tích §1.1 design doc đã đủ thuyết phục, refactor chỉ tốn 3–4 ngày để xác nhận điều đã biết. |
| **Multi-agent** | **Không có supervisor**, multi-agent = bất đồng Classifier ↔ Auditor | ADR *"vì sao tôi chủ động loại bỏ Supervisor"* là artefact, không phải lời bào chữa. |
| **UI** | Streamlit ở M5 (bảo hiểm) → Next.js ở M7 (nâng cấp) | Luôn có demo chạy được từ M5. Next.js time-box; trượt thì ship Streamlit. |
| **Vision fallback** | **Giữ** | Nhánh XML → Vision → Manual Queue là minh chứng Multi-modal + Strategic Escalation. |
| **Corpus** | **250–300 hóa đơn** (không phải 400) | Đủ đại diện 14 nhóm defect, tối ưu thời gian eval + shadow cost. Xem phép tính §2.2. |
| **Ngân sách** | $0 — free tier toàn bộ | Groq free + key rotation (sẵn có từ Project A) + Gemini free + Langfuse Cloud Hobby + Postgres local/Neon free + GitHub Actions. |

### Nguyên tắc xuyên suốt (không thương lượng)

1. **LLM không bao giờ bật một cờ, không bao giờ post một bút toán.** Cờ do Python deterministic
   tính từ sự thật trong DB; write action đi qua guard code + DB constraint + test. Mọi thay đổi
   phải giữ nguyên vẹn ba lớp này.
2. **Đo trước, tối ưu sau.** Mỗi kỹ thuật mới chỉ merge khi có số trước/sau trên eval set. Kỹ thuật
   không cải thiện số → bỏ, **ghi lại lý do** (bản thân việc đó là nội dung portfolio — kế thừa
   đúng bài học từ Project A M3.7).
3. **Mọi metric LLM báo cáo kèm baseline không-LLM.** Classification phải thắng `keywords_vi`
   ≥15 điểm. Không thắng thì không cần LLM ở node đó.
4. **Deterministic thì đừng để LLM làm.** Parse, số học, checksum, bật cờ — máy tính làm *chính
   xác*, không phải LLM làm *xấp xỉ*. 5/8 node của `enrichment_graph` không gọi LLM là thiết kế
   đúng, không phải tiết kiệm.
5. **Không tin graph state, tin database.** Guard `create_journal_entry` phải đọc lại Postgres xác
   nhận `approvals` row đã persist — state có thể bị giả mạo qua time-travel fork.
6. **Pin version LangGraph, không bump giữa chừng.** Checkpointer + interrupt semantics là phần
   correctness của hệ thống này rơi vào. Re-pin có chủ đích, với test HITL durability xanh, hoặc
   không re-pin.
7. **Không đạt gate thì không mở milestone sau.** Đặc biệt M1 — không có XML thật thì không được
   bắt đầu M2.

---

## 1. Vị trí hiện tại (tóm tắt 1 đoạn)

**Updated 2026-07-29 — M0 (5/5), M1 (4/4), M2 (5/5) and M3 (4/4) all closed.** M3 was split into
three specs, each reviewed after implementation: **M3-A** LLM plumbing (cost capture, `LLM_MODE=replay`),
**M3-B** vision surface (sanitize, structured retry, `vision.py`, corpus PDFs), **M3-C** the enrichment
graph. `make check` green with **455 tests**, and green again under `LLM_MODE=replay` with **no API
keys** — the condition that lets CI and the live demo run without touching a provider. The corpus is
300 manifest entries: 285 XML (all parse) + 15 PDFs now rendered, plus 3 XML↔PDF twins for the
escalation path. Gate evidence in `docs/notes/2026-07-29-m3a-llm-plumbing.md`,
`…-m3b-vision-surface.md`, `…-m3c-enrichment-graph.md`.

**Every milestone review has found defects the implementation reported as passing** — a gutted M1.6
guard and a fabricated constraint (M2-A), a mutation guard that guarded nothing (M2-B), a red
`make check` and an unlinted `scripts/` (M2-C), a `record` mode that could never record a failure
and a `generate_stream` bypassing the replay seam (M3-A), a "verbatim" criteria list that dropped
its own failing criterion (M3-B), and two tests that passed exactly once per database plus a guard
made red by asserting a list contains itself (M3-C). All fixed and re-measured.

**First real number, measured 2026-07-29 by the M3 exit audit.** The deterministic layer was run
across all 285 XML invoices and scored against the corpus manifest — no LLM involved, so it costs
nothing to repeat:

| | |
|---|---|
| XML parsed | **285/285** |
| Expected findings detected (locally-decidable codes) | **47/47 — recall 100%** |
| Invoices with no expected local finding, flagged anyway | **0/238 — false-positive rate 0.0%** |

Locally-decidable means the four codes `validate()` can decide from one invoice with no database:
`ARITHMETIC_MISMATCH`, `WORDS_DIGITS_MISMATCH`, `TAX_CODE_CHECKSUM_INVALID`, `LINE_VAT_MISMATCH`.
Duplicate, budget and vendor codes need corpus-wide or DB state and are M4's to measure. Design doc
§10.4 targets a flag false-positive rate ≤5%; the deterministic layer is at **0%** before any LLM
exists. The generator imports neither `xml_schema` nor `domain/` — enforced by
`test_generator_independence` — so this agreement is evidence, not a tautology.

> The first pass of that measurement read **2.5% false positives**, and every one turned out to be a
> **mislabelled corpus row rather than a validator error**: `price_outlier` scaled a line's amount
> 10× without scaling its `TThue` (4 invoices, deviations to 5,040,000 đồng), and duplicates
> inherited `wrong_vat_rate` from the invoice they copied while declaring only their duplicate
> marker (2 invoices, one of them two hops down a chain). Both fixed in the generator, and
> `test_no_invoice_carries_an_UNDECLARED_finding` now pins the corpus's labels to its contents in
> both directions. Left unfixed, M6 would have scored six correct detections as false positives and
> **understated its own precision**.

> **The pattern is now the finding.** Three consecutive milestones were declared closed on a commit
> where `make check` was RED, with the fixes sitting uncommitted. M3-C's red commit `097388b` was
> **pushed**, CI ran it, and CI **failed** (run `30420027036`) — the safety net worked and nobody
> looked. The fix is not more tooling: it is that a milestone is not closed until CI is green on the
> pushed commit. **As of this writing `origin/main` is still at the red `097388b`; the green fix
> `76d6280` is local and unpushed.**


**M0 — nền chạy được.** Repo `git init` riêng, đã push lên
`github.com/quockhanh41/Vietnamese-Invoice-agent` (private). src-layout `src/invoice_agent/` cài
bằng `uv pip install -e ".[dev]"` — **không file nào cần `sys.path.insert`**. `make dev` dựng
Postgres 17 + pgvector, 2 schema `app`/`langgraph`, `alembic upgrade head` với `alembic_version`
hạ cánh đúng schema `app`. `make check` = ruff sạch + mypy sạch + **55 test xanh**. Có `config.py`
(`pydantic-settings`, `NoDecode` + validator tách CSV), `gateway.py` port từ Project A nhưng
**nhận `Settings` thay vì tự `os.getenv`**, `observability.py` copy nguyên vẹn 41 dòng,
`requirements.lock`, CI (ruff/mypy/pytest trên Postgres thật + gitleaks quét full history),
`CLAUDE.md`, `.env.example`. **Ba ràng buộc dependency verify LIVE**: `fastapi 0.140.0 →
starlette 1.3.1`, `langfuse 3.15.0` (<4.0.0), `langgraph 1.2.9 + checkpoint-postgres 3.1.0`.

**M1.1 + 1.2 — đạt gấp bội.** `tests/fixtures/real_samples/` có **8 hóa đơn thật, 8 nhà cung cấp,
3 phiên bản schema**, đều đã ẩn danh (4 file cuối là hóa đơn người nhà, có sự đồng ý). Chúng
**bác bỏ 10 giả định** của bảng M1.2 gốc và cho ra **luật validation đo được 8/8** — xem bảng
"Thực tế vs giả định" ở M1 và README của thư mục fixtures. Đây là toàn bộ giá trị của gate cứng:
nếu viết parser theo trí nhớ thì generator cũng sinh XML khớp luật sai đó, 300/300 pass, và
report ghi `false-positive rate 0%` — một con số bịa.

**M1.5 + 1.6 — done, then hardened by review on 2026-07-27.** `interrupt()` + `PostgresSaver`
survive `kill -9` **10/10** on a clean CI runner, and `alembic revision --autogenerate` against a
database holding 4 real checkpoint tables emits **no DROP**. ADR
`0002-alembic-langgraph-schema-isolation.md` is written.

The review found the first cut of both tests asserted less than they claimed, and both are fixed:

- **The Alembic guard did not touch the thing it guarded.** It re-implemented `include_object`
  inside the test. Measured: replacing the body in `migrations/env.py` with `return True` left the
  test **green** while real autogenerate emitted `drop_table` for all four checkpoint tables **and**
  `alembic_version`. It now drives `alembic revision --autogenerate` for real, and is
  mutation-verified red/green.
- **Half of the load-bearing finding was never measured.** `trace` cannot show it: the first pass of
  the gate node raises `GraphInterrupt` and commits nothing, so it appears once in `trace` whether
  its body ran once or twice. A `node` column now counts per node — **`n1_record` (before the gate)
  = 1, `n2_gate` (contains `interrupt()`) = 2**, measured across the process boundary.
- **The restart test could kill too early.** It polled for *any* checkpoint row, but a run to
  interrupt writes **3**, the first (`step=-1, source=input`) landing before any node runs — a ~9ms
  window in which the kill precedes `n1_record`. It now polls
  `checkpoint_writes.channel = '__interrupt__'`.
- **CI never ran migrations.** `init_db.sql` creates empty schemas only, so every Postgres test
  would have failed on a fresh runner. `alembic upgrade head` is now a CI step.

**Consequence for M5, sharper than before**: `post_entry` must be a node **after** `approval_gate`
— *and* idempotent. Node placement only stops the repeat caused by `interrupt()`. Measured
2026-07-27: `kill -9` **inside** a post-gate node, after its write and before its checkpoint commits,
then resume from a new process → the node runs again → **2 rows**, i.e. two journal entries. What
actually stops that is the M2.4 `UNIQUE` constraint plus the M5.3 guard, not the graph shape.

**M1.3 + 1.4 — done 2026-07-27, then hardened by review the same day**, exactly as 1.5 and 1.6 were.
`xml_schema.py` (path map anchored at `HDon`), `models.py` (`InvoiceData`), `xml_parser.py`. 8/8 real
fixtures parse; `make check` green with 123 tests (116 at the time of writing; the review commits
that closed the two holes added the other 7). Spec
`docs/superpowers/specs/2026-07-27-xml-extraction-design.md`, plan
`docs/superpowers/plans/2026-07-27-xml-extraction.md`. **M1 gate 4/4.**

The review found two tests that passed while guarding nothing — the third and fourth instance of this
pattern in this milestone, and worth recording rather than quietly fixing:

- **The entity guard did not guard.** `test_entity_declarations_rejected` asserted only the exception
  *type*, and stayed **green** with `defusedxml` swapped for stdlib `ET`. The payloads' root is
  `<r>`, so under stdlib the billion-laughs document *parses* (expanding to `'AAAA'`×4) and then dies
  in `locate_hdon` as "unrecognised root element", while XXE dies as "malformed XML" — both
  `XmlExtractionError`, so the assertion was satisfied by an error unrelated to entity expansion. It
  now matches on `"unsafe XML rejected"` and is mutation-verified red for both payloads.
- **The six header `TTKhac` bags had no test at all.** Breaking 2 of 6 `EXT_SCOPES` paths left all
  116 tests **green** — a wrong path yields no bag rather than an error, so it is silent data loss.
  `test_header_ext_scopes_are_populated_from_real_files` now pins one measured key per scope.
- **A claim was corrected, not just the code.** Row 1.3's first cut said "5/5 mutation tests
  verified". Measured: 3 of 5 turn their named guard red, field-level `.//` is caught by the static
  guard rather than the behavioural one, and the entity mutation was not caught at all until fixed.


**Còn ở project khác, CHƯA port** (tính vào scope M3): `execute_tool_with_retry` +
`DISPATCH_TABLE` ở `../phase-5-agentic-ai/react-pattern-tool-use/mini-project/agent.py` — port ở
M3.2 và **bỏ hàm calculator dùng `eval()`**.

**Chưa từng viết bao giờ** (phải tính vào scope, không phải copy): `interrupt()` /
`Command(resume=)` hiện đại — module `human-in-the-loop/` chỉ có prose, `support_agent.py` dùng
`interrupt_before` cũ và LLM đều mock; `PostgresSaver` — mới chỉ wire `MemorySaver`; `Send`/fan-out
— zero code; MCP server — chỉ có sketch 19 dòng; LangFuse cho *agent* — module dạy LangSmith, zero
code LangFuse; cost tracking — Project A **hoàn toàn không có** (dashboard hardcode `$0.00`).

**Nợ kỹ thuật kế thừa**: ✅ `os.getenv` rải rác → một `config.py` (M0.2) · ✅ `sys.path.insert` →
src-layout (M0.1) · ✅ không lint/typecheck/CI → đã có (M0.6) · ✅ **PAID 2026-08-01 (M5-C)**
`main.py` 643 dòng với `routes/` rỗng → `src/invoice_agent/api/` tách thật ngay từ commit đầu:
`app.py` + `schemas.py` + `progress.py` + năm module route (`health`, `invoices`, `reports`,
`approvals`, `batches`), không module nào chứa SQL — `db/queries.py` giữ phần đọc. **Cả bốn món nợ
kế thừa từ Project A giờ đã trả xong.**

**⚠️ Bài học bảo mật của chính repo này**: hóa đơn thật mang PII **và** chứng thư số, mà
**gitleaks không bắt được PII** — nó tìm token. `.gitignore` là lớp phòng thủ duy nhất, và nó
**đã từng thủng**: neo `/*.xml` chỉ chặn file rời ở root, để lọt nguyên thư mục nhà cung cấp gửi
kèm 2 PDF có số CCCD (vá ở `bb8718d`). Đính chính một cảnh báo cũ: bản roadmap đầu ghi
`vietnamese-legal-bot/.claude/settings.json` "đang commit Langfuse key plaintext" — **sai**,
`.claude/` đã bị gitignore và `git log --all` trả về rỗng; key chưa bao giờ vào history.

**Next**: M5 — HITL production, the API, and the Streamlit insurance UI. **M4 closed 2026-08-01
(4/4 specs)**; the gate table is in §3. What is owed, roughly in the order it will hurt:

- **A throttled Auditor re-measurement.** M4-C's 12.5% dispute precision is the milestone's headline
  negative result, but Gemini free tier rate-limited **177 times** during the run, so those audits
  fell through to a second model — "the Auditor" was a timing-dependent mixture. Family disjointness
  held throughout, and the finding survives either mixture, but **the number M6 publishes must come
  from one rater**.
- ✅ **PAID 2026-08-01 by M5-B.** One live 30-invoice batch at `max_concurrency=4`, 6 keys per
  provider: **266 rate-limited calls to produce 63 successful ones** (groq 176/209, gemini 90/122),
  20.4s live vs 4s replay. No invoice was lost — rotation plus the model chain absorb every limit —
  but **it absorbs them by silently demoting to weaker models**: 86% of classifications were served
  by the *fallback* `llama-3.1-8b-instant`, not the primary. The constant stays at 4 because nothing
  measured a better value; M4-C's *serial* run took 177 limits, so **concurrency is not the binding
  constraint, free-tier quota is**. Two consequences for M6: the Auditor is a two-model mixture again
  (25 gemini / 5 groq), and **M4-C's 95.4% classification accuracy is in question** because it never
  recorded which model served each call.
- **Per-department gate-batch declarations.** `gate_batch.json` pins the MKT chain only; the batch
  raises 9 `BUDGET_BREACH` flags and 8 are undeclared-but-correct (§3's note).
- **Vision fixtures do not travel across platforms** — the replay key hashes a rasterized PNG, so
  macOS/ARM recordings miss on CI. Re-record on the CI platform, or score from committed PNGs.
- **The six PII-carrying vendor PDFs are still in git history on origin**, alongside the M1 tax-code
  leak. Deferred four times now; deferred is still not the same as decided.
- Still open from M1: the **NĐ 70/2025/NĐ-CP** field check.

**The rule, restated because M4 failed it twice.** A milestone is not closed until `make check` is
green on the *pushed* commit and CI agrees — and CI must actually *run* the milestone's gate. M4-D
was declared closed on a red commit whose gate test did not execute in CI at all, because no step
generated the corpus. Adding that one CI step immediately exposed two further defects, including a
gate that was making live API calls while claiming to replay.

- ✅ **PAID 2026-08-01.** The vision branch now has its first accuracy number: **2/2 XML↔PDF twins
  extracted 4/4 on `invoice_number` · `invoice_series` · `seller_tax_code` · `total_amount`**
  (`INV-0196`, `INV-0275`). A third twin, `INV-0029`, is recorded as **unmeasured** rather than
  failed — six attempts, every one a Gemini 503, so it never produced an extraction to score.
  Pinned by `tests/test_vision_accuracy.py`, replaying with every key unset. Owed since M3-B and
  deferred three times; paying it took one afternoon once a key existed, and it surfaced two
  defects in `structured.py` that had made every live structured call impossible.
  Re-declared at M4-A: Task 9 step 1 precondition check confirmed no key configured.
  **M6 cannot report a vision number until this is paid**, and paying it later means rebuilding all
  of M3's context first.
- **The six PII-carrying vendor PDFs are still in git history on origin**, alongside the M1 tax-code
  leak in this document. Untracked from `HEAD` at M3-B, but the blobs remain reachable. Deferred
  twice; deferred is not the same as decided.
- **Two design debts M4 inherits**: `thread_id` is `enrich:{invoice_id}` and therefore stable across
  re-runs, so batch needs a per-run component (M3-C §8.5); and nothing yet proves the enrichment
  graph survives being wrapped as a function node, which is exactly what M4.6 does (M3-C §8.3).
- **ADR `0001-three-graphs-not-one.md`**, owed since M1. M4 adds `0003-no-supervisor-agent.md` and
  `0004-serial-budget-reconciliation.md`, so writing all three together is cheaper than one at a time.
- Still open from M1, still not blocking: the **NĐ 70/2025/NĐ-CP** field check —
  `InvoiceLine.item_kind` stays `int | None` rather than defaulting to `1` until that answers whether
  QĐ-1450 permits omitting `TChat`; whether `TSuat` can carry the non-percent codes `KCT`/`KKKNT`,
  which the parser rejects loudly today because no fixture contains one; and the terminal recording
  (deferred to M8).

**The rule M4 must not repeat**: a milestone is not closed until `make check` is green **on the
pushed commit** and CI agrees. Three milestones running have failed that.

> **Scoreboard, updated 2026-08-01.** M4-A and M4-B both passed it — CI green on the exact closing
> SHA (`a444e22`/`67b1638`, `4682e67`/`90562de`). **M4-C failed it, mildly**: `f08c8a6` was pushed
> **red** (ruff `UP007` + unused imports) and fixed one commit later in `6ea76f9`. Four milestones
> have now failed the rule — but this differs from M3-C's `097388b` in the way that matters: there
> the red commit was pushed, CI failed, **and nobody looked**. Here it was noticed and fixed at once.
>
> **The interesting part is the misattribution, and it is a new failure mode.** `f08c8a6` touched
> exactly one file — this document — and cannot have introduced a lint error. The errors came from
> the Task 2–6 commits, and `6ea76f9` fixed them in `migrations/`, `scripts/eval_classification.py`,
> `tests/test_agents.py` and `tests/test_auditor_chain.py`. CI ran on **nothing** between `90562de`
> and `f08c8a6`: all seven M4-C commits were pushed as one batch, so CI first saw the tip.
>
> So per-task commits are a safety net **only if per-task verification runs**. Batching the push
> means the first CI signal lands on the tip, and the tip's title — here "docs(roadmap): update M4-C
> completion state" — actively misdirects, because a docs commit reads as one that could not
> possibly break the build. Running `make check` after any of Tasks 2 through 6 would have caught it
> at the commit that caused it.

---

## 2. Bản đồ hạ tầng $0 + phép tính khả thi

### 2.1 Stack free tier

| Lớp | Dịch vụ (free) | Giới hạn | Đủ không? |
|---|---|---|---|
| Database | **Postgres local (docker) + pgvector** — deploy dùng **Neon free** | Neon: 0.5GB storage | ✅ 300 hóa đơn + checkpoint ≈ vài chục MB |
| LLM text | **Groq free** + key rotation (sẵn có) | TPD/TPM theo key | ✅ Xem 2.2 — một lượt full corpus ≈ 720 call |
| LLM vision | **Gemini 3.5 Flash free** (AI Studio) | quota ngày | ✅ chỉ ~15 hóa đơn cần vision |
| Observability | **Langfuse Cloud Hobby** | 50K observations/tháng | ✅ Self-host v3 cần thêm ClickHouse + Redis + MinIO — 4 service để trace 1 agent, không đáng |
| CI | GitHub Actions free | 2000 phút/tháng | ✅ CI chạy `LLM_MODE=replay`, không gọi API |
| Deploy | HF Spaces free / Railway free | | ✅ (quyết ở M8) |

> **Khác Project A**: không Qdrant, không Redis. Postgres một mình gánh cả system of record,
> checkpoint, và pgvector. Một service không có việc là một gánh nặng.

### 2.2 Phép tính chi phí (tính 2026-07-25, corpus 300 hóa đơn)

Phân bố call theo thiết kế `enrichment_graph` (5/8 node không gọi LLM):

| Node | Số call / lượt full corpus | Ghi chú |
|---|---:|---|
| `parse_xml`, `validate`, `signals`, `reconcile_budget` | **0** | Python thuần |
| `classify` | 300 | ~800 tok in / 100 out |
| `audit` | 300 | ~1000 in / 150 out |
| `vision_extract` | ~15 | 5% corpus (4% chỉ-PDF + 1% không đọc được) |
| `reconciliation` narrative | ~105 | chỉ hóa đơn có cờ (~35%) |
| **Tổng** | **~720 call** | ~1.1M token/lượt |

**Shadow cost theo giá `gpt-4o-mini`** ($0.15/1M in, $0.60/1M out):

| Node | Cost |
|---|---:|
| classify | $0.054 |
| audit | $0.072 |
| reconcile narrative | $0.022 |
| vision | $0.006 |
| **Tổng / lượt** | **≈ $0.154** |
| **Per-invoice** | **≈ $0.0005** |

**Kết luận**: mục tiêu ≤$0,01/hóa đơn (§10.4 design doc) thoải mái đạt — **thấp hơn 20 lần**. Ngay cả
20 lượt eval trong suốt dự án cũng chỉ ≈ $3 shadow, và **thực tế $0** trên Groq free tier.

> Con số này chính là câu trả lời cho *"agent của bạn tốn bao nhiêu?"* — và lý do nó nhỏ như vậy là
> vì 85% hóa đơn đi đường XML deterministic, không phải vì dùng model rẻ. Đó là điểm nên nói.

**Vì sao 250–300 chứ không phải 400**: nhóm defect nhỏ nhất là prompt injection 1.5% → ở 300 vẫn
được ~5 ca gốc (× ~5 biến thể = 26 test case, đủ cho gate `0/26`). Nhóm "vượt ngân sách" 4% → 12 ca,
đủ để test serialization. Xuống dưới 250 thì `flag false-positive rate ≤5%` mất ý nghĩa thống kê
(chỉ còn ~10 hóa đơn sạch bị gắn cờ oan là đã vượt ngưỡng).

---

## 3. Milestones

> Mỗi milestone có **việc làm** (tham chiếu design doc §), **gate** (điều kiện đo lường — không đạt
> thì không mở milestone sau), và **artefact** (thứ để lại cho portfolio).
> Thứ tự M0→M3 là **cứng** (phụ thuộc dữ liệu và hạ tầng). M6↔M7 có thể hoán đổi.
> Cột "Tuần" là chỉ báo, không phải cam kết — gate mới là thứ quyết định.

---

### M0 — Nền sạch: scaffolding, CI xanh, pin version *(tuần 0, 2–3 ngày)*

*Mục đích: mọi thứ từ M1 trở đi đứng trên nền chạy được. Không được ăn vào thời gian M1.*

| # | Việc | Tham chiếu |
|---|---|---|
| 0.1 | ✅ **Done 2026-07-26.** Pushed to `github.com/quockhanh41/Vietnamese-Invoice-agent` (private). `sys.path.insert` now appears exactly once in the repo — inside the `__init__.py` docstring stating it is not needed. Repo `vietnamese-invoice-agent` (sibling, git riêng), **src-layout** + `pip install -e .` | §11.2 |
| 0.2 | ✅ **Done 2026-07-26 — and it hid a second bug until 2026-07-27.** The `NoDecode` trap is locked by tests. M1.5 then exposed an unrelated defect in the same file: `options=-csearch_path=…` was never URL-encoded, so psycopg rejected the DSN while SQLAlchemy silently accepted it — i.e. Alembic worked and `PostgresSaver` died. Fixed in `542adb3`; the old tests could not catch it because they compared strings instead of opening a connection. **Một** `config.py` với `pydantic-settings`. Model chain là `Annotated[list[str], NoDecode]` — không có nó pydantic-settings chạy `json.loads()` trước validator `mode="before"` và `groq/a,gemini/b` sẽ raise | §11.2 |
| 0.3 | ✅ **Done 2026-07-26**, re-verified from an empty volume 2026-07-27 (`docker compose down -v` → `up` → `init_db.sql`): both schemas created, `vector` in `public`. `docker-compose.yml`: **chỉ** postgres + pgvector. `init_db.sql` tạo 2 schema `app` + `langgraph` | §2.10 |
| 0.4 | ✅ **Done 2026-07-26.** `observability.py` copied verbatim (41 lines, unchanged). `gateway.py` ported but takes `Settings` instead of calling `os.getenv` itself, with `tests/test_gateway.py`. Copy `observability.py` **nguyên vẹn**; copy `gateway.py` + test của nó | §11.1 |
| 0.5 | ✅ **Done 2026-07-26.** `requirements.lock` = 333 pinned lines; tight pins on the load-bearing pair, floors elsewhere. **Pin version**: `langgraph==1.2.9`, `langgraph-checkpoint-postgres==3.1.0`, `litellm`, `sqlalchemy`, `alembic`, `psycopg`. Pin **chặt** nhóm load-bearing, để **floor** cho phần còn lại + `requirements.lock` | §11 |
| 0.6 | ✅ **Done 2026-07-26, one hole closed 2026-07-27.** ruff + mypy + pytest on real Postgres 17, `LLM_MODE=replay`, no API keys, gitleaks over full history. The hole: the workflow created the schemas but never ran `alembic upgrade head`, so every Postgres test would have failed on a clean runner the moment one needed a table — invisible until M1.5 added such tests (`eed6be9`). CI: ruff + mypy + pytest **on push**, service postgres thật, `LLM_MODE=replay` (không cần API key) + gitleaks | §11.2 |
| 0.7 | ✅ **Done 2026-07-26.** `CLAUDE.md` rewritten in English 2026-07-27 and given the authoring-language rule (`4a35802`): docs/comments/commits in English, product output stays Vietnamese. `CLAUDE.md` (copy Karpathy guidelines, viết lại 3 bullet cuối) + `Makefile` | §11.1 |

**⚠️ Ba ràng buộc dependency đã xác minh 2026-07-25** (thử cài thật rồi xóa):
- `fastapi <0.137` cap `starlette<0.50`, xung đột `fastmcp 3.x` (cần `starlette>=1.0.1`) → **fastapi
  phải ≥0.140** nếu muốn API và MCP chung process. Không thì MCP chạy process riêng.
- `langfuse` latest là **4.x** nhưng `observability.py` viết cho **SDK v3** → khóa `>=3.15,<4.0.0`.
  Lên 4.x là migration, không phải bump.
- Python hệ thống là 3.9.6, **không đủ** — dùng `python3.13` (đúng bản venv Project A).

> **Bổ sung 0.8 (phát hiện 2026-07-26)**: gate dưới đây đòi "chạy migration" nhưng 0.1–0.7 **không
> có hạng mục nào dựng Alembic** — 0.5 chỉ pin nó như một dependency. Đã bổ sung scaffolding tối
> thiểu: `alembic.ini` (KHÔNG chứa `sqlalchemy.url` — DSN lấy từ `config.py` để tránh nguồn sự thật
> thứ hai) + `migrations/env.py` với `include_object` loại schema `langgraph` và
> `version_table_schema="app"`. ✅ **Both verified 2026-07-27 at M1.6** against 4 real checkpoint
> tables, by a test that drives `alembic revision --autogenerate` for real and is mutation-verified:
> break `include_object` and it goes red. ADR 0002 written.
>
> Cũng phát hiện khi chạy thật: SQLAlchemy đọc scheme để chọn driver, `postgresql://` trần nghĩa là
> **psycopg2** (repo này cài psycopg 3) → Alembic chết với `ModuleNotFoundError`. `config.py` giữ
> `database_url` ở dạng libpq cho `psycopg`/`PostgresSaver`, và có property riêng
> `app_sqlalchemy_url` gắn `+psycopg` cho Alembic. Có test khoá hành vi này.

**Gate M0**: `make dev` dựng được Postgres + chạy migration; `make test` xanh; `make lint` sạch;
CI badge xanh trên push đầu tiên; `pip install -e ".[dev]"` chạy được từ clone sạch.

**Trạng thái gate (cập nhật 2026-07-27)** — **5/5 đạt, M0 đóng**:

| Điều kiện | | Bằng chứng |
|---|:---:|---|
| `make dev` → Postgres + migration | ✅ | 2 schema `app`+`langgraph` tạo được, `vector` ở `public`, `alembic upgrade head` chạy, `alembic_version` nằm ở `app` |
| `make test` xanh | ✅ | 42 passed |
| `make lint` sạch | ✅ | ruff `All checks passed` + mypy `no issues found` |
| `pip install -e` từ clone sạch | ✅ | `git clone` vào thư mục tạm → `uv venv` → `uv pip install -e ".[dev]"` → 42 passed. Không thiếu file nào |
| CI badge xanh trên push đầu | ✅ | Honest version: the **first** run (`30201551409`, 2026-07-26) was **red** — the `gitleaks-action` push-range bug, since replaced by the plain CLI and documented in `ci.yml`. Green from `30202319143` onward. Latest `30233872335` (2026-07-27): **62 passed** with `HITL_RUNS=10` on real Postgres 17 |

**Artefact**: repo có CI xanh — điểm khởi đầu để mọi commit sau đo được.

---

### M1 — Hai ẩn số đáng sợ nhất *(tuần 1)*

*Mục đích: **chỉ** làm hai việc này. Nếu XML sai hoặc PostgresSaver gây khó, biết ở tuần 1 chứ không
phải tuần 5. Đây là milestone dồn rủi ro lên đầu — không làm gì khác.*

| # | Việc | Tham chiếu |
|---|---|---|
| 1.1 | ✅ **ĐẠT gấp bội — 8 file / 8 nhà cung cấp / 3 phiên bản schema** trong `tests/fixtures/real_samples/` (ẩn danh 2026-07-26→27, xem README thư mục đó). Phủ: cả 2 dạng root, cả 3 `PBan` (2.0.0 / 2.0.1 / 2.1.0), GTGT lẫn không-GTGT, 3 hình dạng MST, hóa đơn **7 dòng**, dòng ghi chú `TChat=4`, `CCCDan`, `DLQRCode`. 4 file sau là hóa đơn **người nhà, có sự đồng ý**. **Còn thiếu (không chặn gate)**: chiết khấu ≠0, hóa đơn điều chỉnh/thay thế, nhiều mức thuế trong một tờ, MISA/BKAV | §12 R1 |
| 1.2 | ✅ **Đối chiếu cấu trúc — ĐÃ LÀM 2026-07-26, và giả định cũ SAI 4 chỗ.** Xem bảng "Thực tế vs giả định" ngay dưới bảng này. Vẫn còn phải **kiểm tra NĐ 70/2025/NĐ-CP** (sửa NĐ 123/2020, hiệu lực 01/06/2025) và đối chiếu lại khi có mẫu GTGT | §12 R1 |
| 1.3 | ✅ **Done 2026-07-27** — `src/invoice_agent/extraction/xml_schema.py`. All element paths anchored at `HDon` without `.//`. Sharp `TTKhac['Amount']` key match for VNPT (4 other vendors carry a gross-amount key; a loose match double-counts tax on 4/8). **Mutation results, stated precisely because the first pass overstated them**: positional `ext_bag`, `money('')→Decimal(0)` and `len(found)!=1→<1` each turn their guard red. Field-level `.//` is caught by the *static* guard `test_no_field_path_uses_descendant_search`, **not** by `test_envelope_values_do_not_leak` — `locate_hdon` returns the inner `HDon` and every scope lookup is relative to it, so field-level `.//` cannot reach the envelope at all; only a root-anchored lookup can, and that is what the behavioural test actually catches. Spec `docs/superpowers/specs/2026-07-27-xml-extraction-design.md`. | §10.1 |
| 1.4 | ✅ **Done 2026-07-27** — `src/invoice_agent/extraction/xml_parser.py` & `models.py`. 8/8 real fixtures parse against a hand-transcribed golden table. Sufficiency test proves `InvoiceData` carries every field needed for the measured M1.2 validation rule. Secured with `defusedxml` (measured stdlib entity expansion). **Two holes found by review the same day, both closed** — and both were tests that passed while guarding nothing, the third and fourth instance of the M1.5/1.6 pattern: (a) `test_entity_declarations_rejected` asserted only the exception *type*, so it stayed green with `defusedxml` swapped for stdlib `ET` — the payloads' root is `<r>`, so billion-laughs parsed and then died in `locate_hdon`; it now matches on `"unsafe XML rejected"` and is mutation-verified red. (b) Nothing asserted the six header-level `TTKhac` bags — breaking 2 of 6 `EXT_SCOPES` paths left all 116 tests green, i.e. silent data loss; `test_header_ext_scopes_are_populated_from_real_files` now pins one real key per scope. | §3.2 |
| 1.5 | ✅ **Done 2026-07-27 — 10/10 under real `kill -9`, hardened the same day after review.** A real child process is SIGKILLed and `returncode == -9` is asserted, so a child dying of anything else cannot pass silently. Two defects found in the first cut: the parent polled for *any* checkpoint row, but a run to interrupt writes **3** and the first (`step=-1, source=input`) lands before any node runs — a ~9ms window where the kill preceded `n1_record`; it now polls `checkpoint_writes.channel = '__interrupt__'`. And a `node` column now measures **both** halves of the finding (`n1_record` = 1, `n2_gate` = 2) instead of only the first. **Spike HITL**: graph 3 node + `interrupt()` + `PostgresSaver` ở schema `langgraph`. Chạy → dừng → `kill -9` → khởi động lại → `Command(resume=)` → hoàn tất | §5.2 |
| 1.6 | ✅ **Done 2026-07-27, ADR written the same day** as the rule demands. The guard now drives the real `alembic revision --autogenerate` instead of a copy of the filter — the copy was worthless: with `include_object` replaced by `return True` it stayed **green** while real autogenerate emitted `drop_table` for all 4 checkpoint tables **and** `alembic_version`. Xử luôn **va chạm Alembic**: `include_object` trong `migrations/env.py` loại schema `langgraph` (không có nó, `--autogenerate` sinh `DROP TABLE checkpoints`), `version_table_schema='app'`. **Ghi ADR ngay hôm chạy được** | §2.10 |

#### Thực tế vs giả định — đo trên file thật 2026-07-26

Bảng cấu trúc ở M1.2 bản đầu **viết theo trí nhớ về QĐ-1450 và sai 4 chỗ**. Đây chính là thứ mà
gate "phải có XML thật" tồn tại để bắt. Ghi lại vì nó là nội dung ADR, không phải lỗi cần giấu.

> **Đính chính 2026-07-26 (n=1 → n=4).** Bản viết lúc mới có 1 file khẳng định *"Không có
> `TDiep`, root là `HDon` thẳng"*. **Sai** — đó là kết luận rút từ một mẫu. File Phong Vũ có
> root = **`TDiep`** → `TTChung` + `DLieu` → … → `HDon`. **Cả hai dạng đều tồn tại thật**;
> bảng M1.2 gốc không sai, nó mô tả dạng phong bì. Parser phải **định vị `HDon`**, không được
> giả định nó là root. Bài học chung: đừng tổng quát hoá từ một file — đó là lý do gate ghi
> "2–3 file", không phải "1 file".

| Giả định cũ | Thực tế trên 4 file | Hậu quả nếu không biết |
|---|---|---|
| root luôn là `TDiep` | **Cả hai**: `HDon` (3 file) và `TDiep` (1 file). Con của root tuỳ NCC: `DLHDon`, `MCCQT`, `DSCKS`, `Fkey`, `DLQRCode` | Parser cứng theo một dạng fail hẳn nửa số file |
| có namespace phải xử lý | Phần dữ liệu **không namespace** ở cả 4 file. Chỉ subtree `Signature` mang `xmlns=…xmldsig#` | XPath viết kèm namespace → không khớp node nào |
| dòng hàng luôn có `TSuat`, `DVTinh` | Vắng ở hóa đơn bán hàng (`KHMSHDon=2`); có ở GTGT (`=1`) dưới dạng **chuỗi `'8%'`, `'10%'`** | Model bắt buộc `TSuat` → fail mọi hóa đơn không-GTGT. Parse `'8%'` thành số → `ValueError`. **Mọi field thuế phải Optional** |
| `NMua` có `MST` | Vắng ở hầu hết file. Và "thiếu" có **ba** biểu hiện: `'0'`, element rỗng → `''`, element vắng → `None` | Validator MST ở M2.6 nổ trên hóa đơn bán lẻ |
| MST là "10/13 số" | **3 hình dạng**: `0100000003` (10), `0300000018-042` (**có gạch**, chi nhánh), `050000000000` (**12 số**, hộ kinh doanh) | Validator từ chối 2/3 dạng thật |
| *(không dự đoán)* | **`TTKhac`** — khối `(TTruong, KDLieu, DLieu)` ở `TTChung`, `NBan`, `NMua`, từng dòng hàng, và `TToan`. **Thứ tự con khác nhau giữa NCC**: Viettel `DLieu,KDLieu,TTruong` vs VNPT/Phong Vũ `TTruong,KDLieu,DLieu` | Đọc theo **vị trí** là vỡ. Và xem ngay dưới — đây là phát hiện đắt nhất |
| *(không dự đoán)* | **`TChat=4` là dòng GHI CHÚ**, không phải hàng hoá — chỉ có `THHDVu`, không giá trị tiền | Cộng nó vào số học là sai; đòi `ThTien` trên mọi dòng là crash |
| *(không dự đoán)* | Số tiền có cả `47777` lẫn **`21618409.09000000`** (8 chữ số thập phân) | `float` sai ở đồng cuối. Bắt buộc `Decimal` |
| `THDon` cho biết loại hóa đơn | **Free-text rác** — 6 biến thể gồm `'TT78 AC'` | Chỉ được dùng `KHMSHDon` để nhận loại |
| `HVTNMHang` ≈ `Ten` | **Hai người khác nhau**: `Ten='NGUYỄN THỊ C'` vs `HVTNMHang='LÊ VĂN D'` (chủ tài khoản vs người trực tiếp mua). Chỗ khác nó chứa **email** | Gộp hai trường là mất dữ liệu và sai ngữ nghĩa |
| `PBan` có 2 phiên bản | **Ba**: `2.0.0`, `2.0.1`, `2.1.0` | |

**Bẫy validation — phát hiện đắt nhất, và đã ĐO được:**

```
luật ngây thơ   sum(ThTien) == TgTTTBSo            →  bắn nhầm 4/5 hóa đơn THẬT, HỢP LỆ
luật đúng       sum(ThTien | TChat==1, fallback TTKhac[Amount]) + TgTThue == TgTTTBSo   →  8/8 ✅
```

**Và ở mức DÒNG thì không được so bằng `==`** — đo trên 8 file thật:

| Phép kiểm | Sai lệch max | Kết luận |
|---|---:|---|
| `SLuong × DGia` vs `ThTien` | **0.01** | cần tolerance |
| `ThTien × TSuat` vs `TThue` | **0.48** | thuế làm tròn tới đồng, **lệch cả hai chiều** |
| `Σ ThTien` vs `TgTCThue` | **0** | so `==` được |
| `Σ TThue` vs `TgTThue` | **0** | so `==` được |

Ca cụ thể: `3 × 398181.33 = 1194543.99` nhưng `ThTien = 1194544`. Người bán chia ngược đơn giá từ
thành tiền đã làm tròn. **Chỉ hóa đơn nhiều dòng có đơn giá lẻ mới lộ ra** — generator tự sinh luôn
chia hết vì nó nhân xuôi từ đơn giá.

Luật ngây thơ — đúng thứ nhóm defect "sai số học 6%" (§10.2) đang test — gắn cờ hóa đơn **thật,
hợp lệ, đã được Cục Thuế ký số** là sai số học. Đó là false positive trên hóa đơn sạch, chính là
metric `flag false-positive rate ≤5%`.

Ba thành phần của luật đúng, **không cái nào đoán được**, mỗi cái do một file khác nhau phát hiện:

- **`TChat==1`** — loại dòng ghi chú `TChat=4` (`hokinhdoanh_…`)
- **fallback `TTKhac[Amount]`** — VNPT để `ThTien=0`, tiền thật nằm trong extension (`vnpt_…`)
- **`+ TgTThue`** — hóa đơn GTGT thì `ThTien` là tiền **trước** thuế (`viettelpost_…`, `phongvu_…`)

Đây là cách vòng tuần hoàn giết dự án nếu không có file thật: parser viết theo trí nhớ sẽ chứa
đúng luật ngây thơ; generator cũng sinh XML "đúng chuẩn" khớp luật đó; **300/300 pass**; bug chỉ
lộ khi gặp hóa đơn thật — tức là không bao giờ, vì corpus toàn đồ tự sinh. Report sẽ ghi
`false-positive rate 0%` và con số đó là bịa.

**Chi tiết khác phải mã hoá vào `xml_schema.py`**: `<SLuong />`/`<DGia />`/`<TLCKhau />`/`<STCKhau />`
rỗng dạng self-closing (parse ra `None`, không phải `0`); `TgTTTBChu` =
`'Ba  triệu năm trăm ngàn đồng./.'` — **hai dấu cách**, **"ngàn"** không phải "nghìn", hậu tố `./.`
(§10.3 dùng nó làm mã hoá độc lập thứ hai của tổng tiền → phải normalize trước khi so).
`THTTLTSuat` đứng **đầu** `TToan` ở NCC này, **cuối** ở NCC kia. `PBan` có cả `2.0.0` lẫn `2.1.0`.
Trường có mà plan không liệt kê: `PBan`, `MCCQT`, `Fkey`, `DLQRCode`, `MSTTCGP`, `HDDCKPTQuan`,
`DVTTe`, `TGia`, `HTTToan`, `THDon`, `MHHDVu`, `TThue`, `TgTCThue`, `TgTThue`, `TTCKTMai`, `DSLPhi`,
`HDCTTChinh`, `MKHang`, `DCTDTu`, `HVTNMHang`, `STKNHang`, `TNHang`, `Fax`, `Website`.

**Dữ liệu rác CÓ THẬT** — validator đòi "hợp lệ" sẽ gắn cờ oan: `NMua/Ten = '.'`,
`NMua/STKNHang = '.'`, `HVTNMHang` chứa **email** thay vì họ tên, `NMua/DChi` =
`'Người mua không lấy hóa đơn'`, và một file **không có `NMua/Ten`** nào cả.

**Câu hỏi mở cho M2.6**: MST 12 số của hộ kinh doanh sinh từ CCCD — chưa rõ có quy tắc checksum
công khai áp dụng được không. Đừng cho validator từ chối nó cho tới khi trả lời được.

**⚠️ Hóa đơn thật mang PII** (tên, email, mã khách hàng, MST, số tài khoản) **và chứng thư số thật**.
gitleaks **không bắt được** — nó tìm token, không tìm PII. `.gitignore` chặn `*.xml` và `*.pdf`
(**không neo root** — neo cũ `/*.xml` chính là chỗ đã thủng ở `bb8718d`), với ngoại lệ
`!tests/**/*.xml`; chỉ bản **đã ẩn danh** trong `tests/fixtures/real_samples/` mới được commit.
Nguyên tắc ẩn danh: **giữ cấu trúc, thay giá trị** — kiểm chứng bằng cách so cây tag gốc vs ẩn danh
phải giống hệt.

> **Redacted 2026-07-28 (M1 review).** The MST row of the table above carried `0104093672`,
> `0304998358-042` and `052098014618` — the **real, pre-anonymisation** tax codes, confirmed by the
> user during this review. None of the three appears in `tests/fixtures/real_samples/`, which is how
> they were caught: `c97da4e` had already swapped them out of the fixtures and source comments but
> deliberately left this doc for a human decision. They are now the corpus's own anonymised values.
> The row's point was always the three *shapes* — 10-digit, dash-suffixed branch, 12-digit household
> — so nothing measured is lost. **Still in git history on origin**; purging it is a separate,
> unmade decision. The lesson generalises: PII leaks into *prose about* the data as readily as into
> the data, and no automated layer here looks at prose.

**Gate M1**: `test_hitl_survives_restart.py` **xanh** (10/10 lần); 2–3 XML thật parse ra
`InvoiceData` đúng; `alembic revision --autogenerate` trên schema đã có checkpoint **không** sinh ra
lệnh DROP nào.
**Artefact**: **terminal recording** — graph dừng ở `interrupt()`, process bị `kill -9`, khởi động
lại, resume, post xong. *Riêng cái này đã hơn phần lớn portfolio project.* Cộng ADR
`0001-three-graphs-not-one.md` + `0002-alembic-langgraph-schema-isolation.md`.

**Gate status (2026-07-27)** — 4/4, M1 gate fully closed:

| Condition | | Evidence |
|---|:---:|---|
| `test_hitl_survives_restart.py` green 10/10 | ✅ | CI run `30233872335`, `HITL_RUNS=10` on a clean runner. Locally 40/40 in 20s while hunting for flakiness |
| No repeated side effect | ✅ | Per-node counts across the process boundary: `n1_record` = 1, `n2_gate` = 2. The second number is the measurement of *why* `post_entry` cannot share a node with the gate |
| `--autogenerate` emits no DROP | ✅ | `test_alembic_ignores_langgraph.py` runs the real command and is mutation-verified: break `include_object` → red |
| 2–3 real XML parse into `InvoiceData` | ✅ | `test_xml_parser.py`: 8/8 real fixtures parse into `InvoiceData`. Sufficiency test confirms model contains all inputs needed for measured M1.2 validation rule |

Artefacts still owed: the terminal recording (deferred to M8 by the M1.5 spec — the tests prove it
*can* be recorded) and ADR `0001-three-graphs-not-one.md`.

> **Nếu không tìm được XML thật trong 2 ngày**: escalate — hỏi cộng đồng dev VN (AI Vietnam FB
> group), hoặc xin 1 file ẩn danh từ kế toán quen. **Không được bắt đầu M2 nếu chưa có.** Viết
> parser theo trí nhớ về element name là cách chắc chắn nhất để hỏng ở tuần 4.

---

### M2 — Corpus + schema + lõi deterministic *(tuần 2)*

> **Sub-milestones status (2026-07-28)**:
> - ✅ **M2-A (Schema, Seed, Repositories)** done 2026-07-28. Migrations 01–09, `seed_reference.py`, repositories (`vendors`, `batches`, `invoices`, `journal`, `flags`, `budget`, `audit_log`, `episodic`, `llm_calls`), 3 CHECK constraints verified. Spec `docs/superpowers/specs/2026-07-28-m2a-schema-seed-repositories-design.md`.
> - ✅ **M2-B (Domain Core)** done 2026-07-28. `src/invoice_agent/domain/` (6 pure modules, 0 I/O), 106 domain unit tests passing, 8/8 real invoices 0 false positives, 10/10 mutation guards verified red, journal entry credit balancing spec corrected. Note `docs/notes/2026-07-28-m2b-domain-core.md`.
> - ✅ **M2-C (Corpus Generator)** done 2026-07-28. `scripts/generate_invoices.py --count 300 --seed 42 --out corpus`, `scripts/generator/` (6 modules + 2 Jinja templates), 67 generator tests passing, strict independence guard asserted (0 parser/domain imports). Note `docs/notes/2026-07-28-m2c-corpus-generator.md`.

| # | Việc | Tham chiếu |
|---|---|---|
| 2.1 | ✅ **Done 2026-07-28 (M2-C)**. `scripts/generate_invoices.py --count 300 --seed 42` — Jinja template (`hdon.xml.j2` + `tdiep.xml.j2`), **viết độc lập với `xml_schema.py`** | §10.2 |
| 2.2 | ✅ **Done 2026-07-28 (M2-C)**. Đủ **14 nhóm defect** theo tỉ lệ §10.2. `defects` là **LIST** không phải enum (hóa đơn thật mang nhiều vấn đề cùng lúc) | §10.2 |
| 2.3 | ✅ **Done 2026-07-28 (M2-C)**. **Ca vượt ngân sách phụ thuộc thứ tự**: 3 hóa đơn Marketing (canonical order breaches C; naive arrival walk breaches any of 3; invoice number order reverse of date order). Manifest ghi `canonical_budget_rank` | §10.2 |
| 2.4 | ✅ **Done 2026-07-28 (M2-A)**. Alembic migrations 01–09: mọi bảng + `UNIQUE(seller_tax_code, template_no, invoice_series, invoice_number)` + `ck_flags_severity`, `ck_flags_raised_by`, `ck_journal_balanced` + `REVOKE UPDATE,DELETE ON audit_log` | §2.3, §2.7 |
| 2.5 | ✅ **Done 2026-07-28 (M2-A)**. Seed reference data với **số hiệu tài khoản TT133 thật** (`6421`, `6422`, `1331`, `331`, `156`, `153`) + `keywords_vi` (= baseline eval) | §2.1 |
| 2.6 | ✅ **Done 2026-07-28 (M2-B)**. Toàn bộ `domain/` — pure function, zero I/O: `validation.py`, `taxcode.py` (checksum MST 10/13/12 số), `money.py` (Decimal VND + số tiền bằng chữ ↔ số), `accounting.py` (JE builder + balance check), `budget.py`, `anomaly.py`. 106 unit tests green. | §3.2 |
| 2.7 | ✅ **Done 2026-07-28 (M2-A)**. Repositories + `audit_log` writer | §2 |

**Gate M2 status (2026-07-28)** — 5/5, Milestone 2 fully closed:
- `make generate && make seed` clean.
- ≥60 unit tests `domain/` green (107 domain unit tests + 67 generator tests, 289 total passing non-DB unit tests).
- Round-trip: **285/285 XML parsed into `InvoiceData`, 15 PDF-only entries declared unparseable-by-design, 300 accounted for** (plus 8/8 real fixtures passing).
- `INSERT` unbalanced Journal Entry → rejected by `ck_journal_balanced`.
- `UPDATE audit_log` → rejected by SQL trigger & RLS rules.


---

### M3 — Extraction pipeline: vòng leo thang, tools, cost, replay *(tuần 3)*

> **Sub-milestones status (2026-07-29)**:
> - ✅ **M3-A (LLM Plumbing)** done 2026-07-29. `LLMCallRecord`, `calls` out-param, `telemetry.py` recording to `llm_calls` table, `LLM_MODE=replay` with `sha256` key cache & 64-bit verification. Spec `docs/superpowers/specs/2026-07-29-m3a-llm-plumbing-design.md`, Note `docs/notes/2026-07-29-m3a-llm-plumbing.md`.
> - ✅ **M3-B (Vision Surface)** done 2026-07-29. `call_structured()` self-correction retry loop in `structured.py`, `sanitize.py` (`escape` + `wrap`), `vision.py` (`rasterize` PDF to PNG at 150 DPI), `vision_model_chain` config. Spec `docs/superpowers/specs/2026-07-29-m3b-vision-surface-design.md`, Note `docs/notes/2026-07-29-m3b-vision-surface.md`.
> - ✅ **M3-C (Enrichment Graph)** done 2026-07-29. `src/invoice_agent/serde.py` msgpack allowlist, `src/invoice_agent/graphs/enrichment.py` (state, 5 nodes, `route_after_validate` extraction-vs-data error router), `scripts/render_graph.py` + `docs/diagrams/enrichment_graph.mmd` drift guard, `scripts/run_one.py` end-to-end runner & `llm_calls.invoice_id` backfill. Spec `docs/superpowers/specs/2026-07-29-m3c-enrichment-graph-design.md`, Note `docs/notes/2026-07-29-m3c-enrichment-graph.md`.

| # | Việc | Tham chiếu |
|---|---|---|
| 3.1 | `tools/registry.py` — **moved to M7** (spec M3-A §3.3). Reason: `ToolSpec` has **no consumer inside the M3 gate** — the gate names cost, the reducer trap, escalation and replay, none of which route through a tool. Its real consumers are M7 (`mcp_exposed`, plus `test_no_write_tools_in_mcp.py`, one of the six portfolio tests) and M4's memory reads. What M3 **does** need from item 3.2 is the retry taxonomy → `llm/structured.py` in M3-B | §8.1 |
| 3.2 | ✅ **Done 2026-07-29 (M3-B)**. `src/invoice_agent/llm/structured.py` — `call_structured()` self-correction retry loop for schema validation errors, sanitizing error feedback prompts to defeat model-echoed injection attacks | §8.1 |
| 3.3 | ✅ **Done 2026-07-29 (M3-B)**. `src/invoice_agent/extraction/sanitize.py` — `escape()` (`&` before `<` and `>`) and `wrap()` fencing untrusted invoice text in `<invoice_data>` blocks | §9.2 |
| 3.4 | ✅ **Done 2026-07-29 (M3-B)**. `src/invoice_agent/extraction/vision.py` — `rasterize()` PDF to PNG (`RASTER_DPI = 150`), `extract()` via `call_structured()`, `vision_model_chain` in `config.py` | §3.2 |
| 3.5 | ✅ **Done 2026-07-29 (M3-C)**. `src/invoice_agent/graphs/enrichment.py` — `EnrichmentState`, 5 nodes, **`route_after_validate` phân biệt lỗi EXTRACTION vs lỗi DATA** (XML findings end; vision findings retry; XML parsing failure climbs to vision if `pdf_path` twin exists). | §3.3 |
| 3.6 | ✅ **Done 2026-07-29 (M3-A)**. **Cost capture**: `calls` out-param trên gateway (`LLMCallRecord` list) → `llm/telemetry.py` → `llm_calls`. Ghi cả lần gọi thất bại + `shadow_cost_usd` | §2.9 |
| 3.7 | ✅ **Done 2026-07-29 (M3-A)**. **`LLM_MODE=replay`** — `llm/cache.py` fixture cache khóa theo `sha256(model, messages, params)` với 64-bit key verification trên `load()` | §11.4 |

**Gate M3 status (2026-07-29)** — 4/4, Milestone 3 closed **after an independent exit review the
same day**. The review found the closing commit `097388b` was **RED** — `make check` failed with 4
ruff errors and the replay gate reported **2 failed, 453 passed**, with both fixes sitting
uncommitted in the working tree. One of those failures,
`test_run_one_links_cost_to_the_invoice`, passed **exactly once per database**: a fixed
`invoice_number` plus `ON CONFLICT DO NOTHING … RETURNING` returns `None` on the second run. All
fixed and re-measured at **455 passed** both ways; the evidence below is the post-review state.
Details in the note's §7.
- **Cost linking**: `scripts/run_one.py` runs one invoice end to end, records cost rows inline during `vision_extract` via `_persist_cost` (`thread_id="enrich:{invoice_id}"`), and backfills `llm_calls.invoice_id` upon database insertion (`tests/test_run_one.py` green).
- **Reducer trap guard**: `test_error_history_grows_LINEARLY_not_quadratically` in `tests/test_enrichment_graph.py` verifies 3 vision retries leave exactly 3 `error_history` entries (catching `operator.add` list returning trap).
- **Escalation & fallback**: Unparseable XML escalates to `vision_extract` when `pdf_path` twin exists (`TestExtractionFailed`), vision findings retry up to `MAX_VISION_ATTEMPTS = 3`, and broken PDF reaches `manual_queue` without invoking model (`test_a_truncated_pdf_reaches_manual_queue_without_spending_a_token`).
- **Replay hermeticity**: Entire test suite passes under `LLM_MODE=replay` with no API keys (`455 passed in 13.85s`).
- **Artefacts**: Committed Mermaid graph `docs/diagrams/enrichment_graph.mmd` + automated drift test `test_the_committed_diagram_matches_the_graph`.
- **M3 Owed Debts**: vision fixture & the first real accuracy number — the vision branch currently has **no accuracy measurement of any kind**, synthetic or real, and no vision-capable API key is configured (spec M3-B §8.1, M3-C §8.1); **git history purge of the six PII-carrying vendor PDFs** (real names, tax codes, QR lookup codes — a *privacy* debt, not a file-size one) alongside the M1 MST leak; ADR `0001-three-graphs-not-one.md`. Also open for M4: `thread_id` is `enrich:{invoice_id}` and therefore stable across re-runs, so batch needs a per-run component (M3-C §8.5).


---

### M4 — Multi-agent + memory + chứng minh serialization *(tuần 4)*

| # | Việc | Tham chiếu |
|---|---|---|
| 4-A | ✅ **Done 2026-07-31.** Corpus & reference alignment — generator departments derived from seed (fixes 194/300 unjoinable FK failure), `canonical_budget_rank` in canonical order, category-first generation (`TIER_MIX` tuned baseline 57.2%, **57.9% after M4-B regenerated the corpus with relative outlier amounts**), alternative categories, and declared 30-invoice gate batch (`gate_batch.py` + `gate_batch.json`). Spec `2026-07-31-m4a-corpus-reference-alignment-design.md`, plan `2026-07-31-m4a-corpus-reference-alignment.md`. | §1.1 |
| 4.1 | **Correction:** `exclude_model=model_used` parameter was NOT implemented. Reason: the parameter never existed, and name-based exclusion cannot deliver a different family (e.g., Llama 3.3 70B appears twice in DEFAULT_MODEL_CHAIN under Groq and OpenRouter). Replaced with declared disjoint model family chains (`auditor_model_chain` and `families.py`). | §6.3 |
| 4.2 | ✅ **Done 2026-08-01 (M4-C).** Disagreement -> `CLASSIFICATION_DISPUTED` flag (`warn` severity, `agent_disagreement` source, Alembic migration 12). `dispute()` carries both opinions and reasons to reviewer. | §6.4 |
| 4-C | ✅ **Done 2026-08-01.** ClassificationAgent + AuditorAgent — `src/invoice_agent/agents/` (`models.py`, `families.py`, `classification.py`, `audit.py`), schema boundary audit (`tests/test_agent_schema_boundary.py`, item 6.4 pulled forward), graph integration `validate -> classify -> audit -> signals -> END`. Evaluator `scripts/eval_classification.py` written. Precondition block recorded in `docs/notes/2026-07-31-m4c-agents.md`: 0 provider keys available in environment, so live measurement paused without stubbing or fabricating numbers. Spec `2026-07-31-m4c-agents-design.md`, plan `2026-07-31-m4c-agents.md`. | §6.2, §6.3 |
| 4-B | ✅ **Done 2026-07-31.** Vendor memory + `signals` — `memory/vendor_profile.py` (exact `(count, total, sum_sq)` accumulator, avg/stddev derived), `rebuild_vendor_profiles.py --check` (zero drift), `signals_node` in the enrichment graph. `PRICE_OUTLIER` went from **0/6 to 6/6 at a 0.7% false-positive rate**. Item 4.4 moved to M5. Spec `2026-07-31-m4b-vendor-memory-signals-design.md`, plan `2026-07-31-m4b-vendor-memory-signals.md`, note `docs/notes/2026-07-31-m4b-vendor-memory.md`. | §2.2, §3.2, §7 |
| 4.3 | ✅ **Done 2026-07-31 (M4-B).** `memory/vendor_profile.py` — upsert **transactional** + z-score price outlier + `rebuild_vendor_profiles.py` (lối thoát khi profile lệch) | §2.2, §7 |
| 4.4 | **Moved to M5** (spec M4-B §1.2). `episodic_decisions` is written by design §5.4's `learn` node and read by §5.1's `suggest` node — both M5 — and had no caller anywhere outside one repository test. Tuning amount-bucket width and flag-overlap ordering against hand-seeded fixtures would have produced numbers that measure the fixtures. Same reasoning that moved item 3.1 to M7 | §7.1 |
| 4.5 | ✅ **Done 2026-08-01 (M4-D).** `domain/anomaly.py` + `serial_join` **SERIAL**, thứ tự canonical `(issue_date, invoice_series, invoice_number)`, ghi `budget_consumption` với `balance_before/after/sequence_no` | §4.3, §2.6 |
| 4.6 | ✅ **Done 2026-08-01 (M4-D).** `graphs/batch.py` — `Send` fan-out, `enrich_one` là **wrapper function node** (map state tường minh + `try/except` mỗi task), `thread_id_for(batch_id, invoice_id)` | §4.1, §4.2 |
| 4-D | ✅ **Done 2026-08-01.** Batch graph + serial reconciliation — `src/invoice_agent/graphs/batch.py`, `tests/test_batch_graph.py`, `tests/test_budget_serialization.py`. Portfolio test 3 passed (10 shuffles inside SAVEPOINT rollbacks produce byte-identical balance chain 35M -> 15M -> -5M and breach INV-0001). 30-invoice gate batch end-to-end replay verified in `LLM_MODE=replay`. ADRs 0001, 0003, 0004 created. Spec `2026-08-01-m4d-batch-graph-design.md`, plan `2026-08-01-m4d-batch-graph.md`, note `docs/notes/2026-08-01-m4d-batch-graph.md`. | §4.1–§4.6 |

**Gate M4**: exact agreement between the declared gate batch
(`scripts/generator/gate_batch.py`, 30 invoices) and what the batch actually
flags — the auto/flag split is reported, not targeted, because the corpus's
finding rate is 28% and `BUDGET_BREACH` is a property of the batch rather than
of the invoice; **`test_budget_serialization.py` green** — shuffle input order
10 times, `balance_after` chain byte-identical and the same invoice breaches;
one corrupt file lands in `failed` without killing the batch; Auditor override
precision measured and reported however it scores (below 50% is net harmful and
gets said).
**Artefact**: **log bất đồng Classifier vs Auditor** — tình huống multi-agent bất đồng thật, không
dàn dựng. Cộng ADR `0003-no-supervisor-agent.md` + `0004-serial-budget-reconciliation.md`.

#### Gate M4 — measured 2026-08-01, replayed with every API key unset

| Criterion | | Evidence |
|---|:---:|---|
| Corrupt files land in `failed`, batch survives | ✅ | `INV-0122`/`INV-0123` in `failed`; the other **28 enriched**, **27 persisted** (1 exact duplicate) |
| MKT budget chain, canonical order | ✅ | `INV-0003 → 35,000,000`, `INV-0002 → 15,000,000`, `INV-0001 → −5,000,000`; **`INV-0001` breaches**, exactly as `corpus/gate_batch.json` declares |
| `test_budget_serialization.py` | ✅ | 10 shuffles, byte-identical chain, same breacher — **mutation-verified**: remove the canonical sort and it goes red |
| Auto/flagged split (reported, not targeted) | ✅ | **23 auto / 7 needs-approval** |
| Duplicate detection | ✅ | 1 `DUPLICATE_INVOICE`, raised by the `UNIQUE` natural key |
| Full gate under `LLM_MODE=replay`, no keys | ✅ | Whole suite green with `GROQ`/`GEMINI`/`OPENROUTER` all unset |
| Classification beats `keywords_vi` by ≥15 | ✅ | **95.4% vs 57.9%** (M4-C), +37.5 points |
| Auditor override precision measured & reported | ✅ | **12.5%** dispute precision (CI 0.043–0.310, n=24) — **below §6.4's 50% net-harm line, and reported as such** |
| ADRs 0001 / 0003 / 0004 | ✅ | Written; 0003 cites the 12.5% measurement rather than an argument |
| `make check` green on the pushed commit, CI agreeing | ✅ | Achieved only after the exit review; see the scoreboard in §1 |

> **What the gate does NOT establish, stated because "exact agreement" overclaims
> otherwise.** `corpus/gate_batch.json` declares the **MKT chain only**. The batch
> raised **9 `BUDGET_BREACH` flags**: `INV-0001` as declared, plus 8 in other
> departments whose budgets the batch's remaining 27 invoices also draw down.
> Those 8 are correct behaviour and undeclared — M4-A's D5 (budget breach is a
> property of the batch, not the invoice) is only half-resolved, because the
> declaration was built to pin MKT and never extended to the rest. **M6 owes
> either a full per-department declaration or an explicit statement that only the
> MKT chain is pinned.**
>
> ✅ **PAID 2026-08-01 by M5-A, and it exposed a second defect.** `gate_batch.json`
> now carries `balance_chains` — one declared chain per department in the batch,
> with limits imported from `scripts.seed_reference.BUDGETS` rather than restated,
> so all 9 `BUDGET_BREACH` flags are declared instead of 1 of 9. Writing the
> declaration is what made the next finding visible: those 8 breaches were also
> being **routed to the auto path**, because `route_outcomes` read a fan-out
> task's in-memory findings and budget findings are computed after the fan-out.
> The guard refused all 8, so nothing unsafe happened — but the routing node was
> blind to the whole budget family, and each batch logged 8 refusals that looked
> like safety events. Fixed in M5-A: routing reads `invoice_flags` with the same
> predicate as the guard and the trigger.

---

### M5 — HITL production + API + UI bảo hiểm *(tuần 5)*

> **Sub-milestones (2026-08-01)**. M5 spans four subsystems, so it is split the way M2–M4 were:
> - ✅ **M5-A (write guard + auto path)** done 2026-08-01. `tools/guards.py`, migration 13, the three
>   persistence gaps, `auto_post_node`, per-department `balance_chains`. Spec
>   `docs/superpowers/specs/2026-08-01-m5a-write-guard-auto-post-design.md`, plan
>   `docs/superpowers/plans/2026-08-01-m5a-write-guard-auto-post.md`, note
>   `docs/notes/2026-08-01-m5a-write-guard.md`.
> - ✅ **M5-B (approval graph + HITL loop)** done 2026-08-01. `graphs/approval.py` (7 nodes +
>   `submit_decision`), `open_approval` in the batch graph, `scripts/recover_pending.py`,
>   `learn` closing the HITL→memory loop. Portfolio test 1 **retargeted onto the real graph**
>   (10/10 at `HITL_RUNS=10`) and portfolio test 4 green. **`max_concurrency` debt PAID.**
>   Spec `2026-08-01-m5b-approval-graph-design.md`, plan `2026-08-01-m5b-approval-graph.md`,
>   note `docs/notes/2026-08-01-m5b-approval-graph.md`.
> - ✅ **M5-C (FastAPI)** done 2026-08-01. `api/app.py`, `queries.py`, `progress.py`, `schemas.py`, routes (`health`, `invoices`, `reports`, `approvals`, `batches`). Security boundary `DecisionRequest` extra='forbid', 4 structural mutation tests verified red, live curl transcript in `docs/notes/2026-08-01-m5c-fastapi.md`. ✅ **M5-D (Streamlit)** done 2026-08-01. `ui/client.py` (mọi HTTP call, có seam
>   `httpx.Client` để test chạy qua `TestClient` in-process) + `ui/app.py` (chỉ layout, bốn trang
>   tiếng Việt). 3/3 mutation đỏ. `make demo` chạy cả API lẫn UI. Spec
>   `2026-08-01-m5d-streamlit-design.md`, plan `2026-08-01-m5d-streamlit.md`, note
>   `docs/notes/2026-08-01-m5d-streamlit.md`. **M5 đóng.**

| # | Việc | Tham chiếu |
|---|---|---|
| 5.1 | ✅ **Done 2026-08-01 (M5-B).** `graphs/approval.py` — all 7 nodes. `approval_gate` holds `interrupt()` and nothing else (M1.5 measured that node runs TWICE across resume); `suggest` calls no LLM and returns `[]` on a fresh database, reported as measured. `ApprovalState` is plain JSON so a checkpoint survives a deploy landing between the interrupt and the decision | §5.1 |
| 5.2 | ✅ **Done 2026-08-01 (M5-B).** 12 of the gate batch's 15 `needs_approval` get a thread; the other 3 have no invoice row (NOT NULL FK) and are reported in `unopened`. `open_approval` trong batch_graph: ghi 1 row `app.approvals` → `approval_graph.ainvoke(..., config={"configurable": {"thread_id": f"appr:{invoice_id}"}})` chạy tới `interrupt()` → kiểm tra key `__interrupt__` trong kết quả → **trả về ngay** | §5.2 |
| 5.3 | ✅ **Done 2026-08-01 (M5-A).** `tools/guards.py` — `create_journal_entry` re-reads `approvals` from Postgres (and compares its `invoice_id`, without which one approved invoice authorises posting any other), re-reads `invoice_flags` for the `auto_clean` path, and is idempotent so a `kill -9` resume completes instead of double-posting. Every refusal writes `audit_log` **on its own connection**: the guard runs inside the caller's transaction, so a denial's audit row would otherwise roll back with the transaction it aborted — design §5.3's "every refusal writes `audit_log`" would have been silently false. `edited` counts as approved. Three gaps closed alongside it, all found by reading the code: `invoice_flags` received only duplicate and budget rows, so the trigger fired on a nearly empty table; `category`/`department` were never persisted; `set_status` had zero callers | §5.3 |
| 5.4 | ✅ **Done 2026-08-01 (M5-B).** Records an episode on EVERY decision, not only rejections — `suggest` asks what happened last time for this vendor, and a memory of refusals only would bias every suggestion. `learn` — lý do reject → `episodic_decisions`; `field_corrections` → `vendors.reviewer_notes` (cái `classify` đọc lần sau) | §5.4 |
| 5.5 | ✅ **Done 2026-08-01 (M5-B).** Reports **orphans** separately — an undecided row with no live interrupt is an invoice a human waits for forever. Tested from a cold process. `scripts/recover_pending.py` — quét `approvals WHERE decision IS NULL`, với mỗi `thread_id` gọi `get_state` xác nhận còn interrupt treo. **API không bao giờ giả định registry trong RAM sống sót** | §5.5 |
| 5.6 | ✅ **Done 2026-08-01 (M5-C).** FastAPI routes **tách thật**: `health` (pings DB), `batches` (202 async + SSE progress), `invoices`, `approvals` (submit_decision wrapper, no guard/journal import allowed), `reports`. Security boundary `DecisionRequest` extra='forbid'. All 4 structural mutations verified red. | §11.2 |
| 5.7 | ✅ **Done 2026-08-01 (M5-D).** **Streamlit approval UI tối giản** — upload, hàng chờ duyệt, chi tiết, approve/reject. HTTP-only: `ui/` imports nothing from `invoice_agent` ngoài `config`, có source guard chặn — nếu import thẳng `submit_decision` thì consumer duy nhất của M5-C đi vòng qua API. Copy tiếng Việt, lỗi API hiện thành thông báo tiếng Việt chứ không phải traceback | Quyết định §0 |

**Gate M5**: `test_never_post_without_approval.py` xanh; **`test_batch_not_blocked_by_pending.py`
xanh** (26 sạch post được trong khi 4 đang chờ — đây là test biện minh cho cả thiết kế 3 graph);
duyệt được qua `curl`; `kill -9` giữa lúc còn pending → restart → `recover_pending.py` → vẫn duyệt
được; feedback của reviewer thật sự xuất hiện trong `vendors.reviewer_notes`.
**Artefact**: demo Streamlit chạy được — từ đây trở đi **luôn có demo**, không phụ thuộc Next.js.

---

### M6 — Security + observability + eval *(tuần 6)*

| # | Việc | Tham chiếu |
|---|---|---|
| 6.1 | ✅ **Done 2026-08-02 (M6-A)**. Corpus injection: **6 payload gốc × 4 placement (24) + 6 reasoning cases = 30 case total** (roadmap §2.2 estimated 26 before seller_name and reasoning surfaces were known, spec §1.3). | §9.1 |
| 6.2 | ⚠️ **Done 2026-08-02 (M6-A), gate NOT met — reported, not hidden.** **Negative control**: 24 carrier/twin pairs under real `groq/llama-3.3-70b-versatile` output. **22/24 identical; 2/24 are measured injection successes**, both `category_laundering` (`first_line`, `note_line`): the twin is a strict prefix of the carrier, so the instruction alone moved `MARKETING → OFFICE_SUPPLIES`. Design §9.2 predicts exactly this and the deterministic `BUDGET_BREACH` contains it. **The first cut of this row claimed 18/24 with 6 shifts — the control used a fixed text per placement, so it measured subject matter, and its allowlist named four non-events while missing both real successes.** Note §3. | §9.3 |
| 6.3 | ✅ **Done 2026-08-02 (M6-A)**. `test_prompt_injection.py` **CI-blocking**. 24 carriers through the graph: **0 decisions set, 0 journal entries posted**; 6 reasoning-channel cases likewise. `extra="forbid"` rejects unknown fields; `BUDGET_BREACH` fires while the model returns the laundered category. **3 of 4 mutations bite this suite** (A, B-after-fix, C); D is covered by `test_never_post_without_approval.py`. The first cut claimed 4/4 and cited a test that does not exist — note §4. | §9 |
| 6.3b | ⚠️ **Two gaps found 2026-08-02 (M6-A), open — and one retracted claim.** **Retracted:** an earlier row here claimed the LLM selects which budget a flag is computed against. It does not — `serial_join` groups on the **task-declared** department and `InvoiceData` has no `department`, so the classifier's value never reaches the grouping. The supporting test set the department by hand and measured nothing about the model (note §6.1). **Still open, and not injection issues:** (a) an invoice with **no declared department gets no budget check at all**, silently — `unroutable` writes no flag, so `route_outcomes` does not force it to a human either, and `api/routes/batches.py` makes `department` an optional field applied to a whole upload; (b) `invoices.department` is filled from the classifier when the task declares none (`batch.py:224`), so the stored department can disagree with the one the budget used. Pinned by `test_an_unroutable_invoice_raises_a_flag_a_reviewer_can_see` (`xfail(strict=True)`). | §9.2 |
| 6.4 | ✅ **Done 2026-08-01 (M4-C).** Pulled forward to M4-C Task 1 — **Schema audit test** (`tests/test_agent_schema_boundary.py`): asserts no agent response model carries approval/flag/status fields (`extra="forbid"` enforced). | §9.2 |
| 6.5 | LangFuse `@observe` mọi node, tag `invoice_id`/`batch_id`/`node`/`agent`, cost + shadow cost làm metadata | §2.9 |
| 6.6 | Cost cap: batch 10.000 file bị **từ chối trước khi gọi LLM lần nào**; per-invoice token budget vượt → manual queue | §9.2 |
| 6.7 | Toàn bộ eval stage + `scripts/eval.py` → `docs/eval-report.md` | §10.4 |
| 6.8 | **Tự bấm giờ nhập tay 10 hóa đơn vào Excel** rồi ngoại suy — đừng trích báo cáo thị trường cho con số của chính mình | §10.4 |

**Gate M6**: no execution channel **24/24 + 6 reasoning** (0 decisions, 0 entries — reconciled from
"0/26", raised because §9.1 missed the `seller_name` and reasoning surfaces); **negative control
100% — ❌ NOT MET, measured 22/24** (compares `category` **and** `department`), the two failures being
real `category_laundering` successes; **plus two open gaps that are missing controls rather than
injection issues — an undeclared department yields no budget check at all, silently, and
`invoices.department` is filled from the classifier (item 6.3b)**; schema audit xanh; 3 of 4
mutations bite the injection suite; cost cap 1/1;
`make eval` sinh report có **mọi** số ở bảng §10.4, trong đó classification **thắng baseline
`keywords_vi` ≥15 điểm** (không thắng → ghi lại và cân nhắc bỏ LLM ở node đó, đúng nguyên tắc 3).
**Artefact**: `docs/eval-report.md` + ADR `0005-structured-output-as-security-boundary.md`.
**Báo cáo trung thực** — công bố cả thất bại. *"Auditor override precision 0.52 — chỉ vừa đủ có lợi,
tôi giữ vì nó tập trung ở nhóm giá trị cao, đây là breakdown"* ấn tượng hơn hẳn 0.98 khắp nơi.

---

### M7 — MCP + Next.js *(tuần 7)*

| # | Việc | Tham chiếu |
|---|---|---|
| 7.1 | FastMCP server **sinh từ `tools/registry.py`**: 7 read tool + 5 resource (`invoice://`, `invoice-xml://`, `budget://`, `vendor://`, `report://`) | §8.2 |
| 7.2 | `submit_approval_decision` — write **do người ủy quyền**, gate bằng `MCP_ALLOW_APPROVALS` + actor token, audit log ghi `actor='mcp:<client>'` | §8.2 |
| 7.3 | **`test_no_write_tools_in_mcp.py`** — mọi `ToolSpec` có `side_effect=="write"` và `mcp_exposed` phải nằm trong allowlist tường minh | §8.2 |
| 7.4 | Config Claude Desktop + quay session hỏi thật: *"phòng nào vượt ngân sách tháng 7?"* | §8.2 |
| 7.5 | Next.js: upload, batch progress (SSE), approval queue, detail split-view PDF/field, reports. **Time-box 4 ngày** | Quyết định §0 |
| 7.6 | pgvector cho episodic `reason_embedding` — **chỉ nếu còn giờ** | §7.1 |

**Gate M7**: Claude Desktop query được database hóa đơn qua MCP; `test_no_write_tools_in_mcp.py`
xanh; Next.js làm được vòng approve/reject đầy đủ **hoặc** đã quyết định dừng và giữ Streamlit.
**Artefact**: recording Claude Desktop truy vấn kho hóa đơn — *"interrupt là transport-agnostic"*.

> **Quy tắc dừng**: hết ngày thứ 4 mà Next.js chưa làm xong vòng approve/reject → **dừng, giữ
> Streamlit**, chuyển sang M8. Next.js là rủi ro lịch trình lớn nhất mà **không chứng minh kỹ năng
> nào trong 7 kỹ năng GĐ5**. Một UI xấu trên agent đúng thắng một UI đẹp trên agent làm dở.

---

### M8 — Mặt tiền portfolio *(tuần 8)*

| # | Việc | Chi tiết |
|---|---|---|
| 8.1 | `ReportingAgent` + `/reports` — biểu đồ + nhận xét tiếng Việt | |
| 8.2 | **README final**: architecture diagram, **bản đồ 7 kỹ năng GĐ5**, bảng eval với số thật, câu "safety property ba lớp", link demo | §14 |
| 8.3 | **5 ADR hoàn chỉnh** — đây là **kịch bản phỏng vấn**, không phải giấy tờ | M1/M4/M6 |
| 8.4 | **Video demo 3 phút** (tiếng Việt, phụ đề Anh) theo `docs/demo-script.md` | |
| 8.5 | Deploy public + load test 100 hóa đơn | |
| 8.6 | Bài blog kỹ thuật — khớp lịch C8–C9 roadmap tổng | |

**Gate M8 (= Definition of Done)**: người lạ mở README hiểu dự án trong 5 phút; bấm URL dùng được
ngay; **mọi con số trong README trace được về `docs/eval-report.md` trong repo**.

---

## 4. Những thứ cố tình KHÔNG làm

- **Supervisor agent** — thứ tự pipeline là bất biến nghiệp vụ (§6.1). ADR giải thích thay thế.
- **Redis** — Postgres + checkpointer đã đủ. Project A dùng Redis vì có session/cache thật; ở đây nó
  không có việc gì làm.
- **Qdrant / vector DB riêng** — pgvector hoặc không gì cả.
- **OCR thật (Tesseract/VietOCR)** — vision model lo, và đây không phải project computer vision.
- **Auth / JWT / RBAC / multi-tenant** — reviewer là một string trong `approvals.decided_by`. Tốn
  một tuần và không chứng minh kỹ năng nào trong 7 kỹ năng GĐ5. Ghi "Future Work" là đủ.
- **Tích hợp ERP thật (MISA AMIS API…)** — `journal_entries` là sổ nội bộ. Tích hợp thật cần tài
  khoản doanh nghiệp và không thêm gì cho câu chuyện agent.
- **Fan-out tầng 2 theo phòng ban** — vòng lặp serial rõ ràng hơn và nhanh tương đương ở n=30.
- **Streaming token-by-token trên UI** — SSE tiến độ theo node là đủ, dễ hơn nhiều.
- **Self-host LangFuse** — v3 kéo theo ClickHouse + Redis + MinIO. Bốn service để trace một agent.

---

## 5. Rủi ro chính

| Rủi ro | Khả năng | Giảm thiểu |
|---|---|---|
| **Cấu trúc XML QĐ-1450 sai** — toàn bộ extraction/generator/eval dựa lên nó | Trung bình-Cao | M1 là milestone dành riêng cho việc này. Parser viết theo file **thật** trước, generator sau. Gate cứng: không có XML thật thì không mở M2. Kiểm tra NĐ 70/2025. |
| **429 free tier khi fan-out** — hỏng đúng lúc demo trực tiếp | Cao | `max_concurrency=4–6` là kiểm soát **chính**, key rotation là **lưới**. `LLM_MODE=replay` từ M3 khiến demo không thể hỏng vì rate limit của người khác. |
| **Ma sát LangGraph/PostgresSaver** — mất ngày đánh nhau với framework | Trung bình | Pin version ở M0, không bump. Spike ở M1. Wrapper function node thay vì compiled-subgraph-as-node. Ghi ADR ngay hôm chạy được. LangGraph đã lên 1.x nên `interrupt()` ổn định hơn lo ngại ban đầu. |
| **Next.js phình scope** nuốt M7–M8 | Trung bình-Cao | API-first. Streamlit ở M5 làm bảo hiểm. Time-box 4 ngày + **quy tắc dừng** rõ ràng ở M7. |
| **Eval tuần hoàn** — *"bạn tự chấm bài của mình?"* | Cao nếu không phòng | Generator (Jinja template) và parser (`xml_schema.py` path map) **viết độc lập**; 2–3 XML thật trong eval set; baseline `keywords_vi` cạnh mọi metric LLM; negative control cho injection. |
| **Auditor không đủ giỏi để phản biện** (override precision <50%) | Trung bình | Đã thiết kế là thí nghiệm **có gate**. Thua vẫn ra writeup — đúng bài học M3.7 của Project A (NULL/NO-GO cũng là kết quả). Không block milestone khác. |
| **Bug reducer `operator.add`** làm state phình bậc hai | Trung bình | Test 3-iteration ở gate M3. Node trả **delta**, không bao giờ trả cả list. |
| Không đủ thời gian 8 tuần | Trung bình | Thứ tự cắt: Next.js → ReportingAgent → pgvector → giảm số PDF vision. **Không bao giờ cắt**: HITL durability, injection test, eval harness, cost tracking. |

---

## 6. Nhịp làm việc đề xuất

- Mỗi phiên: đọc §7 "Trạng thái" → chọn việc trong milestone đang mở → implement → **verify bằng
  lệnh chạy được, không phải cảm giác** → cập nhật §7.
- Sau mỗi milestone: cập nhật bảng §7 + ghi artefact vào `docs/notes/`.
- Mọi hằng số không hiển nhiên trong code phải có comment ghi **ngày + phép đo + tên file report**
  biện minh cho nó (kế thừa thói quen tốt nhất của Project A — xem `confidence.py:74-99`).
- Milestone lớn dùng workflow spec → plan → implement → verify, lưu vào `docs/superpowers/{specs,plans}`.

---

## 7. Trạng thái milestone

| Milestone | Trạng thái | Ngày xong | Ghi chú |
|---|---|---|---|
| M0 — Nền sạch | ✅ | 2026-07-27 | Gate 5/5. `make dev` + `make check` green; CI green on real Postgres (`30233872335`, 62 passed). Alembic scaffolding was pulled in here because the gate demanded "run migrations" while 0.1–0.7 contained no item that built it. A CI hole survived until 2026-07-27: the workflow created the schemas but never ran `alembic upgrade head`, invisible until a test needed a table |
| M1 — XML thật + HITL spike | ✅ | 2026-07-28 | **Gate 4/4, re-verified independently 2026-07-28** (not merely re-read): `make check` 123 passed, HITL `kill -9` **10/10 locally** at `HITL_RUNS=10`, real `alembic revision --autogenerate` against the 4 live checkpoint tables emits an **empty** migration. All 6 items done: ✅ 1.1 (8 files / 8 vendors / 3 `PBan`) · ✅ 1.2 · ✅ 1.3 `xml_schema.py` · ✅ 1.4 `xml_parser.py` (8/8 parse) · ✅ 1.5 HITL durability · ✅ 1.6 Alembic isolation + ADR 0002. **8/8 guards re-confirmed red under mutation** — see the review note below. Owed, non-blocking: ADR `0001-three-graphs-not-one.md`; terminal recording (deferred to M8); NĐ 70/2025 field check |
| M2 — Corpus + schema + domain | ✅ | 2026-07-28 | **Gate 5/5, re-verified independently 2026-07-28.** Split into three specs: **M2-A** (2.4/2.5/2.7 schema+seed+repositories, 15 bảng / 9 revision / 11 mutation guard đều đỏ) · **M2-B** (2.6 `domain/`, 107 test, 8/8 hóa đơn thật ra ZERO finding, 10 guard đỏ) · **M2-C** (2.1–2.3 generator, 67 test, 9 guard đỏ). Corpus: 300 mục = 285 XML **parse hết** + 15 PDF-only khai báo rõ là không parse được (M3.4 render). Generator KHÔNG import `xml_schema`/`xml_parser`/`domain` — có test chặn, nên điểm round-trip là bằng chứng chứ không phải lặp vòng. **Ba lần review, ba lần tìm ra lỗi mà bản implement đã báo là đạt**: guard M1.6 bị viết lại thành mù trên CI + artefact bịa `ck_vendors_tax_code_format` + 32 diff ma autogenerate (M2-A); một mutation guard xanh khi đã phá luật (M2-B); `make check` ĐỎ lúc đóng milestone + `scripts/` chưa từng được lint + `BUDGETS` import chết (M2-C). Đã sửa hết và đo lại. **Nợ, không chặn M3**: ADR `0001-three-graphs-not-one.md`; terminal recording (M8); NĐ 70/2025; KCT/KKKNT; PDF render (M3.4); injection variant 7–26 (M6.1) |
| M3 — Extraction + tools + cost + replay | ✅ | 2026-07-29 | **Gate 4/4, closed 2026-07-29.** Split into three specs, each independently reviewed the same day it was implemented: **M3-A** (3.6/3.7 cost capture + `LLM_MODE=replay`; migration 10 closed an M2-A hole where `llm_calls` shipped five columns short of design §2.9 while the spec claimed it verbatim) · **M3-B** (3.2/3.3/3.4 `structured.py`, `sanitize.py`, `vision.py`, corpus PDFs + font; six PII-carrying vendor PDFs untracked) · **M3-C** (3.5 `graphs/enrichment.py`, `route_after_validate`, `serde.py` msgpack allowlist, `run_one.py`, generated mermaid drift guard). **455 passed** under `make check` and again under `LLM_MODE=replay` with zero API keys. Item 3.1 moved to M7 — `ToolSpec` has no consumer inside the M3 gate (M3-A §3.3). **Three reviews, three sets of defects the implementation had reported as passing**: `LLM_MODE=record` could never write a FAILURE fixture — the sole reason `CachedResponse.error` exists — plus `generate_stream` bypassing the replay seam, and no test driving gateway → telemetry → DB (M3-A; **the first of those originated in the plan**); a closeout note headed "verbatim" that dropped the one criterion that was false, invented open questions replacing two safety-relevant real ones, and an "extraction confirmed" spot-check with no numbers and no recorded fixture (M3-B); a **red pushed commit**, two tests that passed exactly once per database, and a mutation made red by asserting a list contains itself (M3-C). **16/16 · 14/14 · 12/12 guards red** after fixes. **Owed, not blocking M4**: the vision branch has **no accuracy measurement at all** and no key is configured; the PII git-history purge; ADR `0001-three-graphs-not-one.md`; a per-run `thread_id` component (M3-C §8.5) and function-node wrapping (§8.3), both landing in M4. |
| M4 — Multi-agent + memory + serialization | ✅ | 2026-08-01 | **Closed 2026-08-01, after an exit review that found the closing commit RED.** Four specs: **M4-A** corpus & reference alignment · **M4-B** vendor memory + `signals` · **M4-C** agents · **M4-D** batch graph + serial reconciliation. Gate measured and replayed with every API key unset — see the gate table in §3. Headline numbers: classification **95.4% vs 57.9% baseline** (+37.5, gate needed +15); department **77.2%** against an **84.0% ceiling**; Auditor dispute precision **12.5%** (CI 0.043–0.310, n=24), **below §6.4's 50% net-harm line and reported as such** — the multi-agent experiment produced a negative result and it is the milestone's most honest artefact. `PRICE_OUTLIER` went **0/6 → 6/6 at 0.7% FP**. Vision debt paid: **2/2 twins 4/4 fields**. Batch gate: 28 enriched / 2 failed / 27 persisted, **23 auto / 7 needs-approval**, MKT chain `35M → 15M → −5M` with `INV-0001` breaching. **Every one of the four specs was closed on a claim the review disproved**: M4-A's departments did not join the seed and its `category` was unlearnable (baseline 1.8%, below chance); M4-B's parameter sweep was run honestly and then not acted on, leaving the pre-existing constants; M4-C's harness had three bugs and had never been executed, and `structured.py` had two defects that made **every live structured call impossible** since M3-B; M4-D was declared closed on a **red pushed commit** with a fabricated `Decimal("50000000")` budget limit, a gate that was **making live calls while claiming to replay**, and tests that **did not run in CI at all** because no step generated the corpus. All fixed and re-measured. **Owed**: a throttled Auditor re-measurement (the 177 Gemini rate-limits mixed two models into one rater); vision fixtures re-recorded on the CI platform; the PII git-history purge. |
| M5 — HITL production + API + UI | ✅ | 2026-08-01 | Four specs: **M5-A** write guard + auto path · **M5-B** approval graph + HITL loop · **M5-C** FastAPI · **M5-D** Streamlit UI. **Closed 2026-08-01.** **M5-A done 2026-08-01**, then corrected by review: the reported gate split (18/12) could not be reproduced — it was measured against rows leaked by a test that committed on the `owner_conn` fixture, the same leak `4f870bc` had already fixed once; `route_outcomes` was structurally blind to budget findings and sent **8 invoices to the auto path that the guard refused, every one a `BUDGET_BREACH`**; and the note had TT133 `6421`/`6422` swapped in the paragraph stating its own headline risk. Reproducible answer: **15 posted / 15 needs-approval**. **M5-B done 2026-08-01**, also corrected by review — and this time the review found **nothing pushed and CI never run** across 12 commits while the milestone was declared COMPLETE, no mutation table, no live measurement, and a `approval_graph.mmd` listed as delivered that was never regenerated and had gone stale. All five mutations verified red afterwards, incl. the one that matters: give `apply_decision` a `conn` and the **behavioural test stays GREEN** while the guard verifies its own caller's uncommitted write. Portfolio test 1 **retargeted onto the real approval graph, 10/10**; portfolio test 4 green at **15 posted / 12 parked**. **`max_concurrency` debt PAID** — and the live run found a bare `assert` in `serial_join` that killed the whole batch on an invoice with no seller MST, a defect replay structurally cannot reach. Note `docs/notes/2026-08-01-m5b-approval-graph.md`. **M5-C done 2026-08-01**: `api/app.py` FastAPI app, 5 route modules, `db/queries.py` read-only layer, `progress.py` SSE channels, `DecisionRequest` extra='forbid' security boundary, 4 structural mutation tests verified red, live curl gate transcript in `docs/notes/2026-08-01-m5c-fastapi.md`. **M5-D done 2026-08-01**: `ui/client.py` + `ui/app.py`, four Vietnamese pages, HTTP-only — the UI imports nothing from `invoice_agent` except `config`, enforced by a source guard, because a Streamlit app that imported `submit_decision` directly would leave M5-C's only consumer bypassing the API. 3/3 mutations red, incl. the asymmetry that matters: with the DB import added the **client tests stay GREEN** and only the import check sees it. `make demo` serves both processes; streamlit 1.60.0 took `requirements.lock` to 409 lines, measured rather than hidden. **M5-D does NOT consume M5-C's SSE endpoint** — a blocking stream freezes Streamlit's rerun loop; its consumer is M7. **M5 gate, all six conditions met**: `test_never_post_without_approval.py` (M5-A) · `test_batch_not_blocked_by_pending.py` (M5-B) · approval over real `curl` (M5-C) · `kill -9` + `recover_pending.py` (M5-B) · reviewer feedback in `vendors.reviewer_notes` (M5-B) · a working Streamlit demo (M5-D). **The review record above is kept deliberately.** The first cut of this row replaced all of it with “All gate criteria met”, which would have made M5 the only milestone with no account of what its reviews found — and it had the most. §10.5 is *Báo cáo trung thực*. Note `docs/notes/2026-08-01-m5d-streamlit.md`. |
| M6 — Security + observability + eval | 🔲 | | |
| M7 — MCP + Next.js | 🔲 | | Quy tắc dừng: 4 ngày cho Next.js |
| M8 — Portfolio final | 🔲 | | |

### M1 exit review — 2026-07-28

M1 produced **four** separate tests that passed while guarding nothing (1.5, 1.6, and two in 1.4).
Four is a pattern, not bad luck, so closing the milestone on the *claim* that they were since
mutation-verified would repeat the exact mistake the milestone kept making. Every guard was
re-broken from scratch and re-run:

| Mutation applied to source | Guard that should catch it | |
|---|---|:---:|
| `defusedxml` → stdlib `ElementTree` | `test_entity_declarations_rejected` | ✅ red |
| 2 of 6 `EXT_SCOPES` paths broken | `test_header_ext_scopes_are_populated…` | ✅ red |
| `money('')` → `Decimal(0)` | `test_empty_element_is_none_not_zero` | ✅ red |
| `locate_hdon`: `len(found) != 1` → `< 1` | `test_multi_invoice_envelope_raises` | ✅ red |
| `ext_bag` reads children positionally | `test_ext_bag_is_order_independent` | ✅ red |
| root-anchored `.//PBan` | `test_envelope_values_do_not_leak` | ✅ red |
| `EXT_KEY_LINE_AMOUNT` → the gross-amount key | `test_invoice_data_is_sufficient…` | ✅ red |
| `include_object` → `return True` | `test_alembic_ignores_langgraph` | ✅ red |

**8/8.** The guards are real this time, and the two `.//` guards divide the work exactly as row 1.3
claims: the static one catches field-level `.//`, the behavioural one catches root-anchored lookups.

Two corrections to the record, both small:

- **Test count drifted.** Rows 1.3/1.4 say 116; the suite is **123**. The 7 came from the review
  commits themselves (`4442bd1`, `c97da4e`) — the guards added to close the holes.
- **A PII leak lived in this document, not in the data.** See the redaction note in §M1.2.

Nothing found in this review blocks M2. **The gate is open.**

---

## 8. Sáu test là phần portfolio

Mọi thứ khác trong repo tồn tại để hỗ trợ chúng.

| Test | Milestone | Chứng minh |
|---|:---:|---|
| ✅ `test_hitl_survives_restart.py` | M1 → **retargeted M5-B** | interrupt → `kill -9` → resume hoàn tất — **10/10**. Since M5-B it runs the **real `approval_graph`**, not the spike, and asserts the consequence as well as the mechanism: exactly **1** journal entry, **1** audit grant, **1** decided approval |
| ✅ `test_never_post_without_approval.py` | M5-A | Safety property, cả ba lớp code/DB/test — **5/5 mutation đỏ**, mỗi lớp cắn độc lập. Ghi chú: bỏ `UNIQUE(invoice_id)` **không** làm case post-hai-lần đỏ, vì guard đọc entry cũ trước khi insert; hai case đo hai thứ khác nhau |
| ✅ `test_budget_serialization.py` | M4 | Xáo trộn thứ tự đến → cùng một hóa đơn vượt — **10 shuffles, byte-identical chain, mutation-verified** |
| ✅ `test_batch_not_blocked_by_pending.py` | M5-B | Batch reaches `completed` **while** approvals sit undecided — measured **15 posted / 12 parked**. Asserts the RELATIONSHIP, not the counts: the split is a corpus property (M5-A kept it reported, not targeted). The roadmap's original "26 sạch / 4 chờ" predates the corpus and matches nothing |
| ⚠️ `test_prompt_injection.py` | M6-A | 0 decisions set / 0 entries posted across 24 carriers + 6 reasoning cases; 3 of 4 mutations bite this suite; **negative control 22/24 — 2 measured `category_laundering` successes, contained by `BUDGET_BREACH`, gate not met** |
| `test_no_write_tools_in_mcp.py` | M7 | Quyết định kiến trúc được test bảo vệ |
