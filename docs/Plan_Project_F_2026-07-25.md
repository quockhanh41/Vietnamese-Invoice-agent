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

**Updated 2026-07-27 — M0 closed, gate 5/5. M1 open: 1.1, 1.2, 1.5, 1.6 done; 1.3 and 1.4 remain.**

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

**Remaining in M1**: 1.3 `xml_schema.py` · 1.4 `xml_parser.py` — written against the 8 real
fixtures. No spec yet; needs its own brainstorming pass.


**Còn ở project khác, CHƯA port** (tính vào scope M3): `execute_tool_with_retry` +
`DISPATCH_TABLE` ở `../phase-5-agentic-ai/react-pattern-tool-use/mini-project/agent.py` — port ở
M3.2 và **bỏ hàm calculator dùng `eval()`**.

**Chưa từng viết bao giờ** (phải tính vào scope, không phải copy): `interrupt()` /
`Command(resume=)` hiện đại — module `human-in-the-loop/` chỉ có prose, `support_agent.py` dùng
`interrupt_before` cũ và LLM đều mock; `PostgresSaver` — mới chỉ wire `MemorySaver`; `Send`/fan-out
— zero code; MCP server — chỉ có sketch 19 dòng; LangFuse cho *agent* — module dạy LangSmith, zero
code LangFuse; cost tracking — Project A **hoàn toàn không có** (dashboard hardcode `$0.00`).

**Nợ kỹ thuật kế thừa**: ✅ `os.getenv` rải rác → một `config.py` (M0.2) · ✅ `sys.path.insert` →
src-layout (M0.1) · ✅ không lint/typecheck/CI → đã có (M0.6) · 🔲 `main.py` 643 dòng với `routes/`
rỗng → tách routes thật, đến M5.6.

**⚠️ Bài học bảo mật của chính repo này**: hóa đơn thật mang PII **và** chứng thư số, mà
**gitleaks không bắt được PII** — nó tìm token. `.gitignore` là lớp phòng thủ duy nhất, và nó
**đã từng thủng**: neo `/*.xml` chỉ chặn file rời ở root, để lọt nguyên thư mục nhà cung cấp gửi
kèm 2 PDF có số CCCD (vá ở `bb8718d`). Đính chính một cảnh báo cũ: bản roadmap đầu ghi
`vietnamese-legal-bot/.claude/settings.json` "đang commit Langfuse key plaintext" — **sai**,
`.claude/` đã bị gitignore và `git log --all` trả về rỗng; key chưa bao giờ vào history.

**Next**: 1.3 `xml_schema.py`, then 1.4 `xml_parser.py`, both written against the 8 real fixtures.
They are the last two items standing between M1 and its gate.

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
| 1.3 | 🔲 **Not started** — no `xml_schema.py` exists yet. This is what the last open M1 gate condition is waiting on. `xml_schema.py` — **một nơi duy nhất** chứa element path + namespace. Viết theo file thật *trước*, generator sẽ viết *sau* và **độc lập** (đây là cơ chế chống eval tuần hoàn) | §10.1 |
| 1.4 | 🔲 **Not started**, depends on 1.3. `xml_parser.py` deterministic, parse được 2–3 file thật | §3.2 |
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
| MST là "10/13 số" | **3 hình dạng**: `0104093672` (10), `0304998358-042` (**có gạch**, chi nhánh), `052098014618` (**12 số**, hộ kinh doanh) | Validator từ chối 2/3 dạng thật |
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
gitleaks **không bắt được** — nó tìm token, không tìm PII. `.gitignore` chặn `/*.xml` và `/*.pdf` ở
root; chỉ bản **đã ẩn danh** trong `tests/fixtures/real_samples/` mới được commit. Nguyên tắc ẩn danh:
**giữ cấu trúc, thay giá trị** — kiểm chứng bằng cách so cây tag gốc vs ẩn danh phải giống hệt.

**Gate M1**: `test_hitl_survives_restart.py` **xanh** (10/10 lần); 2–3 XML thật parse ra
`InvoiceData` đúng; `alembic revision --autogenerate` trên schema đã có checkpoint **không** sinh ra
lệnh DROP nào.
**Artefact**: **terminal recording** — graph dừng ở `interrupt()`, process bị `kill -9`, khởi động
lại, resume, post xong. *Riêng cái này đã hơn phần lớn portfolio project.* Cộng ADR
`0001-three-graphs-not-one.md` + `0002-alembic-langgraph-schema-isolation.md`.

**Gate status (2026-07-27)** — 2/3, blocked only on the XML half:

