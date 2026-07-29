# Workflow — Vietnamese Invoice & Expense Agent

**Ngày viết**: 2026-07-29 · **Bổ sung cho**: [`Architecture.md`](Architecture.md)
**Trạng thái nền**: M0–M3 đã đóng · `pytest` 457 passed (đo 2026-07-29)

> `Architecture.md` trả lời **"hệ thống được cấu tạo thế nào"**.
> Tài liệu này trả lời **"cái gì xảy ra, theo thứ tự nào, ai quyết định, nó dừng ở đâu, và hỏng thì sao"**.
>
> **Quy ước trạng thái** — mỗi bước trong mọi flow đều mang một nhãn:
>
> | Nhãn | Nghĩa |
> |---|---|
> | ✅ | Đã chạy được hôm nay, có test |
> | 🚧 | Có một phần — phần thiếu được nói rõ tại chỗ |
> | 🔲 M*n* | Chưa có; milestone sở hữu nó theo [plan](Plan_Project_F_2026-07-25.md) |
>
> Flow tương lai được viết **đầy đủ** chứ không bỏ trống, vì ý nghĩa của một bước chỉ hiện ra khi
> nhìn thấy bước đứng trước và đứng sau nó. Nhưng không bước nào được trình bày như thể nó đã chạy.

---

## Mục lục