| Condition | | Evidence |
|---|:---:|---|
| `test_hitl_survives_restart.py` green 10/10 | ✅ | CI run `30233872335`, `HITL_RUNS=10` on a clean runner. Locally 40/40 in 20s while hunting for flakiness |
| No repeated side effect | ✅ | Per-node counts across the process boundary: `n1_record` = 1, `n2_gate` = 2. The second number is the measurement of *why* `post_entry` cannot share a node with the gate |
| `--autogenerate` emits no DROP | ✅ | `test_alembic_ignores_langgraph.py` runs the real command and is mutation-verified: break `include_object` → red |
| 2–3 real XML parse into `InvoiceData` | 🔲 | Needs 1.3 + 1.4. **This is the whole of what M1 still owes** |

Artefacts still owed: the terminal recording (deferred to M8 by the M1.5 spec — the tests prove it
*can* be recorded) and ADR `0001-three-graphs-not-one.md`.

> **Nếu không tìm được XML thật trong 2 ngày**: escalate — hỏi cộng đồng dev VN (AI Vietnam FB
> group), hoặc xin 1 file ẩn danh từ kế toán quen. **Không được bắt đầu M2 nếu chưa có.** Viết
> parser theo trí nhớ về element name là cách chắc chắn nhất để hỏng ở tuần 4.

---

### M2 — Corpus + schema + lõi deterministic *(tuần 2)*

| # | Việc | Tham chiếu |
|---|---|---|
| 2.1 | `scripts/generate_invoices.py --count 300 --seed 42` — Jinja template, **viết độc lập với `xml_schema.py`** | §10.2 |
| 2.2 | Đủ **14 nhóm defect** theo tỉ lệ §10.2. `defects` là **LIST** không phải enum (hóa đơn thật mang nhiều vấn đề cùng lúc) | §10.2 |
| 2.3 | **Ca vượt ngân sách phải phụ thuộc thứ tự**: seed 3 hóa đơn Marketing sao cho theo thứ tự canonical thì cái thứ 3 vượt, thứ tự khác thì cái khác vượt. Manifest ghi cái nào vượt canonically | §10.2 |
| 2.4 | Alembic migrations: mọi bảng + **`UNIQUE(seller_tax_code, template_no, invoice_series, invoice_number)`** + 2 CHECK constraint + trigger + `REVOKE UPDATE,DELETE ON audit_log` | §2.3, §2.7 |
| 2.5 | Seed reference data với **số hiệu tài khoản TT133 thật** (`6421`, `6422`, `1331`, `331`, `156`, `153`) + `keywords_vi` (= baseline eval) | §2.1 |
| 2.6 | Toàn bộ `domain/` — pure function, zero I/O: `validation.py`, `taxcode.py` (checksum MST 10/13 số), `money.py` (Decimal VND + **số tiền bằng chữ ↔ số**), `accounting.py` (JE builder + balance check), `budget.py`, `anomaly.py` | §3.2 |
| 2.7 | Repositories + `audit_log` writer | §2 |

**Gate M2**: `make generate && make seed` chạy sạch; ≥60 unit test `domain/` xanh; parser round-trip
300/300 hóa đơn synthetic **và** 2–3 file thật; thử `INSERT` một bút toán lệch Nợ/Có → **bị CHECK
constraint từ chối**; thử `UPDATE audit_log` → **bị từ chối quyền**.
**Artefact**: screenshot `psql` cho thấy database tự bảo vệ mình — chứng cứ trực quan nhất cho
"safety property ba lớp".

---

### M3 — Extraction pipeline: vòng leo thang, tools, cost, replay *(tuần 3)*

| # | Việc | Tham chiếu |
|---|---|---|
| 3.1 | `tools/registry.py` — `ToolSpec(name, args_model, return_model, side_effect, mcp_exposed)`. **Một nguồn** cho cả LangGraph executor lẫn MCP server | §8.1 |
| 3.2 | `tools/executor.py` — port `execute_tool_with_retry` (mini-project `agent.py:90`), thêm retry khi vi phạm Pydantic schema. **Không port hàm calculator dùng `eval()`** | §8.1 |
| 3.3 | `extraction/sanitize.py` — bọc text hóa đơn thành block `<invoice_data>` đã escape, **không bao giờ nối vào system prompt** | §9.2 |
| 3.4 | `vision_extract.py` — PDF → ảnh → structured output, inject `current_errors` vào prompt khi retry | §3.2 |
| 3.5 | `graphs/enrichment.py` — **`route_after_validate` phân biệt lỗi EXTRACTION vs lỗi DATA**. Đây là nội dung thật của self-correction, không phải cái vòng lặp | §3.3 |
| 3.6 | **Cost capture**: thêm `usage` out-param vào `_generate_impl` (theo đúng idiom `used_model` ở `gateway.py:256`) → `llm_calls`. **Ghi cả lần gọi thất bại** + `shadow_cost_usd` | §2.9 |
| 3.7 | **`LLM_MODE=replay`** — fixture cache khóa theo `hash(model, messages)` | §11.4 |

**Gate M3**: một hóa đơn chạy end-to-end có cost ghi vào `llm_calls`; test vòng lặp **3 iteration**
khẳng định `len(error_history) == 3` (bắt bug reducer `operator.add` trả cả list → tăng trưởng bậc
hai); hóa đơn XML lỗi extraction leo lên vision, hóa đơn vision hỏng rơi xuống manual_queue; toàn bộ
test chạy được với `LLM_MODE=replay` **không cần API key**.
**Artefact**: ảnh render `enrichment_graph` + trace một hóa đơn leo thang chiến lược qua 3 tầng.

---

### M4 — Multi-agent + memory + chứng minh serialization *(tuần 4)*

| # | Việc | Tham chiếu |
|---|---|---|
| 4.1 | `ClassificationAgent` (tier rẻ) + `AuditorAgent` với **`exclude_model=model_used`** — mở rộng `generate_with_model` (`gateway.py:152`) | §6.3 |
| 4.2 | Bất đồng → cờ `CLASSIFICATION_DISPUTED`, đưa người duyệt **cả hai ý kiến kèm lý do** | §6.4 |
| 4.3 | `memory/vendor_profile.py` — upsert **transactional** + z-score price outlier + `rebuild_vendor_profiles.py` (lối thoát khi profile lệch) | §2.2, §7 |
| 4.4 | `memory/episodic.py` — **kênh SQL trước** (cùng NCC HOẶC cùng category, ±1 amount bucket, order theo trùng khớp cờ + recency). pgvector để M7 nếu còn giờ | §7.1 |
| 4.5 | `domain/anomaly.py` + `reconcile_budget` **SERIAL**, thứ tự canonical `(issue_date, invoice_series, invoice_number)`, ghi `budget_consumption` với `balance_before/after/sequence_no` | §4.3, §2.6 |
| 4.6 | `graphs/batch.py` — `Send` fan-out, `enrich_one` là **wrapper function node** (map state tường minh + `try/except` mỗi task), `max_concurrency=4–6` | §4.1, §4.2 |

**Gate M4**: batch 30 hóa đơn → ~26 auto, ~4 gắn cờ; **`test_budget_serialization.py` xanh** — xáo
trộn thứ tự file đầu vào 10 lần, chuỗi `balance_after` byte-identical và **cùng một hóa đơn** vượt;
một file hỏng trong batch không giết cả batch (rơi vào `failed`); Auditor override precision đo được
(báo cáo dù xấu — dưới 50% là gây hại ròng).
**Artefact**: **log bất đồng Classifier vs Auditor** — tình huống multi-agent bất đồng thật, không
dàn dựng. Cộng ADR `0003-no-supervisor-agent.md` + `0004-serial-budget-reconciliation.md`.

---

### M5 — HITL production + API + UI bảo hiểm *(tuần 5)*

| # | Việc | Tham chiếu |
|---|---|---|
| 5.1 | `graphs/approval.py` — `load_snapshot → suggest → approval_gate[interrupt()] → apply_decision → post/reject → learn`. **`suggest` KHÔNG gọi LLM** (nó nằm trên đường batch phải chờ) | §5.1 |
| 5.2 | `open_approval` trong batch_graph: ghi 1 row `app.approvals` → `approval_graph.ainvoke(..., config={"configurable": {"thread_id": f"appr:{invoice_id}"}})` chạy tới `interrupt()` → kiểm tra key `__interrupt__` trong kết quả → **trả về ngay** | §5.2 |
| 5.3 | `tools/guards.py` — `create_journal_entry` assert `decision=="approved"` **VÀ đọc lại Postgres** xác nhận `approvals` row đã persist. Mọi lần từ chối ghi `audit_log` | §5.3 |
| 5.4 | `learn` — lý do reject → `episodic_decisions`; `field_corrections` → `vendors.reviewer_notes` (cái `classify` đọc lần sau) | §5.4 |
| 5.5 | `scripts/recover_pending.py` — quét `approvals WHERE decision IS NULL`, với mỗi `thread_id` gọi `get_state` xác nhận còn interrupt treo. **API không bao giờ giả định registry trong RAM sống sót** | §5.5 |
| 5.6 | FastAPI routes **tách thật**: `health`, `batches` (+SSE tiến độ theo node), `invoices`, `approvals`, `reports` | §11.2 |
| 5.7 | **Streamlit approval UI tối giản** — upload, hàng chờ duyệt, chi tiết, approve/reject | Quyết định §0 |