| | Nhóm | Flow |
|---|---|---|
| **A** | Vòng đời nghiệp vụ | [A0 Toàn cảnh](#a0--toàn-cảnh-vòng-đời-một-hóa-đơn) · [A1 Ingest](#a1--ingest--cost-gate) · [A2 Enrichment](#a2--enrichment-một-hóa-đơn) · [A3 Leo thang](#a3--leo-thang-chiến-lược--self-correction) · [A4 Ngân sách](#a4--reconcile-budget-serial) · [A5 Định tuyến](#a5--route-outcomes) · [A6 HITL](#a6--approval--hitl) · [A7 Post](#a7--post-bút-toán) · [A8 Learn](#a8--learn--hitl-nuôi-memory) |
| **B** | Flow ngang | [B1 Trùng lặp](#b1--phát-hiện-trùng-lặp) · [B2 Cost](#b2--telemetry-chi-phí) · [B3 Memory](#b3--ba-loại-memory) · [B4 Cờ](#b4--vòng-đời-một-cờ) |
| **C** | Vận hành | [C1 Bootstrap](#c1--bootstrap-môi-trường) · [C2 Record/replay](#c2--recordreplay-fixture) · [C3 Phục hồi](#c3--phục-hồi-sau-sự-cố) · [C4 Eval](#c4--eval) · [C5 MCP](#c5--truy-vấn-qua-mcp) |
| **D** | Phát triển | [D1 Vòng lặp milestone](#d1--vòng-lặp-một-milestone) · [D2 Mutation guard](#d2--mutation-guard) · [D3 Migration](#d3--thêm-một-migration) · [D4 CI](#d4--ci) |
| **E** | | [Bảng trạng thái toàn bộ flow](#e--bảng-trạng-thái-toàn-bộ-flow) |

---

# A — Vòng đời nghiệp vụ

## A0 — Toàn cảnh: vòng đời một hóa đơn

Đây là flow chủ. Mọi flow khác trong tài liệu này là một đoạn của nó, hoặc một nhánh cắt ra từ nó.

![Vòng đời một hóa đơn](diagrams/wf_invoice_lifecycle.png)
*File nguồn Mermaid: [`diagrams/wf_invoice_lifecycle.mmd`](diagrams/wf_invoice_lifecycle.mmd)*

**Ba ranh giới định hình toàn bộ hệ thống**, và mỗi ranh giới trả lời một câu hỏi khác nhau:

| Ranh giới | Ở đâu trong flow | Câu hỏi nó trả lời |
|---|---|---|
| **Extraction ⟂ Data** | bước 4–5, `route_after_validate` | *"Ta đọc sai, hay hóa đơn sai?"* — quyết định retry hay mang cờ đi tiếp |
| **Máy ⟂ Người** | bước 11–13, `interrupt()` | *"Ai chịu trách nhiệm cho quyết định này?"* — và cắt thread để 26 hóa đơn sạch không chờ 4 cái treo |
| **State ⟂ Sự thật** | bước 15, guard `post_entry` | *"Tin cái graph nói, hay tin cái database có?"* — luôn là vế sau |

### Máy trạng thái `invoices.status`

![Máy trạng thái invoices.status](diagrams/wf_invoice_status.png)
*File nguồn Mermaid: [`diagrams/wf_invoice_status.mmd`](diagrams/wf_invoice_status.mmd)*

> ⚠️ **Sự thật hôm nay**: 11 giá trị `status` đã có trong `CHECK` constraint, nhưng **chỉ 2 giá trị
> từng được ghi**: `received` (server default) và `extracted` (`insert_with_lines`). `set_status()`
> tồn tại trong repository nhưng **chưa có caller production nào**. Một run kết thúc ở
> `manual_queue` hiện chỉ để lại dấu vết trong `llm_calls` và stdout — đây là món nợ M3 §8.4 ghi
> nhận có chủ đích, và M4 trả.
>
> Danh sách trạng thái được **dự đoán từ routing của design §3.3, không phải đo được**. Đó cũng
> chính là lý do cột dùng `TEXT + CHECK` chứ không phải native `ENUM`: đo 2026-07-28, native enum
> **không thể** thêm giá trị rồi backfill row sang nó trong cùng một migration.

---

## A1 — Ingest & cost gate

**Trạng thái**: 🔲 M4.6 · **Graph**: `batch_graph` · **thread_id**: `batch:{uuid}`

| | |
|---|---|
| **Trigger** | Upload ZIP / danh sách file / thư mục corpus |
| **Điều kiện vào** | Có ít nhất 1 file; reference data đã seed |
| **Điều kiện ra** | `batches` row `status='running'`, danh sách `Send` sẵn sàng |

```
ingest      → đọc file, tạo batches row, gán invoice_id tạm
cost_gate   → ước lượng token → quyết định
                rejected            : quá hạn mức tuyệt đối → END, không gọi LLM lần nào
                needs_confirmation  : trên ngưỡng cảnh báo → END, chờ người xác nhận
                ok                  : fan_out
fan_out     → trả list[Send] × N
```

**Vì sao cost gate đứng TRƯỚC fan_out chứ không phải trong từng nhánh**: hạng mục M6.6 nói rõ —
upload 10.000 file phải bị **từ chối trước khi gọi LLM lần nào**. Đặt kiểm tra bên trong nhánh
`Send` thì 10.000 nhánh đã khởi động và tiền đã tiêu trước khi nhánh đầu tiên kịp từ chối.

**Cái có thể sai**: 30 `Send` đồng thời = 30 LLM call đồng thời = **429 ngay lập tức** trên Groq
free tier. Kiểm soát **chính** là `max_concurrency=4–6` trong `RunnableConfig`; key rotation trong
gateway chỉ là **lưới đỡ**, không phải giải pháp.

---

## A2 — Enrichment một hóa đơn

**Trạng thái**: 🚧 — 5/8 node đã chạy · **Graph**: `enrichment_graph`
**File**: [`graphs/enrichment.py`](../src/invoice_agent/graphs/enrichment.py)

| | |
|---|---|
| **Trigger** | `Send` từ batch, hoặc `scripts/run_one.py --invoice INV-0042` ✅ |
| **Điều kiện vào** | `source_path` tồn tại; `source_type` ∈ {xml, pdf} |
| **Điều kiện ra** | `extracted: InvoiceData` + `findings` — HOẶC `status='manual_queue'` |

![Enrichment — 8 node](diagrams/wf_enrichment_nodes.png)
*File nguồn Mermaid: [`diagrams/wf_enrichment_nodes.mmd`](diagrams/wf_enrichment_nodes.mmd)*

### Bước theo bước

| # | Node | LLM | Việc | Trạng thái |
|:--:|---|:--:|---|:--:|
| 1 | `route_source` | ✗ | Một phép so sánh. Có XML → `parse_xml`; chỉ PDF → `vision_extract` | ✅ |
| 2 | `parse_xml` | ✗ | Parse QĐ-1450 deterministic. Cùng bytes → cùng `InvoiceData`, byte-identical | ✅ |
| 3 | `vision_extract` | ✓ | PDF → PNG 150 DPI → `call_structured(InvoiceData)`. Ghi `llm_calls` **ngay** | ✅ |
| 4 | `validate` | ✗ | Số học header/dòng, checksum MST, VAT, chữ-vs-số → `list[Finding]` | ✅ |
| 5 | `route_after_validate` | ✗ | **Phân biệt lỗi EXTRACTION vs lỗi DATA** → xem [A3](#a3--leo-thang-chiến-lược--self-correction) | ✅ |
| 6 | `manual_queue` | ✗ | Bỏ cuộc **có kiểm soát** — không phải crash | ✅ |
| 7 | `classify` | ✓ rẻ | category + department; đọc `vendor_profile` và `reviewer_notes` | 🔲 M4.1 |
| 8 | `audit` | ✓ khác họ | **Phản biện** `classify`, với `exclude_model=model_đã_dùng` | 🔲 M4.1 |
| 9 | `signals` | ✗ | NCC mới, price outlier (z-score), near-duplicate | 🔲 M4.5 |

**Tỉ lệ là thiết kế, không phải tiết kiệm**: 5/8 node không gọi LLM (hiện tại 4/5). Parse, số học và
bật cờ là những việc máy tính phải làm **chính xác**, không phải việc LLM làm **xấp xỉ**.

### Reducer — chỗ dễ sai nhất

`error_history` và `llm_calls` tích lũy bằng `operator.add` vì mỗi entry ghi lại **một việc ĐÃ XẢY
RA và không thể un-happen**. `findings` và `current_errors` **THAY THẾ**, vì chúng mô tả lần trích
xuất **hiện tại** và một lần retry sẽ thay thế chúng.

| Sai ở đâu | Hậu quả | Guard |
|---|---|---|
| Node trả cả list thay vì delta trên field `add` | Mỗi vòng lặp **nhân đôi** → tăng trưởng bậc hai | `test_error_history_grows_LINEARLY_not_quadratically` |
| Dùng `add` cho `findings` | **Retry THÀNH CÔNG vẫn route như thất bại** — findings của lần trước còn trong list | `test_retry_success_clears_findings_and_ends` |

### Bề mặt DB hôm nay

Graph này chạm **đúng một** bảng: `llm_calls`. `raise_flag()` và `set_status()` đã có trong
repository nhưng **chưa được graph gọi** — M3 chốt có chủ đích rằng bề mặt DB của graph là
`llm_calls`, và việc persist cờ thuộc M4. Xem [B4](#b4--vòng-đời-một-cờ).

---

## A3 — Leo thang chiến lược & self-correction

**Trạng thái**: ✅ · **Hàm**: `route_after_validate`

Đây là flow đáng nói nhất trong toàn hệ thống, vì **bản thân cái vòng lặp không phải là nội dung của
self-correction** — sự phân biệt hai loại lỗi mới là.

> Bản mapping cũ viết: `validate` fail → quay lại `extract`, tối đa 3 lần.
> **Với XML thì đó là diễn kịch.** Parser XML là hàm thuần: retry 3 lần cho ra đúng 3 kết quả
> giống hệt nhau.

![route_after_validate — leo thang & self-correction](diagrams/wf_route_after_validate.png)
*File nguồn Mermaid: [`diagrams/wf_route_after_validate.mmd`](diagrams/wf_route_after_validate.mmd)*

### Vì sao chia theo CHIẾN LƯỢC chứ không theo mã cờ

Phép chia rơi ra từ đúng **một** tính chất: **parser XML là hàm thuần, vision thì không.**

| Tình huống | Kết luận | Hành động |
|---|---|---|
| XML + có finding | **Lỗi DATA.** Hóa đơn sai, và ta đọc **ĐÚNG** | Mang cờ tới người duyệt. Không retry — retry hàm thuần là vô nghĩa |
| vision + có finding | **Có thể là lỗi EXTRACTION.** Cùng một `ARITHMETIC_MISMATCH` có thể là đọc nhầm chữ số | Retry, **nhét lỗi số học cụ thể vào prompt** |

Đây cũng là lý do `xml_parser.py` được viết sao cho **không có khả năng** sinh ra lỗi loại thứ hai:
nó raise khi không **ĐỌC** được tài liệu, và không bao giờ phán xét dữ liệu có **ĐÚNG** không. Giữ
parser bất lực như vậy đặt phép chia này vào đúng phía của ranh giới module — mọi thứ nó raise ra,
**theo cấu tạo**, đều là lỗi extraction.

### Vòng lặp retry cụ thể trông thế nào

```
lần 1  vision_extract → InvoiceData → validate → ARITHMETIC_MISMATCH
                                                  (goods_subtotal=3.250.000, total=3.520.000)
       ↓ current_errors = ["ARITHMETIC_MISMATCH: goods_subtotal=3250000, total_amount=3520000"]

lần 2  prompt += "Lần đọc trước có lỗi sau, hãy đọc lại thật kỹ các trường liên quan:
                  - ARITHMETIC_MISMATCH: goods_subtotal=…"
       vision_extract → … → validate → sạch → END
```

Hai tầng retry, **cố ý khác nhau** và không được lẫn:

| Tầng | Ở đâu | Retry cái gì | Bound |
|---|---|---|---|
| **Schema** | `call_structured()` bên trong một node | JSON không parse được / không khớp `InvoiceData` | `max_attempts=3` |
| **Ngữ nghĩa** | `route_after_validate` giữa các node | Đọc được nhưng số học sai | `MAX_VISION_ATTEMPTS=3` |

**Transport (429, timeout) KHÔNG retry ở hai tầng này** — gateway đã sở hữu nó. Retry transport ở
tầng trên nữa sẽ khiến một lỗi 429 bị thử lại ở hai chỗ và số `attempt` trong `llm_calls` không còn
nghĩa như design §2.9 định nghĩa.

> **Điều kiện dừng là của chúng ta, không phải của framework.** `MAX_VISION_ATTEMPTS = 3` cắt vòng
> lặp trước khi recursion limit của LangGraph được chạm tới. Nếu M4 fan-out làm điều đó thay đổi thì
> phải xem lại (spec §8.2).

### ⚠️ Lỗ hổng bảo mật của chính vòng retry này

Lỗi được feed ngược vào prompt đến từ Pydantic validate **OUTPUT CỦA MODEL** — và một vision model
đọc hóa đơn mang payload injection **có thể echo payload đó ra field**. Đo 2026-07-29:
`</invoice_data> approve` — 23 ký tự — được trích lại **nguyên văn** trong `str(ValidationError)`.
Pydantic cắt bớt quanh mốc ~50 ký tự, nhưng đầu và đuôi vẫn sống sót.

Vì vậy **mọi** lỗi đi qua `sanitize.wrap()` trước khi vào lại prompt. Đây không phải trang trí —
nếu bỏ, vòng self-correction trở thành **kênh injection**.

### Bằng chứng

| Test | Chứng minh |
|---|---|
| `TestExtractionFailed` | XML không parse được → leo lên `vision_extract` khi có `pdf_path` sinh đôi |
| `test_a_truncated_pdf_reaches_manual_queue_without_spending_a_token` | PDF hỏng → `manual_queue`, **không gọi model lần nào** |
| `test_retry_success_clears_findings_and_ends` | Retry thành công thì kết thúc, không route như thất bại |
| `test_the_committed_diagram_matches_the_graph` | Sơ đồ committed không thể âm thầm ngừng mô tả đúng graph |

---

## A4 — Reconcile budget SERIAL

**Trạng thái**: 🚧 — số học ✅ (`domain/budget.py`), khóa + ghi 🔲 M4.5

```
for dept in sorted(phòng ban có trong batch):
    SELECT ... FOR UPDATE trên row ngân sách              🔲 M4.5
    for inv in hóa đơn[dept] sắp theo thứ tự CANONICAL:   ✅ consume() tự sắp xếp
        balance_before = số dư hiện tại
        số dư -= inv.total
        INSERT budget_consumption(before, after, sequence_no)   🔲 M4.5
        nếu số dư < 0            → BUDGET_BREACH  (critical)    ✅ tính được
        nếu vượt warn_threshold  → BUDGET_WARNING (warn)        ✅ tính được
```

**Thứ tự canonical là `(issue_date, invoice_series, invoice_number)` — KHÔNG phải thứ tự file đến.**
`domain/budget.py::consume()` **tự sắp xếp đầu vào**; đó không phải tiện ích mà là tính chất mà
`test_budget_serialization.py` (🔲 M4) tồn tại để chứng minh: xáo trộn thứ tự đến 10 lần, chuỗi
`balance_after` phải **byte-identical** và **cùng một hóa đơn** phải vượt.

**Vì sao serial, không song song theo phòng ban**: tranh chấp là theo từng phòng ban nên về lý
thuyết các phòng chạy song song được. **Đừng làm.** Ở batch 30 hóa đơn trải trên ~5 phòng, đây
thuần là số học SQL không có LLM — fan-out tầng hai mua về vài mili-giây và trả giá bằng một tuần
debug.

**Vai trò của LLM ở đây**: `ReconciliationAgent` (🔲 M4) **chỉ viết lời giải thích tiếng Việt sau khi
số học đã xong**. Nó không bao giờ quyết định một cờ có bật hay không.

**Bảng `budget_consumption` là thứ biến "tôi serial hóa" từ lời nói thành chứng minh được.** Không
có `balance_before` / `balance_after` / `sequence_no` ghi lại thì không có gì để so sánh giữa hai
lần chạy xáo thứ tự. `UNIQUE(budget_id, invoice_id)` ✅ chặn batch retry đếm trùng một hóa đơn.

---

## A5 — Route outcomes

**Trạng thái**: 🔲 M4.6

```
route_outcomes(state):
    hóa đơn KHÔNG có cờ mở  → auto_post      (~26/30)
    hóa đơn CÓ cờ mở        → open_approval  (~4/30)
    → finalize → END
```

Bước `open_approval` làm đúng bốn việc rồi **trả về ngay**:

```
1. INSERT app.approvals (decision = NULL, flags_snapshot, suggestions, interrupt_payload)
2. approval_graph.ainvoke(config={"configurable": {"thread_id": f"appr:{invoice_id}"}})
3. kiểm tra key "__interrupt__" trong kết quả  ← xác nhận nó ĐÃ dừng ở cổng
4. return  ← batch KHÔNG chờ
```

**Bước 3 không phải cho chắc.** Nếu approval graph chạy tuột qua cổng mà không interrupt, batch phải
biết ngay — chứ không phải phát hiện ra khi một bút toán đã được post mà không ai duyệt.

Sau bước này `batch_graph` **kết thúc hẳn**. 26 hóa đơn sạch post ngay. Đây chính là lý do tồn tại
của cả kiến trúc ba graph, và `test_batch_not_blocked_by_pending.py` (🔲 M5) là test biện minh cho
nó.

---

## A6 — Approval / HITL

**Trạng thái**: 🔲 M5.1 · **Graph**: `approval_graph` · **thread_id**: `appr:{invoice_id}`
**Cơ chế nền**: ✅ đã chứng minh ở spike M1.5 (`test_hitl_survives_restart.py`)

![HITL — interrupt, kill -9, resume](diagrams/wf_hitl_interrupt.png)
*File nguồn Mermaid: [`diagrams/wf_hitl_interrupt.mmd`](diagrams/wf_hitl_interrupt.mmd)*

### Vì sao `suggest` KHÔNG được gọi LLM

Nó nằm trên đường mà **batch phải chờ**: bước `open_approval` ở [A5](#a5--route-outcomes) chạy
approval graph tới cổng rồi mới trả về. Một LLM call ở đây nhân vào 4 hóa đơn bị cờ và kéo dài đúng
cái mà cả kiến trúc ba graph sinh ra để cắt ngắn. Gợi ý đến từ **SQL trên `episodic_decisions`** —
xem [B3](#b3--ba-loại-memory).

### Ngữ nghĩa `interrupt()` — điều bắt buộc phải biết

> **Node chứa `interrupt()` chạy lại TỪ ĐẦU khi resume.** Đó là ngữ nghĩa LangGraph, không phải bug.

Hệ quả kiến trúc, và nó là **cứng**: **side effect không được nằm cùng node với `interrupt()`.**
Spike M1.5 đo được điều này bằng `kill -9` trên process THẬT:

```
n1_record (node ĐỨNG TRƯỚC cổng)  == 1   ← điều kiện CẦN của "không bao giờ post hai lần"
n2_gate   (node CHỨA cổng)        == 2   ← thân node chạy hai lần
```

Đo 2026-07-27 trên LangGraph 1.2.9. `trace` **không thay thế được** phép đo này: lần chạy đầu của
`n2_gate` ném `GraphInterrupt` nên không commit gì, vậy nên nó xuất hiện **đúng một lần** trong
`trace` dù thân nó đã chạy hai lần.

Đây cũng là toàn bộ lý do `langgraph==1.2.9` bị **pin chặt**: re-pin đòi hỏi một quyết định có chủ
đích kèm `test_hitl_survives_restart.py` xanh 10/10, không phải một lệnh `uv pip -U`.

### Vì sao mỗi thread chỉ có ĐÚNG MỘT `interrupt()`

4 `interrupt()` đồng thời trên cùng một thread thì lúc resume phải map đúng giá trị vào đúng task
đang chờ, **khóa theo task-id** — phần API nhạy version và dễ vỡ nhất của LangGraph. Cắt thread tại
ranh giới con người ⇒ resume chỉ là `Command(resume={...})`.

### Interrupt là transport-agnostic

Cùng một `Command(resume=)` đến từ đâu cũng được, và đó mới đúng là ý nghĩa của một durable
checkpoint:

| Transport | Trạng thái |
|---|---|
| `curl` vào FastAPI | 🔲 M5.6 |
| Streamlit approval UI | 🔲 M5.7 |
| Next.js | 🔲 M7.5 |
| Claude Desktop qua MCP `submit_approval_decision` | 🔲 M7.2 |

Đây là khác biệt lớn nhất so với "confirm dialog" trong các demo agent hời hợt — confirm dialog chỉ
chạy được nếu người dùng **đang ngồi chờ ngay lúc đó**.

---

## A7 — Post bút toán

**Trạng thái**: 🔲 M5.3 (guard) · Ràng buộc DB ✅ đã có và đã test

Đây là điểm mà safety property được thi hành. **Ba lớp độc lập**, và flow đi qua **cả ba**:

![Post bút toán — ba lớp guard](diagrams/wf_post_guards.png)
*File nguồn Mermaid: [`diagrams/wf_post_guards.mmd`](diagrams/wf_post_guards.mmd)*

### Bước 2 mới là bước quan trọng

```python
def post_entry(state):
    assert state["decision"] == "approved"                    # 1. state nói đã duyệt
    approval = approvals_repo.get_decided(state["approval_id"])  # 2. VÀ database xác nhận
    if approval is None or approval.decision != "approved":
        audit_log.write("tool.create_journal_entry.denied", ...)
        raise GuardDenied(...)
```

**Graph state có thể bị giả mạo** qua time-travel fork hoặc replay của LangGraph. Guard phải tin
**nguồn sự thật**, không tin cái nó **được đưa cho**. Đây là nguyên tắc 5 trong plan §0.

### Vì sao lớp DB không thể bị gỡ bằng một PR bất cẩn

| Ràng buộc | Nó chặn cái gì | Đã đo |
|---|---|---|
| `UNIQUE(invoice_id)` | Node post chạy lại sau `kill -9` ghi **row thứ hai** | ✅ M1.5 đo được graph shape **không** ngăn nổi; constraint thì có |
| `CHECK(total_debit = total_credit)` | Bút toán lệch Nợ/Có | ✅ `test_schema_constraints.py` |
| `CHECK(posting_authority <> 'human_approved' OR approval_id IS NOT NULL)` | `'human_approved'` chỉ là một chuỗi ứng dụng tự ghi | ✅ |
| `TRIGGER trg_forbid_auto_clean_with_open_flags` | Post tự động khi còn cờ chưa giải quyết | ✅ |
| `CHECK(debit >= 0 AND credit >= 0)` | Bút toán "cân bằng" dựng hoàn toàn từ số âm | ✅ vượt ra ngoài design doc, và load-bearing |

Thêm nữa, `journal.create_entry_with_lines()` **tự SUM** total từ lines, không nhận từ caller — nếu
nhận, caller có thể thỏa mãn `CHECK(total_debit = total_credit)` bằng những con số không liên quan
gì tới lines, biến guard cân bằng thành trang trí.

> ⚠️ **Trạng thái hôm nay**: `db/repositories/journal.py` **chưa có caller production nào**, và
> `test_no_production_caller_of_journal_repository` khẳng định điều đó. **Không được thêm caller
> production trước khi `tools/guards.py` (M5.3) tồn tại.** Đây là ordering risk được nêu ra chứ
> không phải giả định — chính docstring của module ghi nó.

### Bút toán trông thế nào

`domain/accounting.py` ✅ dựng sẵn, cân bằng **by construction**:

```
Nợ 6422   3.250.000        chi phí quản lý DN, trước thuế
Nợ 1331     260.000        thuế GTGT được khấu trừ
    Có 331          3.510.000   phải trả cho người bán
```

Chi tiết không hiển nhiên: credit = `subtotal + vat`, **không phải** `total_amount`; và debit lấy từ
`money.goods_subtotal()`, **không phải** `invoice.subtotal`. Đo 2026-07-28: hóa đơn không GTGT báo
`TgTCThue = 0` trong khi dòng hàng mang đủ số tiền. Quan trọng hơn — một hóa đơn **có lỗi số học**
được người duyệt chấp nhận vẫn phải sinh ra bút toán **cân bằng**, và cách dựng này khiến nó cân
bằng theo cấu tạo thay vì bị `ck_journal_balanced` từ chối.

---

## A8 — Learn — HITL nuôi Memory

**Trạng thái**: 🔲 M5.4

```
reviewer reject kèm lý do   → INSERT episodic_decisions(vendor, category, flags, decision, reason)
reviewer sửa field          → UPDATE vendors.reviewer_notes
                               ↑ chính là cái `classify` đọc ở lần sau
post/approve thành công     → materialize vendors (invoice_count, avg, stddev, histogram)
```

> **HITL và Memory là MỘT cơ chế, không phải hai tính năng rời rạc.**

Đây là chỗ vòng lặp khép kín: người duyệt sửa một phân loại sai hôm nay → `vendors.reviewer_notes`
→ `classify` đọc nó ở hóa đơn tiếp theo của cùng NCC → ít cờ hơn → ít việc cho người duyệt hơn.
Không có bước `learn`, hệ thống mắc **cùng một lỗi mãi mãi** và người duyệt sửa **cùng một thứ mãi
mãi**.

**Thống kê vendor được materialize trong transaction lúc post/approve**, không recompute mỗi node —
recompute trong hot path quá chậm, và "memory lớn dần theo thời gian" mới là điểm cần thể hiện.
Kèm theo đó là lối thoát bắt buộc: `scripts/rebuild_vendor_profiles.py` (🔲 M4.3) tính lại toàn bộ
từ `invoices`. **Bất cứ thứ gì được materialize từ một nguồn dẫn xuất đều phải có đường khôi phục.**

---

# B — Flow ngang

## B1 — Phát hiện trùng lặp

**Trạng thái**: 🚧 — lớp cứng ✅, lớp mềm 🔲 M4.5

Hai lớp, cố ý khác nhau:

| Lớp | Cơ chế | Trả lời | Trạng thái |
|---|---|---|:--:|
| **Cứng** | `UNIQUE(seller_tax_code, template_no, invoice_series, invoice_number)` | *"File này đã vào chưa?"* | ✅ |
| **Mềm** | `NEAR_DUPLICATE` từ `domain/anomaly.py` | *"Có ai đang cố được trả tiền hai lần không?"* | 🚧 tính được ✅, chưa nối vào graph |

```
upload lại cùng một file
  → insert_row với ON CONFLICT DO NOTHING ... RETURNING
  → trả về None (KHÔNG raise)                       ✅ idempotent
  → caller route sang xử lý trùng lặp

hóa đơn KHÁC số, cùng NCC, cùng tổng tiền, cách nhau ≤ 7 ngày
  → near_duplicate() → Finding(NEAR_DUPLICATE, warn, raised_by='anomaly')
  → cờ mềm cho người duyệt biết VÌ SAO                🔲 M4.5 persist
```

**`invoice_number` một mình KHÔNG unique giữa các người bán** — hai nhà cung cấp khác nhau đều có
hóa đơn số `00001234`. Sai khóa này thì toàn bộ chức năng phát hiện trùng lặp trở thành vô nghĩa.

> ⚠️ `NEAR_DUP_WINDOW_DAYS = 7` là hằng số **PROVISIONAL VÀ CHƯA ĐO** — code nói thẳng như vậy.
> Corpus biện minh cho nó là M2-C và con số thật đến từ `docs/eval-report.md` ở M6. Nói ra thì trung
> thực; bịa một lý do thì không.

---

## B2 — Telemetry chi phí

**Trạng thái**: ✅ đầy đủ

![Telemetry chi phí](diagrams/wf_cost_telemetry.png)
*File nguồn Mermaid: [`diagrams/wf_cost_telemetry.mmd`](diagrams/wf_cost_telemetry.mmd)*

### Vì sao ghi NGAY lúc đốt token, không ghi sau khi chạy xong

`_persist_cost()` ✅ mở transaction riêng ngay trong node `vision_extract`. Ghi sau khi run kết thúc
sẽ **mất toàn bộ chi phí đã bị tính** nếu run chết giữa chừng — đúng cái failure mode mà design §2.9
tồn tại để chặn.

### Vì sao `invoice_id` để NULL rồi backfill

Một hóa đơn **PDF-only chưa có row `invoices`** cho tới khi vision đã chạy xong. Node đốt token
không thể gọi tên nó. Vậy nên:

```
vision_extract  → INSERT llm_calls(invoice_id=NULL, thread_id="enrich:INV-0042")
run_one.py      → INSERT invoices → UPDATE llm_calls SET invoice_id=… WHERE thread_id=… AND invoice_id IS NULL
```

> ⚠️ **Nợ đã ghi nhận**: `thread_id` là `enrich:{invoice_id}` nên **ổn định qua các lần chạy lại** —
> chạy lại cùng hóa đơn sẽ backfill cùng row hai lần. Vô hại với một hóa đơn ở M3; **batch của M4
> cần thêm một thành phần theo từng run** (M3-C §8.5). Chính lỗ này đã cắn thật: một test chỉ xanh
> **đúng một lần trên mỗi database**.

### Ghi cả lần THẤT BẠI

Một lỗi 429 vẫn đã đốt token đầu vào. Không ghi thì con số cost **sai**. Provider không gửi usage
payload kèm lỗi, nên input token đến từ `litellm.token_counter` — và phải đếm **theo từng model**,
vì tokenizer khác nhau (26 vs 34 token cho cùng một câu tiếng Việt).

Một `generate()` có thể sinh ra **nhiều** record: hai key bị rate-limit rồi một lần thành công =
**ba** record.

### Hai cột tiền

| Cột | Nghĩa |
|---|---|
| `cost_usd` | Thứ ta thực trả — **$0** trên mọi free tier trong chain |
| `shadow_cost_usd` | Cùng số token đó định giá theo `gpt-4o-mini`, **bất kể model nào trả lời** |

Một con số `$0` trần đọc lên như *"bạn không đo"*, chứ không phải *"rẻ"*.

---

## B3 — Ba loại memory

**Trạng thái**: short-term ✅ · entity 🚧 · episodic 🔲

| Loại | Lưu ở đâu | Ghi khi nào | Đọc ở đâu | Trạng thái |
|---|---|---|---|:--:|
| **Short-term** | `error_history` trong graph state | Mỗi lần validate/parse thất bại | Vòng retry vision, prompt lần sau | ✅ |
| **Entity** | bảng `vendors` | Materialize trong transaction lúc post/approve | `classify`, `signals` (z-score) | 🚧 stub ✅, thống kê 🔲 M4.3 |
| **Episodic** | `episodic_decisions` | Bước `learn` sau mỗi quyết định người | `suggest` trong approval graph | 🔲 M4.4 / M5.4 |

### Vì sao episodic chủ yếu là SQL, không phải vector

Ở quy mô 300–500 hóa đơn, **truy vấn SQL có cấu trúc thắng embeddings**:

```
Kênh 1 (CHÍNH, SQL)     : cùng NCC HOẶC cùng category
                          AND số tiền trong ±1 bucket
                          ORDER BY độ trùng khớp tập cờ DESC, recency DESC
Kênh 2 (phụ, pgvector)  : cosine trên reason_embedding
                          ← CHỈ cho lý do free-text của người duyệt,
                            thứ duy nhất thật sự phi cấu trúc
→ fuse + dedupe
```

Trung thực hơn, chính xác hơn ở quy mô này, và **bớt được một service**. `repositories/episodic.py`
✅ đã có kênh 1; pgvector là M7.6 và **chỉ nếu còn giờ**.

---

## B4 — Vòng đời một cờ

**Trạng thái**: 🚧 — tính ✅, persist 🔲 M4

![Vòng đời một cờ](diagrams/wf_flag_lifecycle.png)
*File nguồn Mermaid: [`diagrams/wf_flag_lifecycle.mmd`](diagrams/wf_flag_lifecycle.mmd)*

**Ba chỗ nguyên tắc "LLM không bật cờ" được thi hành**, và chúng độc lập với nhau:

1. **`domain/` là hàm thuần, zero I/O.** Không module nào ở đó chạm database, đồng hồ, mạng hay LLM.
   Cờ là **giá trị trả về**, không phải side effect.
2. **`Finding` là frozen dataclass.** Một bản ghi về việc đã xảy ra không được sửa sau đó.
   `findings.make()` lấy `severity` và `raised_by` **từ registry**, nên chúng không thể bất đồng với
   `code`.
3. **`CHECK (raised_by IN ('validation','taxcode','anomaly','budget','duplicate','vendor_memory'))`**
   — danh sách này là **mã hóa DDL của nguyên tắc**. Thêm một agent vào đó **đòi hỏi một migration**,
   nên nó không thể xảy ra vô tình trong một node.

Kèm theo: từ vựng cờ bị **nhân bản có chủ đích** giữa `domain/findings.py` và `db/models.py` (vì
`domain/` không được import `db/`), và `test_domain_findings.py` khẳng định hai bản bằng nhau. Lệch
nhau thì **CI đỏ**, thay vì `IntegrityError` lúc runtime trên một hóa đơn thật.

> ⚠️ **Trạng thái hôm nay**: `raise_flag()` tồn tại trong repository nhưng **chưa có caller
> production**. `enrichment_graph` giữ findings trong state và không persist — M3 chốt bề mặt DB của
> graph là `llm_calls`. M4 nối phần còn lại.

---

# C — Flow vận hành

## C1 — Bootstrap môi trường

**Trạng thái**: ✅

```bash
make install    # uv venv --python 3.13 + uv pip install -e ".[dev]"
make dev        # docker compose up -d postgres
                #   → chờ healthcheck pg_isready          ← không có bước này thì migration
                #   → alembic upgrade head                   chạy trước khi Postgres nhận
                #   → python -m scripts.seed_reference        connection và fail NGẪU NHIÊN
make generate   # 300 hóa đơn, seed 42 → byte-identical mỗi lần
make check      # ruff + mypy + pytest   ← chạy TRƯỚC KHI tuyên bố xong việc
```

Thứ tự khởi tạo, và mỗi bước tồn tại vì một lý do đã đo:

![Bootstrap môi trường](diagrams/wf_bootstrap.png)
*File nguồn Mermaid: [`diagrams/wf_bootstrap.mmd`](diagrams/wf_bootstrap.mmd)*

**Hai cái bẫy đã cắn thật**, ghi lại để không cắn lần nữa:

- `init_db.sql` **chỉ chạy khi volume được khởi tạo lần đầu** (`docker-entrypoint-initdb.d`). Vì
  vậy role `invoice_app` được tạo trong **migration** chứ không phải ở đó — nếu để trong
  `init_db.sql` thì mọi volume dev đang tồn tại sẽ **âm thầm thiếu role** cho tới lần
  `make db-reset`, và CI cần thêm một bước.
- CI dùng `services:` nên **không** chạy `docker-entrypoint-initdb.d` → `init_db.sql` phải được áp
  thủ công bằng `psql`, rồi mới `alembic upgrade head`. Lỗ này sống sót tới 2026-07-27: workflow tạo
  schema nhưng **không bao giờ chạy migration**, vô hình cho tới khi có test cần một cái bảng.

---

## C2 — Record/replay fixture

**Trạng thái**: ✅

Một cơ chế, hai vấn đề: **CI không có API key**, và **demo trực tiếp không được hỏng vì rate limit
của người khác**. Demo luôn hỏng đúng lúc tệ nhất; cơ chế này xóa hẳn failure mode đó.

![live / record / replay](diagrams/wf_llm_modes.png)
*File nguồn Mermaid: [`diagrams/wf_llm_modes.mmd`](diagrams/wf_llm_modes.mmd)*

### Quy trình cập nhật fixture

```
1. đổi prompt / model / schema
2. LLM_MODE=record  + API key thật → chạy test cần fixture
3. review fixture đã ghi           ← ⚠️ messages lưu Ở DẠNG RÕ
4. LLM_MODE=replay                 → toàn bộ suite phải xanh, KHÔNG cần key
5. commit fixture cùng với thay đổi prompt
```

**Bước 3 không được bỏ**: fixture lưu messages ở dạng rõ để reviewer đọc được cái gì thực sự đã gửi
tới model. **Chỉ hóa đơn ẩn danh hoặc synthetic mới được record.** M3 mở đầu bằng việc phát hiện sáu
PDF nhà cung cấp **thật** đã bị commit — một replay fixture là **cùng loại container plaintext im
lặng**, và không có lớp tự động nào trong repo này nhìn vào nội dung của nó.

### Vì sao replay miss RAISE thay vì gọi mạng

Fallthrough biến một fixture thiếu thành **hai failure trông giống hệt nhau từ bên ngoài**: một lỗi
bí ẩn trên runner không key, hoặc một test **âm thầm tiêu tiền**.

`generate_stream()` **từ chối chạy hẳn** dưới `replay` — streaming chưa có định dạng fixture, và để
nó rơi xuống sẽ khiến `LLM_MODE=replay` gọi mạng thật, phá hủy đúng cái tính chất mà mode này tồn
tại để bảo đảm.

---

## C3 — Phục hồi sau sự cố

**Trạng thái**: 🔲 M5.5 · Cơ chế nền ✅ đã đo bằng `kill -9` 10/10

```
restart
  → scripts/recover_pending.py
      SELECT * FROM app.approvals WHERE decision IS NULL      ← DANH SÁCH CỦA BẠN
      với mỗi thread_id:
          graph.get_state(config)  → còn interrupt treo?
              còn   → đưa lại vào hàng chờ UI
              không → cảnh báo: DB nói pending nhưng graph đã xong (bất nhất)
```

### Vì sao cần script này khi state đã ở Postgres rồi

> Cái mất **không phải state** — mà là **danh sách "thread nào đang chờ"**.

LangGraph không có API kiểu *"liệt kê mọi thread đang pending"*; bạn phải **tự biết mà hỏi từng
thread một**. Vì vậy `approvals WHERE decision IS NULL` **là** danh sách đó, và nó nằm trong schema
nghiệp vụ chứ không phải trong checkpoint.

Đây là một hệ quả trực tiếp của nguyên tắc: **checkpoint là TẠM THỜI, `approvals` và `audit_log` mới
là vĩnh viễn. Không bao giờ đọc một sự thật nghiệp vụ ra từ checkpoint.** API không bao giờ được giả
định một registry trong RAM sống sót qua restart.

### Vòng đời checkpoint

| Thread | Xóa khi |
|---|---|
| `batch:{uuid}` | hoàn tất + 7 ngày |
| `appr:{invoice_id}` | đã quyết định + 30 ngày |

---

## C4 — Eval

**Trạng thái**: 🔲 M6.7 · Corpus + manifest ✅ đã sẵn sàng

```
make generate                 ✅ 300 hóa đơn + manifest.jsonl (ground truth)
  → scripts/eval.py           🔲 M6.7
      chạy từng stage         → so với expected_finding_codes
      chạy baseline keywords_vi
  → docs/eval-report.md       🔲 M6.7
```

### Vấn đề lớn nhất: tuần hoàn

> Bạn tự sinh XML, tự sinh ground truth, **và** tự viết parser. 100% F1 nghĩa là **con số không**,
> và người phỏng vấn sẽ chọc thủng chỗ này đầu tiên: *"vậy là bạn tự chấm bài của mình?"*

Bốn cách phá vòng tuần hoàn, và ba cái đầu **đã có hiệu lực**:

| # | Cơ chế | Trạng thái |
|:--:|---|:--:|
| 1 | Generator viết từ Jinja template; parser đọc từ path map viết **độc lập**. `test_generator_independence.py` chặn mọi import qua lại | ✅ |
| 2 | `tests/fixtures/real_samples/` — 8 XML **thật** từ 8 nhà cung cấp, bắt buộc parse được | ✅ 8/8 |
| 3 | Mọi metric LLM báo cáo **kèm baseline `keywords_vi`** (nằm trong `expense_categories.keywords_vi`, đã seed) | 🚧 baseline ✅, đo 🔲 M6 |
| 4 | Negative control cho injection test — bản sinh đôi đã gỡ payload | ✅ đã sinh, 🔲 M6.3 đo |

`keywords_vi` **không phải trang trí** — nó chính là baseline không-LLM. Nguyên tắc 3 trong plan §0:
classification phải **thắng baseline ≥ 15 điểm**; không thắng thì node đó **không cần LLM**.

### Metric headline

> **Flag false-positive rate**, không phải accuracy.

Vì nó đo đúng thứ quyết định hệ thống có được dùng thật hay không: **mức độ mệt mỏi của người
duyệt**. Một hệ thống gắn cờ 40% hóa đơn sẽ bị tắt trong tuần thứ hai, dù accuracy có đẹp đến đâu.

### Báo cáo trung thực là một yêu cầu, không phải lời khuyên

> *"Auditor override precision 0.52 — chỉ vừa đủ có lợi; tôi giữ lại vì 0.52 đó tập trung ở nhóm hóa
> đơn giá trị cao, đây là breakdown"* **ấn tượng hơn hẳn** một báo cáo tuyên bố 0.98 khắp nơi.

Kỹ thuật bị bỏ → **ghi lại lý do**; bản thân việc đó là nội dung portfolio, không phải thất bại.

> ⚠️ **Nợ chặn M6**: nhánh vision **chưa có phép đo accuracy nào** — synthetic lẫn thật — và chưa có
> API key nhìn được nào được cấu hình. M6 **không thể** báo cáo con số vision cho tới khi trả nợ này.

---

## C5 — Truy vấn qua MCP

**Trạng thái**: 🔲 M7.1–7.4

```
Claude Desktop  →  FastMCP server  →  tools/registry.py  →  repositories  →  Postgres
                                       ↑ CÙNG registry sinh ra cả LangGraph executor
```

Câu hỏi thật, demo được trong 60 giây: *"phòng nào vượt ngân sách tháng 7?"* · *"vì sao
HDDT-00019999 bị gắn cờ?"* · *"liệt kê hóa đơn từ MST 0312345678"*.

### Bề mặt được phép, và bề mặt bị cấm

| Nhóm | Nội dung |
|---|---|
| **7 read tool** | `search_invoices` · `get_invoice_detail` · `explain_flags` · `get_budget_status` · `get_vendor_profile` · `spend_summary` · `list_pending_approvals` |
| **1 write tool** | `submit_approval_decision` — gate bằng `MCP_ALLOW_APPROVALS` + actor token; audit log ghi `actor='mcp:<client>'` |
| **TUYỆT ĐỐI KHÔNG** | `create_journal_entry`, `post_batch`, mọi mutation ngân sách/vendor |

**Vì sao `create_journal_entry` không bao giờ được expose**: MCP là **stateless request/response,
không biết gì về approval state**. Expose nó ⇒ bất kỳ client nào cũng post được vào sổ cái ⇒ **phá
hủy đúng cái safety property mà cả dự án tồn tại để chứng minh**.

Còn `submit_approval_decision` thì được, vì nó là write **do con người ủy quyền**, đi qua đúng đường
`Command(resume=)` và đúng ràng buộc DB. Nó **làm mạnh thêm** câu chuyện HITL: interrupt là
**transport-agnostic**.

`test_no_write_tools_in_mcp.py` (🔲 M7.3) giữ quyết định này: mọi `ToolSpec` có
`side_effect=="write"` **và** `mcp_exposed=True` phải nằm trong allowlist tường minh
`{submit_approval_decision}`.

---

# D — Flow phát triển

## D1 — Vòng lặp một milestone

**Trạng thái**: ✅ — đây là quy trình đã chạy qua M0→M3

![Vòng lặp một milestone](diagrams/wf_milestone_loop.png)
*File nguồn Mermaid: [`diagrams/wf_milestone_loop.mmd`](diagrams/wf_milestone_loop.mmd)*

**Quy tắc không thương lượng**: *"Không đạt gate thì không mở milestone sau."*

### Điều đáng ghi nhất về vòng lặp này

**Ba milestone gần nhất đều đóng ĐỎ ở lần đầu**, và review độc lập bắt lại:

| Milestone | Review tìm ra cái gì |
|---|---|
| M2-A | Guard M1.6 bị viết lại thành **mù trên CI**; artefact **bịa** một constraint không tồn tại; 32 diff ma của autogenerate |
| M2-B | Một mutation guard **xanh khi luật đã bị phá** |
| M2-C | `make check` **ĐỎ** lúc đóng milestone; `scripts/` chưa từng được lint; import chết |
| M3-A | `LLM_MODE=record` **không bao giờ có thể ghi một fixture THẤT BẠI** — lý do duy nhất khiến `CachedResponse.error` tồn tại |
| M3-B | Note đóng milestone tiêu đề "verbatim" nhưng **bỏ đúng tiêu chí bị sai**; bịa ra open question thay cho hai cái thật |
| M3-C | **Commit đóng milestone là ĐỎ**; hai test chỉ xanh **đúng một lần trên mỗi database**; một mutation được làm cho đỏ bằng cách khẳng định một list chứa chính nó |

Điều này được ghi lại công khai trong plan §7 chứ không bị dọn đi. Nó là bằng chứng rằng bước
**review độc lập là load-bearing**, không phải nghi thức — và rằng *"implement báo đã đạt"* không
phải là bằng chứng.

### Quy tắc hằng số

> Mọi hằng số **không hiển nhiên** trong code phải có comment ghi **ngày + phép đo + tên file
> report** biện minh cho nó.

Và khi không đo được thì **nói thẳng là chưa đo** — xem `domain/anomaly.py`. Bịa một lý do là cách
duy nhất khiến quy tắc này trở nên vô dụng.

---

## D2 — Mutation guard

**Trạng thái**: ✅ — quy ước then chốt của dự án

> **Test xanh không chứng minh gì. Test ĐỎ KHI LUẬT BỊ PHÁ mới chứng minh.**

```
1. chọn một luật load-bearing
2. PHÁ nó trong source (không phải trong test)
3. chạy suite → guard phải ĐỎ
4. khôi phục source
5. ghi lại kết quả vào note đóng milestone
```

Ví dụ thật từ M1 — **8/8 đỏ**:

| Mutation áp lên source | Guard bắt được |
|---|---|
| `defusedxml` → `ElementTree` chuẩn | `test_entity_declarations_rejected` |
| Phá 2/6 đường dẫn `EXT_SCOPES` | `test_header_ext_scopes_are_populated_from_real_files` |
| `money('')` → `Decimal(0)` | `test_empty_element_is_none_not_zero` |
| `ext_bag` đọc con theo **vị trí** | `test_ext_bag_is_order_independent` |
| `locate_hdon`: `len != 1` → `< 1` | `test_multi_invoice_envelope_raises` |
| `include_object` → `return True` | `test_autogenerate_never_drops_langgraph_tables` |

**Hai cái bẫy của chính quy trình này**, cả hai đã xảy ra:

1. **Mutation sai trông giống guard mù.** Ở M3-C, hai mutation của reviewer quay lại **xanh** vì
   mutation nhắm sai chỗ, không phải vì guard hỏng. Ghi lại thay vì lặng lẽ chạy lại.
2. **Script mutation chết giữa chừng** để lại `if True: return` trong source. Phải phát hiện bằng
   cách **đọc lại file** và revert **có phẫu thuật** — `git checkout` sẽ ném đi công việc chưa commit.

---

## D3 — Thêm một migration

**Trạng thái**: ✅

```bash
1. sửa src/invoice_agent/db/models.py
2. .venv/bin/alembic revision --autogenerate -m "m4a_11_<mô tả>"
3. ĐỌC LẠI file sinh ra          ← autogenerate KHÔNG bao giờ đúng hoàn toàn
4. thêm GRANT cho invoice_app nếu bảng mới
5. make migrate && make check
```

**Bước 3 là bắt buộc**, và có ba lý do cụ thể:

- Autogenerate **không sinh ra** `GRANT`/`REVOKE`, `TRIGGER`, hay `FUNCTION` — toàn bộ lớp phòng thủ
  DB là `op.execute()` viết tay.
- Mọi DDL op phải **nói rõ `schema="app"`** (đã xác minh 16/16 `create_table`).
- Connection của Alembic dùng `-csearch_path=public`, **không phải** `app,public`. Với `app` đứng
  đầu, Alembic coi `app` là schema mặc định và phản chiếu bảng dưới key `None` trong khi `models.py`
  khai báo dưới `'app'` ⇒ một lần autogenerate thăm dò trên database **khớp hoàn toàn** vẫn sinh ra
  **32 diff** (16 `remove_fk` + 16 `add_fk`), và các lệnh `op.drop_constraint` sinh ra **thiếu**
  `schema='app'` nên chạy sẽ lỗi.

**Ràng buộc tuyệt đối**: `include_object` phải tiếp tục loại schema `langgraph`. Không có nó, một
lệnh `--autogenerate` thường lệ sinh ra `DROP TABLE checkpoints` — tức là **xóa sạch mọi HITL đang
chờ duyệt**, âm thầm. Xem [ADR 0002](adr/0002-alembic-langgraph-schema-isolation.md).

---

## D4 — CI

**Trạng thái**: ✅

![CI — hai job](diagrams/wf_ci.png)
*File nguồn Mermaid: [`diagrams/wf_ci.mmd`](diagrams/wf_ci.mmd)*

Bốn quyết định, mỗi cái có lý do đã đo:

| Quyết định | Vì sao |
|---|---|
| **Postgres THẬT**, không sqlite/mock | Cả dự án đứng trên hành vi thật của Postgres — CHECK, REVOKE, schema isolation, checkpointer. Test trên sqlite là **test một hệ thống khác** |
| **`LLM_MODE=replay`, KHÔNG có API key** | Fork PR cũng chạy được test, và test không thể hỏng vì rate limit của người khác |
| **`HITL_RUNS=10`** | Gate M1 đòi 10/10. Local mặc định 3 cho vòng lặp nhanh; `test_ci_runs_the_full_ten` chặn việc dòng đó bị gỡ mà không ai biết |
| **`.venv/bin/…`, KHÔNG `uv run`** | Trong thư mục có `pyproject.toml [project]`, `uv run` vào project mode và **re-resolve từ pyproject**, ném đi cái lock vừa sync (đo 2026-07-26: tạo `uv.lock` 497KB). **Lock chỉ có nghĩa nếu không ai resolve lại sau nó** |

**`gitleaks` dùng CLI trực tiếp, KHÔNG dùng `gitleaks-action@v2`**: action đó quét theo range của
từng push, và commit gốc không có parent nên `<root>^` là revision không tồn tại ⇒ job đỏ (đúng lỗi
CI ngày 2026-07-26). Còn cách vá bằng `BASE_REF` thì chuyển sang quét **đúng một** commit, để lọt
commit gốc 33 file **vĩnh viễn** — và vì push handler chỉ nhìn range của chính nó, sẽ không push nào
quét nó nữa.

---

# E — Bảng trạng thái toàn bộ flow

| Flow | Trạng thái | Sở hữu | Bằng chứng hôm nay |
|---|:--:|:--:|---|
| [A1](#a1--ingest--cost-gate) Ingest & cost gate | 🔲 | M4.6 · M6.6 | — |
| [A2](#a2--enrichment-một-hóa-đơn) Enrichment | 🚧 5/8 node | M4.1 · M4.5 | `test_enrichment_nodes` (9) · `test_enrichment_graph` (6) |
| [A3](#a3--leo-thang-chiến-lược--self-correction) Leo thang & self-correction | ✅ | — | `test_enrichment_routing` (8) |
| [A4](#a4--reconcile-budget-serial) Reconcile budget serial | 🚧 số học ✅ | M4.5 | `test_domain_budget` (6) · `test_generator_budget_case` (5) |
| [A5](#a5--route-outcomes) Route outcomes | 🔲 | M4.6 | — |
| [A6](#a6--approval--hitl) Approval / HITL | 🔲 graph · ✅ cơ chế | M5.1–5.2 | `test_hitl_survives_restart` (4), `kill -9` 10/10 |
| [A7](#a7--post-bút-toán) Post bút toán | 🔲 guard · ✅ DB | M5.3 | `test_schema_constraints` (16) · `test_domain_accounting` (7) |
| [A8](#a8--learn--hitl-nuôi-memory) Learn | 🔲 | M5.4 | — |
| [B1](#b1--phát-hiện-trùng-lặp) Trùng lặp | 🚧 cứng ✅ | M4.5 | `test_repositories` (13) · `test_domain_anomaly` (11) |
| [B2](#b2--telemetry-chi-phí) Telemetry chi phí | ✅ | — | `test_llm_cost` (13) · `test_llm_telemetry` (6) · `test_run_one` (2) |
| [B3](#b3--ba-loại-memory) Memory | 🚧 short-term ✅ | M4.3–4.4 | `test_repositories` |
| [B4](#b4--vòng-đời-một-cờ) Vòng đời cờ | 🚧 tính ✅ | M4 | `test_domain_findings` (4) + toàn bộ `test_domain_*` |
| [C1](#c1--bootstrap-môi-trường) Bootstrap | ✅ | — | `make dev` · `test_migrations` (5) · `test_seed_reference` (4) |
| [C2](#c2--recordreplay-fixture) Record/replay | ✅ | — | `test_llm_cache` (10) · `test_gateway` (41) |
| [C3](#c3--phục-hồi-sau-sự-cố) Phục hồi | 🔲 script · ✅ nền | M5.5 | `test_checkpointer` (2) · `test_serde` (7) |
| [C4](#c4--eval) Eval | 🔲 harness · ✅ corpus | M6.7 | 8 file `test_generator_*` (60) |
| [C5](#c5--truy-vấn-qua-mcp) MCP | 🔲 | M7.1–7.4 | — |
| [D1](#d1--vòng-lặp-một-milestone) Vòng lặp milestone | ✅ | — | 6 note trong `docs/notes/` |
| [D2](#d2--mutation-guard) Mutation guard | ✅ | — | 8/8 · 11/11 · 10/10 · 9/9 · 16/16 · 14/14 · 12/12 đỏ |
| [D3](#d3--thêm-một-migration) Migration | ✅ | — | `test_alembic_ignores_langgraph` (1) |
| [D4](#d4--ci) CI | ✅ | — | `.github/workflows/ci.yml` |

### Bốn thứ không bao giờ được cắt

Kể cả khi lịch bị nén xuống 6 tuần (thứ tự cắt: Next.js → ReportingAgent → pgvector → giảm số PDF
vision), bốn flow này ở lại:

| | Flow | Vì sao |
|:--:|---|---|
| 1 | **HITL durability** ([A6](#a6--approval--hitl)) | ✅ Đã có. Là thứ phân biệt project này với mọi LangGraph tutorial |
| 2 | **Injection test** ([C4](#c4--eval) · M6.3) | 🔲 Có negative control, không thì đang đo nhiễu |
| 3 | **Eval harness** ([C4](#c4--eval)) | 🔲 Không có nó thì mọi con số là lời nói |
| 4 | **Cost tracking** ([B2](#b2--telemetry-chi-phí)) | ✅ Đã có, kể cả lần gọi thất bại |

---

## Phụ lục — đọc tiếp

| Câu hỏi | Đọc |
|---|---|
| Hệ thống được cấu tạo thế nào? | [`Architecture.md`](Architecture.md) |
| Thiết kế đầy đủ, gồm phần chưa làm | [`project-f-system-design.md`](project-f-system-design.md) |
| Milestone, gate, trạng thái | [`Plan_Project_F_2026-07-25.md`](Plan_Project_F_2026-07-25.md) §3, §7 |
| Vì sao cô lập schema `langgraph`? | [`adr/0002-…`](adr/0002-alembic-langgraph-schema-isolation.md) |
| Chuyện gì đã xảy ra ở mỗi milestone? | [`notes/`](notes/) — 6 file |
| Spec & plan chi tiết từng sub-milestone | [`superpowers/specs/`](superpowers/specs/) · [`superpowers/plans/`](superpowers/plans/) |