**Gate M5**: `test_never_post_without_approval.py` xanh; **`test_batch_not_blocked_by_pending.py`
xanh** (26 sạch post được trong khi 4 đang chờ — đây là test biện minh cho cả thiết kế 3 graph);
duyệt được qua `curl`; `kill -9` giữa lúc còn pending → restart → `recover_pending.py` → vẫn duyệt
được; feedback của reviewer thật sự xuất hiện trong `vendors.reviewer_notes`.
**Artefact**: demo Streamlit chạy được — từ đây trở đi **luôn có demo**, không phụ thuộc Next.js.

---

### M6 — Security + observability + eval *(tuần 6)*

| # | Việc | Tham chiếu |
|---|---|---|
| 6.1 | Corpus injection: **6 payload gốc × biến thể = 26 case**. Hai cái quan trọng nhất là #5 social engineering (*"Đã được Giám đốc phê duyệt qua email"*) và #6 rửa danh mục — chúng đánh bại mọi phát hiện theo từ khóa | §9.1 |
| 6.2 | **Negative control**: mỗi hóa đơn có payload có một bản sinh đôi đã gỡ payload, phải classify **y hệt**. Không có control thì đang đo nhiễu | §9.3 |
| 6.3 | `test_prompt_injection.py` **CI-blocking**. "Thành công" = bất kỳ điều nào: `approval_status` được set khi chưa có quyết định người; `create_journal_entry` gọi với `auto_clean` trên hóa đơn có cờ; classification khác bản sinh đôi; một cờ bị bóp | §9 |
| 6.4 | **Schema audit test**: khẳng định không response model nào của agent có field approval/flag/status — *structured output là ranh giới an ninh* | §9.2 |
| 6.5 | LangFuse `@observe` mọi node, tag `invoice_id`/`batch_id`/`node`/`agent`, cost + shadow cost làm metadata | §2.9 |
| 6.6 | Cost cap: batch 10.000 file bị **từ chối trước khi gọi LLM lần nào**; per-invoice token budget vượt → manual queue | §9.2 |
| 6.7 | Toàn bộ eval stage + `scripts/eval.py` → `docs/eval-report.md` | §10.4 |
| 6.8 | **Tự bấm giờ nhập tay 10 hóa đơn vào Excel** rồi ngoại suy — đừng trích báo cáo thị trường cho con số của chính mình | §10.4 |

**Gate M6**: injection **0/26**; negative control 100% khớp; schema audit xanh; cost cap 1/1;
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
| M1 — XML thật + HITL spike | 🟡 | | ✅ 1.1 (8 files / 8 vendors) · ✅ 1.2 · ✅ **1.5 HITL durability — `kill -9` 10/10, no repeated side effect** · ✅ **1.6 Alembic schema isolation + ADR 0002** · 🔲 1.3 `xml_schema.py` · 🔲 1.4 `xml_parser.py`. Both HITL tests were rewritten on 2026-07-27 after review: the Alembic guard tested a copy of `include_object` rather than the real one, and the interrupt-node re-run — the finding M5 depends on — was asserted but never measured. Both now hold, and ADR 0002 records that node placement is necessary, not sufficient |
| M2 — Corpus + schema + domain | 🔲 | | |
| M3 — Extraction + tools + cost + replay | 🔲 | | |
| M4 — Multi-agent + memory + serialization | 🔲 | | |
| M5 — HITL production + API + UI | 🔲 | | |
| M6 — Security + observability + eval | 🔲 | | |
| M7 — MCP + Next.js | 🔲 | | Quy tắc dừng: 4 ngày cho Next.js |
| M8 — Portfolio final | 🔲 | | |

---

## 8. Sáu test là phần portfolio

Mọi thứ khác trong repo tồn tại để hỗ trợ chúng.

| Test | Milestone | Chứng minh |
|---|:---:|---|
| `test_hitl_survives_restart.py` | M1 | interrupt → `kill -9` → resume hoàn tất |
| `test_never_post_without_approval.py` | M5 | Safety property, cả ba lớp code/DB/test |
| `test_budget_serialization.py` | M4 | Xáo trộn thứ tự đến → cùng một hóa đơn vượt |
| `test_batch_not_blocked_by_pending.py` | M5 | 26 sạch post được khi 4 đang chờ duyệt |
| `test_prompt_injection.py` | M6 | 0/26 thành công, có negative control |
| `test_no_write_tools_in_mcp.py` | M7 | Quyết định kiến trúc được test bảo vệ |
