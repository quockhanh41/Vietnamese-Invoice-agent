# Bản đồ hàm — kết nối giữa các file ở mức function

**Ngày viết**: 2026-07-30 · **Cập nhật**: 2026-08-01 (đóng M5) · **Bổ sung cho**:
[`Architecture.md`](Architecture.md) + [`Workflow.md`](Workflow.md)
**Trạng thái nền**: M0–M5 đã đóng · phạm vi: **mã đang có trong repo**, không phải thiết kế

> `Architecture.md` cho bạn **danh từ** — module nào là gì.
> `Workflow.md` cho bạn **động từ** — bước nào chạy khi nào.
> Tài liệu này cho bạn **đường dây**: hàm nào gọi hàm nào, **vào cái gì**, **ra cái gì**.
>
> Mỗi chữ ký hàm ở đây được chép từ mã nguồn, không diễn giải. Nếu một chữ ký trong tài liệu
> khác với `src/`, hãy tin `src/` và sửa tài liệu này.

---

## Quy ước đọc

**Chữ ký** viết theo dạng `file::hàm(tham số) -> kiểu trả về`, đường dẫn tính từ gốc repo:

```
scripts/run_one.py::run_one(invoice_id, *, corpus, gateway) -> dict
    └→ graphs/enrichment.py::build_enrichment_graph(*, gateway, checkpointer) -> CompiledGraph
```

Thụt lề + `└→` nghĩa là **cấp gọi sâu hơn một bậc**. `src/invoice_agent/` được lược bỏ khỏi
đường dẫn cho gọn — mọi đường dẫn không bắt đầu bằng `scripts/`, `tests/` hay `migrations/`
đều nằm dưới `src/invoice_agent/`.

**Nhãn trạng thái** giống `Workflow.md`:

| Nhãn | Nghĩa |
|---|---|
| ✅ | Có mã, có caller production, có test |
| 🟡 | Có mã + test, **chưa có caller production** — xem [§14](#14--hàm-đã-có-nhưng-chưa-ai-gọi) |
| 🔲 M*n* | Chưa có file; milestone sở hữu nó |

---

## Mục lục

| § | Nội dung |
|---|---|
| [0](#0--mười-ba-workflow-có-mã-chạy-được-hôm-nay) | Mười ba workflow có mã chạy được hôm nay |
| [1](#1--wf-1-enrichment-một-hóa-đơn) | **WF-1 Enrichment** — chuỗi gọi đầy đủ |
| [2](#2--wf-2-sinh-corpus-tổng-hợp) | WF-2 Sinh corpus tổng hợp |
| [3](#3--wf-3-seed-reference-data) | WF-3 Seed reference data |
| [4](#4--wf-4-migration) | WF-4 Migration |
| [5](#5--wf-5-sinh-lại-ba-sơ-đồ-graph) | WF-5 Sinh lại ba sơ đồ graph |
| [6](#6--wf-6-hitl-durability) | WF-6 HITL durability |
| [7](#7--wf-7-batch_graph) | **WF-7 `batch_graph`** — fan-out rồi join serial |
| [8](#8--wf-8-approval_graph) | **WF-8 `approval_graph`** — quyết định của con người |
| [9](#9--cửa-ghi-duy-nhất--toolsguardspy) | **Cửa ghi DUY NHẤT** — `tools/guards.py` |
| [10](#10--wf-9-http-api) | WF-9 HTTP API |
| [11](#11--wf-10-streamlit-ui) | WF-10 Streamlit UI |
| [12](#12--wf-1113-ba-script-vận-hành) | WF-11…13 Ba script vận hành |
| [13](#13--trục-ngang-config--dsn--engine) | Trục ngang: config → DSN → engine |
| [14](#14--hàm-đã-có-nhưng-chưa-ai-gọi) | **Hàm đã có nhưng chưa ai gọi** |
| [15](#15--bảng-tra-ngược-file--hàm--ai-gọi) | Bảng tra ngược: file → hàm → ai gọi |

---

# 0 — Mười ba workflow có mã chạy được hôm nay

| # | Workflow | Điểm vào | Lệnh | Trạng thái |
|---|---|---|---|---|
| **WF-1** | Enrichment một hóa đơn | `scripts/run_one.py::main` | `python -m scripts.run_one --invoice INV-0001` | ✅ |
| **WF-2** | Sinh corpus tổng hợp | `scripts/generate_invoices.py::main` | `make generate` | ✅ |
| **WF-3** | Seed reference data | `scripts/seed_reference.py::main` | `make seed` | ✅ |
| **WF-4** | Migration | `migrations/env.py` | `make migrate` | ✅ |
| **WF-5** | Sinh lại 3 sơ đồ graph | `scripts/render_graph.py::main` | `python -m scripts.render_graph` | ✅ |
| **WF-6** | HITL durability | `tests/helpers/hitl_child.py::main` | qua `tests/test_hitl_survives_restart.py` | ✅ |
| **WF-7** | Batch nhiều hóa đơn | `graphs/batch.py::build_batch_graph` | qua `POST /batches` hoặc test | ✅ |
| **WF-8** | Một lần duyệt | `graphs/approval.py::build_approval_graph` + `submit_decision` | qua API/UI/test | ✅ |
| **WF-9** | HTTP API | `api/app.py::create_app` | `make api` | ✅ |
| **WF-10** | Streamlit UI | `ui/app.py::main` | `make demo` | ✅ |
| **WF-11** | Dựng lại hàng chờ duyệt | `scripts/recover_pending.py::main` | `python -m scripts.recover_pending` | ✅ |
| **WF-12** | Dựng lại vendor profile | `scripts/rebuild_vendor_profiles.py::main` | `make rebuild-profiles` (+ `--check`) | ✅ |
| **WF-13** | Đo Classifier/Auditor | `scripts/eval_classification.py::main` | `python -m scripts.eval_classification` | ✅ |

**Ba workflow nghiệp vụ**: WF-7 (đưa hóa đơn vào hệ thống), WF-8 (người quyết định), WF-1 (một hóa
đơn, dùng cho demo và debug). WF-9/WF-10 là **transport** cho WF-8 chứ không thêm logic nào — đó
chính là điều làm câu "interrupt là transport-agnostic" chứng minh được. Số còn lại là hạ tầng và
vận hành quanh chúng.

---

# 1 — WF-1: Enrichment một hóa đơn

## 1.1 Toàn cảnh chuỗi gọi

![Chuỗi gọi WF-1](diagrams/fn_enrichment_call_chain.png)
*File nguồn Mermaid: [`diagrams/fn_enrichment_call_chain.mmd`](diagrams/fn_enrichment_call_chain.mmd)*

Sơ đồ **cấu trúc** graph (sinh từ graph đã compile, có test chống trôi lệch):
[`diagrams/enrichment_graph.mmd`](diagrams/enrichment_graph.mmd).

> ⚠️ Sơ đồ chuỗi gọi ở trên vẽ từ M3 và mô tả **năm node đầu**. Ba node M4 thêm vào
> (`classify` → `audit` → `signals`) nằm ở [§1.5](#15-ba-node-m4--classify--audit--signals);
> sơ đồ `.mmd` của graph thì luôn đúng vì nó được sinh lại và so sánh trong test.

## 1.2 Điểm vào — `scripts/run_one.py`

```
scripts/run_one.py::main() -> None
    ├→ argparse: --invoice (bắt buộc), --corpus (mặc định Path("corpus"))
    └→ scripts/run_one.py::run_one(invoice_id: str, *, corpus: Path, gateway: LLMGateway|None) -> dict[str, Any]
```

`run_one` làm đúng bốn việc, theo thứ tự:

| # | Gọi ra | Vào | Ra |
|---|---|---|---|
| 1 | `scripts/generator/manifest.py::read_manifest(path: Path)` | `corpus/manifest.jsonl` | `list[dict]` → index theo `invoice_id`; không thấy id thì `SystemExit` |
| 2 | `graphs/enrichment.py::build_enrichment_graph(gateway=gateway)` | `LLMGateway \| None` | graph đã compile (`checkpointer=None`) |
| 3 | `.invoke(state)` | `EnrichmentState` dựng ở bước 2.5 | state cuối, dạng `dict` |
| 4 | `db/repositories/invoices.py::insert_with_lines(...)` + một `UPDATE` thẳng | `final["extracted"]` | `UUID \| None` |

**State khởi tạo** — `run_one` là chỗ duy nhất trong `scripts/` dựng `EnrichmentState` từ đầu
(`graphs/batch.py::enrich_one` là chỗ còn lại, và nó dựng đủ hơn — xem [§7.3](#73-enrich_one--wrapper-quanh-wf-1)):

| Field | Lấy từ | Ghi chú |
|---|---|---|
| `invoice_id` | tham số CLI | **Là id manifest (`"INV-0042"`), KHÔNG phải UUID của DB** |
| `source_path` | `corpus / basename(entry["path"])` | manifest ghi đường dẫn tương đối gốc repo, chỉ lấy tên file |
| `pdf_path` | `corpus / basename(entry["pdf_path"])` hoặc `None` | Đích leo thang; chỉ 3 mục trong corpus có |
| `source_type` | `"xml" if entry["source_type"] == "xml" else "pdf"` | manifest ghi `xml` \| `pdf_vision` |
| `strategy` | `"xml"` | Giá trị mồi; `route_source` ghi đè ngay |
| còn lại | rỗng / `0` / `"received"` | |

> ⚠️ **`run_one` KHÔNG seed `signals` / `classification` / `audit_verdict`.** Nó vẫn chạy, vì
> LangGraph nhận delta và mọi node M4 đều `state.get(...)`. Nhưng nghĩa là script demo M3 này đang
> dựng state theo hình dạng M3, còn đường production (`enrich_one`) dựng đủ 15 key. Nếu một ngày nào
> đó một node bắt đầu *đọc* `state["signals"]` bằng subscript thay vì `.get`, WF-1 sẽ đỏ và WF-7 thì
> không.

**Backfill chi phí** (bước 4, sau khi có `db_invoice_id`):

```sql
UPDATE app.llm_calls
   SET invoice_id = :db_invoice_id
 WHERE thread_id = 'enrich:INV-0042'
   AND invoice_id IS NULL
```

Vì sao cần: `vision_extract` tiêu token **trước khi** hàng `invoices` tồn tại, nên nó ghi
`invoice_id = NULL` + `thread_id`. `thread_id` là khóa nối duy nhất giữa hai lần ghi.

> ⚠️ **Hai `thread_id` khác nhau.** `llm_calls.thread_id` luôn là `enrich:{invoice_id}` (hai phần,
> do `_persist_cost` ghi). *Thread checkpoint* mà WF-7 truyền vào `graph.invoke` là ba phần
> `enrich:{batch_id}:{invoice_id}` ([§7.2](#72-thread_id_for--trả-nợ-m3-c-85)). Hợp nhất hai cái
> mà không sửa backfill sẽ làm cột `llm_calls.invoice_id` ngừng được điền.

## 1.3 `EnrichmentState` — hợp đồng dữ liệu giữa các node

Mọi node nhận **toàn bộ** state và trả về một **dict một phần** (delta). Cách LangGraph trộn
delta vào state phụ thuộc reducer, và đây là nguồn của phần lớn lỗi tinh vi trong graph này:

| Field | Kiểu | Reducer | Node nào ghi |
|---|---|---|---|
| `invoice_id` | `str` | thay thế | *(chỉ state khởi tạo)* |
| `source_path` | `str` | thay thế | *(chỉ state khởi tạo)* |
| `pdf_path` | `str \| None` | thay thế | *(chỉ state khởi tạo)* |
| `source_type` | `"xml" \| "pdf"` | thay thế | *(chỉ state khởi tạo)* |
| `strategy` | `"xml" \| "vision" \| "manual"` | thay thế | `route_source`, `vision_extract` |
| `extracted` | `InvoiceData \| None` | thay thế | `parse_xml`, `vision_extract` |
| `extraction_attempts` | `int` | thay thế | `vision_extract` |
| `current_errors` | `list[str]` | **thay thế** | `parse_xml`, `vision_extract`, `validate` |
| `error_history` | `list[str]` | **`operator.add`** | `parse_xml`, `vision_extract`, `validate` |
| `findings` | `list[Finding]` | **thay thế** | `validate` |
| `signals` | `list[Finding]` | **`operator.add`** | `audit`, `signals` |
| `llm_calls` | `list[LLMCallRecord]` | **`operator.add`** | `vision_extract`, `classify`, `audit` |
| `classification` | `ClassificationResult \| None` | thay thế | `classify` |
| `audit_verdict` | `AuditVerdict \| None` | thay thế | `audit` |
| `status` | `str` | thay thế | `manual_queue` |

> **Đảo ngược hai cột reducer là hỏng ngay.** `findings` mà cộng dồn thì một lần retry thành
> công vẫn định tuyến như thất bại — findings của lần trước còn nằm trong list. Ngược lại,
> node trả về **cả list** thay vì delta cho `error_history` / `signals` / `llm_calls` sẽ khiến mỗi
> vòng nhân đôi dữ liệu.
>
> `findings` và `signals` nằm ở **hai phía đối nhau** của quy tắc này, và đó là ngữ nghĩa chứ không
> phải tiện tay: `findings` mô tả *lần trích xuất hiện tại* (một retry thay thế nó), `signals` ghi
> lại *điều đã quan sát được* (không thể un-happen).

## 1.4 Năm node trích xuất — đọc gì, trả gì, gọi ra file nào

| Node | Hàm | Đọc từ state | Trả về (delta) | Gọi ra ngoài file |
|---|---|---|---|---|
| `route_source` | `route_source(state) -> dict` | `source_type` | `strategy` | — (một phép so sánh) |
| `parse_xml` | `parse_xml_node(state) -> dict` | `source_path` | `extracted`, `current_errors`, (`error_history` khi lỗi) | `xml_parser.parse_invoice_xml` |
| `vision_extract` | `vision_extract_node(state, *, gateway) -> dict` | `pdf_path` \| `source_path`, `current_errors`, `extraction_attempts` | `strategy`, `extracted`, `extraction_attempts`, `current_errors`, `error_history`, `llm_calls` | `vision.extract`, `_persist_cost` |
| `validate` | `validate_node(state) -> dict` | `extracted` | `findings`, `current_errors`, `error_history` — hoặc **`{}`** khi `extracted is None` | `validation.validate` |
| `manual_queue` | `manual_queue_node(state) -> dict` | — | `status = "manual_queue"` | — |

`vision_extract`, `classify` và `audit` đều được gắn vào graph qua
`functools.partial(…, gateway=gw)`, nên `gateway` là **tham số tiêm**, không phải biến module. Đó là
lý do test thay được gateway mà không phải monkeypatch.

Một hàm phụ trong cùng file, chỉ dùng nội bộ:

```
graphs/enrichment.py::_describe(finding: Finding) -> str
    # Finding(code="ARITHMETIC_MISMATCH", detail={"goods_subtotal": "…"})
    # -> "ARITHMETIC_MISMATCH: goods_subtotal=…, total_amount=…"
```

Chuỗi này đi vào **hai** chỗ: `current_errors` (mà router đọc) và prompt retry của vision
(qua `sanitize.wrap`). Vì thế nó phải là một dòng máy đọc được, không phải văn xuôi.

## 1.5 Ba node M4 — `classify` → `audit` → `signals`

```
graphs/enrichment.py::classify_node(state, *, gateway) -> dict
    │   # extracted is None -> {}  (không có gì để phân loại)
    ├→ agents/classification.py::classify(invoice: InvoiceData, *, gateway,
    │                                     vendor=None, calls) -> ClassificationResult
    │       ├→ agents/classification.py::describe(invoice) -> str      # PUBLIC, vì audit dùng LẠI
    │       ├→ extraction/sanitize.py::wrap(text) -> str
    │       └→ llm/structured.py::call_structured(gateway, response_model=ClassificationResult,
    │                                             prompt, system_prompt=SYSTEM_PROMPT, calls)
    └→ graphs/enrichment.py::_persist_cost(calls, invoice_id=…, node="classify")

graphs/enrichment.py::audit_node(state, *, gateway) -> dict
    │   # extracted is None HOẶC classification is None -> {}
    ├→ agents/audit.py::audit(invoice, proposal: ClassificationResult, *, gateway, calls) -> AuditVerdict
    │       ├→ agents/classification.py::describe(invoice)     # CÙNG một cách render
    │       ├→ extraction/sanitize.py::wrap(…)                 # cả invoice LẪN reasoning của Classifier
    │       └→ llm/structured.py::call_structured(…, model=get_settings().auditor_model_chain, …)
    ├→ graphs/enrichment.py::_persist_cost(calls, …, node="audit")
    └→ agents/classification.py::dispute(result, verdict) -> Finding | None
            └→ domain/findings.py::make(CLASSIFICATION_DISPUTED, **detail)

graphs/enrichment.py::signals_node(state, *, conn=None) -> dict
    │   # extracted is None -> {} ;  conn=None -> tự mở get_engine().begin()
    └→ graphs/enrichment.py::_signals_for(conn, data) -> list[Finding]
            │   # không có seller_tax_code -> []  (không có NCC nào để nhớ)
            ├→ memory/vendor_profile.py::stats_for(conn, tax_code) -> anomaly.VendorStats | None
            ├→ domain/anomaly.py::new_vendor(stats) -> Finding | None
            └→ domain/anomaly.py::price_outlier(total_amount, stats) -> Finding | None
```

**`describe` là public có lý do**: hai cách render khác nhau sẽ khiến một bất đồng giữa Classifier và
Auditor **một phần là artefact của prompt**, chứ không phải bất đồng thật.

**`dispute` là PYTHON quyết định, không phải model.** Ba điều kiện, và cả ba đều là "không có gì để
tranh chấp":

| Điều kiện | Trả về |
|---|---|
| `verdict.agrees` | `None` |
| `verdict.proposed_category is None` | `None` — bất đồng mà không nêu phương án là một phản đối người duyệt không hành động được |
| `verdict.proposed_category == result.category` | `None` — CATEGORY là dữ liệu, `agrees` chỉ là văn xuôi |

**`signals` đọc `vendors` ở trạng thái đầu batch.** Xem [`Architecture.md` §6.2](Architecture.md) —
đảo thứ tự này thì `PRICE_OUTLIER` không hỏng, nó chỉ **ngừng bắn**.

## 1.6 Router — hàm quyết định duy nhất

```
graphs/enrichment.py::route_after_validate(state: EnrichmentState) -> str
    # trả về một trong: "vision_extract" | "manual_queue" | END
```

| Điều kiện (xét theo thứ tự) | Trả về |
|---|---|
| `extracted is None` và `strategy == "xml"` và có `pdf_path` | `"vision_extract"` — leo thang XML → vision |
| `extracted is None` và `strategy == "vision"` và `attempts < 3` | `"vision_extract"` — thử lại |
| `extracted is None`, hết đường | `"manual_queue"` |
| có `extracted`, **không** findings | `END` |
| có `extracted`, có findings, `strategy == "xml"` | `END` — **lỗi DỮ LIỆU, mang cờ đi tiếp** |
| có `extracted`, có findings, `strategy == "vision"`, `attempts < 3` | `"vision_extract"` — có thể là lỗi ĐỌC |
| có `extracted`, có findings, vision hết lượt | `"manual_queue"` |

> ⚠️ **`END` ở đây KHÔNG phải kết thúc graph.** `add_conditional_edges` map `END: "classify"`, nên
> nhánh mà router gọi là "xong" thật ra chảy tiếp sang tier phân loại. Router giữ nguyên từ vựng M3;
> chỉ bảng ánh xạ đổi. Đọc nhầm chỗ này sẽ tưởng hóa đơn sạch không bao giờ được classify.

Hằng số `MAX_VISION_ATTEMPTS = 3` nằm cùng file. Chia XML/vision là **theo strategy**, không
theo mã finding — vì `parse_invoice_xml` là hàm thuần còn vision thì không.

## 1.7 Nhánh XML — chuỗi gọi đầy đủ

```
extraction/xml_parser.py::parse_invoice_xml(raw: bytes) -> InvoiceData
    │   # raise XmlExtractionError và KHÔNG BAO GIỜ raise gì khác
    ├→ xml_parser.py::_read(raw: bytes) -> Element
    │       └→ defusedxml.ElementTree.fromstring  # DefusedXmlException / ParseError -> XmlExtractionError
    ├→ xml_schema.py::locate_hdon(root: Element) -> Element          # HDon trần hoặc TDiep/DLieu/HDon
    ├→ xml_parser.py::_require_node(hdon, "DLHDon/TTChung") -> Element
    ├→ xml_parser.py::_require_node(hdon, "DLHDon/NDHDon/TToan") -> Element
    ├→ xml_parser.py::_line(element: Element, index: int) -> InvoiceLine     # cho mỗi HHDVu
    │       ├→ xml_schema.py::integer(raw: str|None, *, where: str) -> int | None
    │       ├→ xml_schema.py::money(raw: str|None, *, where: str) -> Decimal | None
    │       ├→ xml_schema.py::vat_rate(raw: str|None, *, where: str) -> Decimal | None   # '8%' -> 0.08
    │       └→ xml_schema.py::ext_bag(scope: Element|None) -> dict[str, str]  # TTKhac -> {TTruong: DLieu}
    ├→ xml_parser.py::_party(node: Element|None, field: str) -> str | None
    ├→ xml_parser.py::_grand_total(totals: Element) -> Decimal        # TgTTTBSo, bắt buộc
    ├→ xml_schema.py::required_text(node: Element, path: str) -> str
    ├→ xml_schema.py::issue_date(raw: str|None, *, where: str) -> date
    └→ extraction/models.py::InvoiceData(...)                          # pydantic, extra="forbid"
```

**Vào**: `bytes` thô của file XML. **Ra**: `InvoiceData` — hoặc `XmlExtractionError`.

Ranh giới đáng nhớ: parser **không bao giờ phán xét dữ liệu đúng hay sai**. Nó chỉ nói *đọc
được* hay *không đọc được*. Mọi thứ nó raise, theo cấu trúc, đều là lỗi TRÍCH XUẤT — và đó là
điều làm cho việc chia nhánh ở [§1.6](#16-router--hàm-quyết-định-duy-nhất) quyết định được.

## 1.8 Nhánh Vision — chuỗi gọi đầy đủ

### 1.8.1 Từ node xuống gateway

```
graphs/enrichment.py::vision_extract_node(state, *, gateway) -> dict
    │
    ├→ extraction/vision.py::extract(pdf_bytes: bytes, *, gateway, current_errors, calls) -> InvoiceData
    │       ├→ vision.py::rasterize(pdf_bytes: bytes) -> list[bytes]     # pypdfium2, 150 DPI, PNG mỗi trang
    │       │       # PdfiumError -> VisionExtractionError
    │       ├→ (dựng prompt tiếng Việt; nếu có current_errors thì nối vào cuối)
    │       └→ llm/structured.py::call_structured(
    │                  gateway, *, response_model=InvoiceData, prompt, system_prompt,
    │                  model=list(get_settings().vision_model_chain), images=pages,
    │                  max_attempts=3, calls) -> InvoiceData
    │
    └→ graphs/enrichment.py::_persist_cost(calls, *, invoice_id, node="vision_extract")  # xem §1.10
```

Hai lối vào `extract`, phân biệt bằng một dòng: `source = state.get("pdf_path") or state["source_path"]`
— `pdf_path` khi leo thang từ XML, `source_path` khi hóa đơn vốn chỉ có PDF.

`vision_extract_node` bắt **hai** loại lỗi và biến chúng thành `current_errors`, không cho
thoát ra khỏi graph:

| Exception | Ném từ | Ý nghĩa |
|---|---|---|
| `vision.VisionExtractionError` | `rasterize` | PDF không mở được — chưa từng chạm model |
| `structured.StructuredOutputError` | `call_structured` | 3 lần model đều trả JSON sai schema |

### 1.8.2 Vòng lặp schema — `llm/structured.py`

```
llm/structured.py::call_structured[ModelT](gateway, *, response_model, prompt, system_prompt,
                                           model, images, max_attempts=3, calls) -> ModelT
    │   for _ in range(max_attempts):
    ├→ llm/gateway.py::LLMGateway.generate(current_prompt, system_prompt=, model=,
    │                                      json_mode=True, images=, calls=) -> str
    ├→ response_model.model_validate(json.loads(raw))         # thành công -> return ngay
    └→ structured.py::_retry_prompt(original: str, error: Exception) -> str    # khi hỏng
            └→ extraction/sanitize.py::wrap(text: str) -> str
                    └→ sanitize.py::escape(text: str) -> str    # & trước, rồi < >
    # hết lượt -> raise StructuredOutputError(kèm errors của cả 3 lần)
```

> ⚠️ `sanitize.wrap` ở đây **không phải trang trí**. `str(ValidationError)` của Pydantic trích
> nguyên văn output của model, và một model đọc hóa đơn có payload injection có thể echo payload
> đó vào field. Chuỗi lỗi vì thế quay lại prompt qua một block `<invoice_data>` đã escape.

Chỉ **nửa schema** được retry ở tầng này. Nửa transport (429, fallback model) thuộc gateway —
retry ở cả hai tầng sẽ khiến số `attempt` trong `llm_calls` mất ý nghĩa.

Đây cũng là vòng lặp cứu `CATEGORIES` khỏi một nhãn bịa: `Literal` đóng ⇒ danh mục không tồn tại là
`ValidationError` ⇒ retry, thay vì lọt vào `dispute()` và làm hỏng metric.

### 1.8.3 Bên trong gateway — `llm/gateway.py`

```
LLMGateway.generate(prompt, system_prompt, model, temperature=0.1, max_tokens=2048,
                    json_mode=False, reasoning_effort=None, calls=None, images=None) -> str
    └→ LLMGateway._generate_impl(...) -> tuple[str, str]        # (text, model đã trả lời)
        ├→ _resolve_chain(model: str|list[str]|None) -> list[str]
        │       └→ _usable_models(models) -> list[str]     # bỏ qua ở mode replay
        │               └→ _keys_for(model: str) -> list[str]
        │                       └→ _provider(model: str) -> str      # tách theo tiền tố "/"
        │
        │   for attempt_model in models_to_try:
        │       ├→ _is_thinking_model(model) -> bool
        │       ├→ _normalize_reasoning_effort(model, effort) -> str
        │       for api_key in api_keys:
        ├→ _complete(*, model, messages, temperature, max_tokens, json_mode,
        │            reasoning_effort, api_key, model_kwargs, images) -> tuple[str, int, int]
        │       ├→ llm/cache.py::make_key(*, model, messages, temperature, max_tokens,
        │       │                         json_mode, reasoning_effort, image_digests) -> str
        │       ├── mode == "replay":
        │       │       └→ llm/cache.py::load(cache_dir, key) -> CachedResponse | None
        │       │               # None -> raise LLMReplayMiss ; .error -> raise RuntimeError
        │       └── mode == "live" | "record":
        │               ├→ litellm.completion(...)          # ← chỗ DUY NHẤT chạm mạng
        │               ├→ _clean_thinking(text: str|None) -> str     # bỏ <think>…</think>
        │               └→ llm/cache.py::save(cache_dir, key, *, model, messages, response) -> Path
        │                       # mode == "record", kể cả khi lỗi (fixture ghi lại cả thất bại)
        │
        ├→ _record_attempt(calls, model, *, started, attempt, messages,
        │                  prompt_tokens=None, completion_tokens=None, error=None) -> None
        │       └→ _count_tokens(model, messages) -> int       # litellm.token_counter, offline
        │       # append MỘT LLMCallRecord cho MỖI LẦN THỬ, kể cả lần hỏng
        │
        └→ raise LLMReplayMiss (nếu có miss) | RuntimeError("All models failed…")
```

**Ba mode, một seam.** `_complete` là điểm duy nhất một request thành lời gọi mạng — hoặc
không. Seam đặt **bên trong** vòng lặp model chứ không bọc quanh `generate()`, vì không có API
key thì chain resolve về rỗng và mọi thứ đặt phía trên sẽ nằm trên nhánh CI không bao giờ tới.

**Ba loại "hỏng", ba cách xử lý khác nhau:**

| Loại | Nhận ra bằng | Hành động |
|---|---|---|
| Rate limit / quota | `_is_rate_limit(err) -> bool` | `continue` — **xoay key**, cùng model |
| Lỗi khác | mọi `Exception` còn lại | `break` — **sang model tiếp theo** |
| Thiếu fixture | `cache.LLMReplayMiss` | `break`, **không ghi record** (không token nào bị đốt) |

## 1.9 Nhánh validate — chuỗi gọi đầy đủ

```
domain/validation.py::validate(invoice: InvoiceData) -> list[Finding]
    │   # KHÔNG BAO GIỜ raise. Mọi thứ trả về là lỗi DỮ LIỆU, không phải lỗi đọc.
    ├→ validation.py::_goods_lines(lines) -> list[InvoiceLine]        # loại TChat == 4
    ├→ domain/money.py::goods_subtotal(lines: Sequence[InvoiceLine]) -> Decimal
    │       └→ money.py::line_amount(line: InvoiceLine) -> Decimal
    │               # ThTien, hoặc fallback ext['Amount'] (VNPT), note line = 0
    ├→ validation.py::_check_lines(goods) -> list[Finding]
    ├→ validation.py::_check_words(invoice) -> list[Finding]
    │       └→ money.py::parse_amount_words(text: str|None) -> Decimal | None
    │               # 'Ba  triệu năm trăm ngàn đồng./.' -> Decimal('3500000'); không đọc được -> None
    ├→ validation.py::_check_tax_codes(invoice) -> list[Finding]
    │       └→ domain/taxcode.py::check_tax_code(mst: str|None, *, required=False) -> Finding | None
    │               ├→ taxcode.py::normalise(mst) -> str | None
    │               └→ taxcode.py::is_valid_checksum(mst10: str) -> bool
    └→ domain/findings.py::make(code: str, **detail) -> Finding        # mọi Finding đi qua đây
```

**Vào**: một `InvoiceData`. **Ra**: `list[Finding]` — rỗng nghĩa là sạch.

Các loại finding mà đường dây này sinh ra, cùng ngưỡng:

| Mã | Điều kiện | Hằng số |
|---|---|---|
| `ARITHMETIC_MISMATCH` | `goods_subtotal + vat_total != total_amount` | so sánh **chính xác**, không dung sai |
| `SUBTOTAL_MISMATCH` | `invoice.subtotal` có giá trị **khác 0** và lệch | — |
| `VAT_TOTAL_MISMATCH` | **mọi** dòng hàng đều khai `TThue` và tổng lệch | — |
| `LINE_AMOUNT_MISMATCH` | `\|qty × price − amount\| > tol` | `LINE_AMOUNT_TOLERANCE = 0.01` |
| `LINE_VAT_MISMATCH` | `\|amount × rate − vat\| > tol` | `LINE_VAT_TOLERANCE = 0.50` |
| `WORDS_DIGITS_MISMATCH` | đọc được chữ **và** khác số | không đọc được ≠ finding |
| `ITEM_KIND_MISSING` | có dòng `TChat` vắng | — |
| `TAX_CODE_*` (3 mã) | qua `taxcode.check_tax_code` | MST 12 số: chấp nhận vô điều kiện |

Bốn mã còn lại của `CODE_REGISTRY` (**17 mã** tổng cộng) không đến từ `validate`:

| Mã | Sinh ở |
|---|---|
| `NEW_VENDOR`, `PRICE_OUTLIER` | `signals_node` → `domain/anomaly.py` ([§1.5](#15-ba-node-m4--classify--audit--signals)) |
| `NEAR_DUPLICATE`, `DUPLICATE_INVOICE` | `serial_join` ([§7.4](#74-serial_join--điểm-commit-duy-nhất-của-batch)) |
| `BUDGET_WARNING`, `BUDGET_BREACH` | `domain/budget.py::consume`, gọi từ `serial_join` |
| `CLASSIFICATION_DISPUTED` | `agents/classification.py::dispute` — **Python so hai structured output** |

`findings.make` đọc `CODE_REGISTRY: dict[str, tuple[severity, raised_by]]`, nên `severity` và
`raised_by` **không thể** lệch với mã. Vocabulary trong `domain/findings.py` được nhân đôi từ
`db/models.py` **có chủ đích** — `domain/` không được import `db/` — và
`tests/test_domain_findings.py` khẳng định hai bản bằng nhau.

## 1.10 Ghi chi phí — chuỗi gọi đầy đủ

```
graphs/enrichment.py::_persist_cost(calls: list[LLMCallRecord], *, invoice_id: str,
                                    node: str = "vision_extract") -> None
    │   # if not calls: return  — không mở transaction vô ích
    ├→ db/engine.py::get_engine() -> Engine          # role invoice_app, .begin() = transaction riêng
    └→ llm/telemetry.py::record(conn, records, *, node, invoice_id=None, batch_id=None,
                                thread_id=None, agent=None, trace_id=None) -> list[int]
            ├→ llm/cost.py::actual_cost_usd(record: LLMCallRecord) -> Decimal     # ACTUAL_PRICES
            ├→ llm/cost.py::shadow_cost_usd(record: LLMCallRecord) -> Decimal     # SHADOW_PRICE
            │       └→ cost.py::_price(rates, record) -> Decimal
            └→ db/repositories/llm_calls.py::record_call(conn, *, model, provider, node,
                       prompt_tokens, completion_tokens, cost_usd, shadow_cost_usd,
                       latency_ms, attempt, succeeded, error, invoice_id, batch_id,
                       thread_id, agent, trace_id) -> int
```

**Ba node gọi hàm này**, mỗi node truyền `node=` của mình (`"vision_extract"`, `"classify"`,
`"audit"`) và cùng `thread_id=f"enrich:{invoice_id}"`, **để trống `invoice_id`**. Đó là chỗ
[§1.2](#12-điểm-vào--scriptsrun_onepy) backfill vào.

`telemetry.py` tồn tại như một module riêng để hướng phụ thuộc đi **xuống**: nó import cả
`llm/cost` lẫn `db/repositories`, trong khi gateway chỉ biết dataclass thuần và `db/` không
biết gì về `llm/`. Không có nó, việc định giá sẽ bị lặp ở đúng ba call site trên.

Hai cột tiền, và khác biệt giữa chúng chính là điểm:

| Cột | Hàm | Nghĩa |
|---|---|---|
| `cost_usd` | `actual_cost_usd` | Thực trả — `$0` với mọi model free tier trong chain |
| `shadow_cost_usd` | `shadow_cost_usd` | Cùng số token, tính theo giá `gpt-4o-mini` — luôn cùng một bảng giá, nên so sánh được xuyên 6 model |

## 1.11 Ghi database — chuỗi gọi đầy đủ

```
scripts/run_one.py::run_one  (trong `with get_engine().begin() as conn:`)
    └→ db/repositories/invoices.py::insert_with_lines(conn, data: InvoiceData, *,
               batch_id=None, source_type="xml", source_path=None, raw_xml=None,
               category=None, department=None) -> UUID | None
            ├→ raise MissingSellerTaxCode  nếu không có data.seller_tax_code
            ├→ db/repositories/vendors.py::ensure_vendor(conn, tax_code, name=None) -> None
            │       # INSERT … ON CONFLICT (tax_code) DO UPDATE SET last_seen_at = now()
            │       # KHÔNG BAO GIỜ ghi đè thống kê đã tích lũy
            ├→ db/repositories/reference.py::get_department(conn, department)   # nếu có department
            │       # không tồn tại -> từ chối, thay vì ghi một phòng ban ma
            ├→ invoices.py::insert_row(conn, values: dict) -> UUID | None
            │       # ON CONFLICT ON CONSTRAINT uq_invoices_natural_key DO NOTHING … RETURNING id
            │       # trùng -> trả None, KHÔNG raise
            └→ conn.execute(models.invoice_line_items.insert(), [...])   # bulk, line_no fallback theo vị trí
```

**Ba tính chất của đường dây này**, tất cả đều là lựa chọn thiết kế chứ không phải tình cờ:

1. **Upload lại cùng một file là idempotent, không phải lỗi.** `insert_row` trả `None` khi
   trùng khóa tự nhiên `(seller_tax_code, template_no, invoice_series, invoice_number)`.
   `serial_join` đọc `None` đó thành `DUPLICATE_INVOICE`.
2. **Không có MST người bán thì không có danh tính.** `MissingSellerTaxCode` là `ValueError`.
   `serial_join` **kiểm trước khi gọi** và ghi nhận `TAX_CODE_MISSING` thay vì để exception bay ra;
   **`run_one` thì vẫn chưa bắt nó**, nên trên đường demo M3 ngoại lệ này vẫn thoát ra CLI. Khoảng
   trống có thật, phạm vi hẹp: nó không nằm trên đường production.
3. **Graph chỉ có hai mặt tiếp xúc database**: `llm_calls` INSERT (qua `_persist_cost`) và một lần
   đọc `vendors` (qua `signals`). Việc ghi `invoices` nằm ở **script hoặc `serial_join`**, không nằm
   trong node — nên `enrichment_graph` chạy được mà không cần ghi gì, và test tận dụng đúng điều đó.

## 1.12 Ba chỗ WF-1 chạm ra thế giới bên ngoài

| # | Ở đâu | Chạm gì | Transaction |
|---|---|---|---|
| 1 | `vision_extract` / `classify` / `audit` | `litellm.completion` hoặc file fixture | — |
| 2 | `_persist_cost` (trong node) | `app.llm_calls` INSERT | **riêng**, `get_engine().begin()` |
| 3 | `signals` | `app.vendors` SELECT | **riêng** (hoặc `conn` được tiêm) |
| 4 | `run_one` sau `.invoke()` | `app.vendors`, `app.invoices`, `app.invoice_line_items`, `app.llm_calls` UPDATE | **riêng**, `get_engine().begin()` |

Các transaction tách nhau **có chủ đích**: chi phí phải được ghi ngay khi token bị tiêu, không
chờ tới cuối run. Run chết giữa chừng thì mọi lời gọi đã bị tính tiền vẫn còn dấu vết.

---

# 2 — WF-2: Sinh corpus tổng hợp

**Lệnh**: `make generate` → `python -m scripts.generate_invoices --count 300 --seed 42 --out corpus`

```
scripts/generate_invoices.py::main() -> None
    └→ generate(*, count: int, seed: int, out: Path) -> None
        │   rng = Random(seed)      # MỘT rng duy nhất xuyên suốt; không random module-level,
        │                           # không datetime.now(), không uuid4()
        ├→ generator/vendors.py::build_vendors(rng: Random, count: int) -> list[Vendor]
        │       # 40 vendor: 30 "đã có lịch sử" + 10 dành cho defect new_vendor
        │
        ├→ generate_invoices.py::_make_base_spec(index, vendor, rng) -> InvoiceSpec
        │
        ├── ① ca vượt ngân sách
        │   ├→ generator/defects.py::build_budget_overrun_case(base, rng) -> list[InvoiceSpec]
        │   └→ scripts/seed_reference.py::BUDGETS       # ← PHỤ THUỘC DUY NHẤT ĐƯỢC PHÉP
        │           # đối chiếu tổng ca với hạn mức đã seed; không vượt -> SystemExit
        │
        ├── ② prompt injection + đối chứng
        │   ├→ defects.py::seed_injection(base, payload) -> InvoiceSpec
        │   └→ defects.py::strip_injection(base) -> InvoiceSpec     # twin, chỉ khác đúng payload
        │
        ├── ③ áp hạn ngạch defect còn lại, theo DEFECT_RATIOS
        │   ├→ defects.py::seed_arithmetic_error(base) -> InvoiceSpec
        │   ├→ defects.py::seed_words_mismatch(base) -> InvoiceSpec
        │   │       └→ generator/amounts.py::to_amount_words(amount, thousand="nghìn") -> str
        │   ├→ defects.py::seed_bad_tax_code(base) -> InvoiceSpec
        │   │       └→ generator/vendors.py::invalidate_checksum(mst: str) -> str
        │   ├→ defects.py::seed_wrong_vat_rate(base) -> InvoiceSpec
        │   ├→ defects.py::duplicate_of(base) -> InvoiceSpec
        │   ├→ defects.py::near_duplicate_of(base, rng) -> InvoiceSpec
        │   └→ generate_invoices.py::_inherited_body_defects(source_defects, source_meta,
        │                                                    target_defects) -> list[str]
        │           └→ generator/manifest.py::BODY_DEFECTS        # defect nào "đi theo thân hóa đơn"
        │
        ├── ④ ghi file
        │   ├→ generator/templates.py::render(spec: InvoiceSpec) -> str
        │   │       ├→ jinja hdon.xml.j2  (+ tdiep.xml.j2 khi spec.root == "tdiep")
        │   │       └→ amounts.py::to_amount_words   (qua property InvoiceSpec.words)
        │   ├→ generator/pdf.py::render_pdf(spec) -> bytes
        │   ├→ generator/pdf.py::render_truncated(spec) -> bytes   ┐
        │   ├→ generator/pdf.py::render_blank() -> bytes           ├ nhóm unreadable_pdf, chia theo i % 3
        │   └→ generator/pdf.py::render_degraded(spec) -> bytes    ┘
        │
        └── ⑤ ghi ground truth
            ├→ generator/manifest.py::DEFECT_CODES     # defect -> mã finding kỳ vọng
            └→ generator/manifest.py::write_manifest(path: Path, records: list[ManifestRecord]) -> None
```

**Vào**: `count`, `seed`, `out`. **Ra**: `out/INV-*.xml`, `out/INV-*.pdf`, `out/manifest.jsonl`.

**Hai trường ground truth khác nhau, dễ nhầm:**

| Trường | Trả lời câu hỏi | Được phép lớn lên? |
|---|---|---|
| `defects` | *Đã CỐ Ý gieo gì vào hóa đơn này?* | ❌ — mang tỉ lệ §10.2, gộp thêm là làm sai tỉ lệ |
| `expected_finding_codes` | *Một detector ĐÚNG phải bắt được gì?* | ✅ — gồm cả defect thừa kế từ hóa đơn bị nhân bản |

**Điểm nối ngược duy nhất giữa WF-2 và WF-3**: `generate_invoices.py` import
`scripts.seed_reference.BUDGETS`. Nếu ai đó nâng hạn mức MKT, ba hóa đơn Marketing lặng lẽ
thôi vượt ngân sách và nhóm `budget_overrun` thành 12 hóa đơn không vi phạm gì — hỏng âm thầm,
chỉ lộ ra ở số recall của M6. Nên `generate` **từ chối chạy** thay vì sinh ra corpus đó.

`corpus/manifest.jsonl` sau đó là **đầu vào của WF-1** ([§1.2](#12-điểm-vào--scriptsrun_onepy)) và
của phép đo accuracy vision (bản sinh đôi XML là ground truth): đó là toàn bộ mối nối giữa hai
workflow.

---

# 3 — WF-3: Seed reference data

**Lệnh**: `make seed` → `python -m scripts.seed_reference`

```
scripts/seed_reference.py::main() -> None
    ├→ db/engine.py::get_owner_engine() -> Engine        # role chủ sở hữu, KHÔNG phải invoice_app
    └→ seed_reference.py::seed(conn: Connection) -> None          # idempotent, mọi write là upsert
        ├→ db/repositories/reference.py::upsert_department(conn, code, name_vi) -> None
        ├→ db/repositories/reference.py::upsert_category(conn, code, name_vi, debit_account,
        │                                     vat_account, credit_account, keywords_vi) -> None
        └→ db/repositories/reference.py::upsert_budget(conn, department_code, period, category,
                                              amount_limit, warn_threshold_pct=0.8) -> int
```

Ba hằng dữ liệu nằm ngay trong file, và **là nguồn sự thật**:

| Hằng | Nội dung | Ai đọc |
|---|---|---|
| `DEPARTMENTS` | 5 phòng ban | `seed`; nhân bản (đóng băng) vào `agents/models.py::DEPARTMENTS` |
| `CATEGORIES` | 7 danh mục + tài khoản TT133 + `keywords_vi` | `seed`; `agents/models.py::CATEGORIES`; `scripts/eval_classification.py`; `keywords_vi` là **baseline không-LLM** mà `eval/baseline.py` đọc |
| `BUDGETS` | 5 hạn mức tháng 2026-07 | `seed` **và** `scripts/generate_invoices.py` |

**Hai bản sao, và cả hai đều được test khóa lại.** `src/` không được import `scripts/`, nên
`agents/models.py` khai lại vocabulary dưới dạng `Literal` đóng và `tests/test_agents.py` khẳng định
hai bản bằng nhau — đúng cách sắp xếp mà `domain.findings.SOURCES` có với `db.models.FLAG_SOURCES`.

Vì sao `get_owner_engine` chứ không `get_engine`: seed là việc quản trị. Runtime chạy bằng role
`invoice_app` bị REVOKE bớt quyền; nếu seed cũng dùng role đó thì các REVOKE trở thành trang trí.

---

# 4 — WF-4: Migration

**Lệnh**: `make migrate` → `alembic upgrade head`

```
migrations/env.py  (module level)
    ├→ config.py::get_settings() -> Settings
    ├→ Settings.alembic_sqlalchemy_url -> str          # KHÔNG phải app_sqlalchemy_url
    │       # .replace("%", "%%")  cho ConfigParser của Alembic
    ├→ db/models.py::metadata                          # target_metadata
    └→ env.py::include_object(object, name, type_, reflected, compare_to) -> bool
            # trả False cho mọi thứ thuộc schema `langgraph`
```

**Ba lý do `alembic_sqlalchemy_url` là một property riêng**, không dùng chung với runtime:

1. Scheme phải là `postgresql+psycopg://` — `postgresql://` trần nghĩa là psycopg2 (không cài).
2. `%` trong query param phải escape thành `%%`.
3. `search_path` **không được để `app` đứng trước**. Với `-csearch_path=app,public`, Alembic
   coi `app` là schema mặc định của connection và phản chiếu bảng dưới khóa `None`, trong khi
   `db/models.py` khai dưới `'app'` — đo 2026-07-28: **32 diff ma** (16 remove_fk + 16 add_fk)
   trên một database khớp hoàn toàn với migration.

`include_object` là thứ chặn `alembic revision --autogenerate` sinh `DROP TABLE` cho
`checkpoints` / `checkpoint_blobs` / `checkpoint_writes` — tức chặn một migration vô tình xóa
sạch mọi HITL đang chờ duyệt. `tests/test_alembic_ignores_langgraph.py` chạy autogenerate
**thật** để giữ tính chất này.

**HEAD hiện tại: `39913b627278` (`m5a_13_flag_severity_trigger`), 15 revision tuyến tính.**

---

# 5 — WF-5: Sinh lại ba sơ đồ graph

```
scripts/render_graph.py::main() -> None
    ├→ graphs/enrichment.py::build_enrichment_graph() -> CompiledGraph
    │       └→ .get_graph().draw_mermaid()  → docs/diagrams/enrichment_graph.mmd
    ├→ graphs/batch.py::build_batch_graph() -> CompiledGraph
    │       └→ .get_graph().draw_mermaid()  → docs/diagrams/batch_graph.mmd
    └→ graphs/approval.py::build_approval_graph() -> CompiledGraph
            └→ .get_graph().draw_mermaid()  → docs/diagrams/approval_graph.mmd
```

**Vào**: không có. **Ra**: ba file `.mmd` được commit.

`draw_mermaid()` là renderer **DUY NHẤT chạy offline** (đo 2026-07-29): `draw_mermaid_png()` cần
round-trip mạng tới mermaid.ink, `draw_png()` cần graphviz, `draw_ascii()` raise ImportError khi
thiếu grandalf.

Đây là workflow duy nhất mà **đầu ra là tài liệu**, và cả ba artefact đều có drift guard riêng:

| Artefact | Guard |
|---|---|
| `enrichment_graph.mmd` | `tests/test_enrichment_graph.py` |
| `batch_graph.mmd` | `tests/test_batch_graph.py` |
| `approval_graph.mmd` | `tests/test_approval_graph.py` |

Guard thứ ba được thêm ở M5-B, sau khi review phát hiện `approval_graph.mmd` **vẽ tay từ design doc
rồi đi lạc** ngay khi `graphs/approval.py` ra đời — và closeout note vẫn liệt kê nó là artefact đã
bàn giao. Hai trong ba sơ đồ có guard; cái thứ ba chính là cái không ai regenerate.

> **Không bao giờ sửa tay file `.mmd`.** Chạy `python -m scripts.render_graph`.

---

# 6 — WF-6: HITL durability

**Không còn là spike.** M1 chạy workflow này trên `tests/helpers/spike_graph.py` — một graph đồ
chơi. Từ M5-B, portfolio test 1 được **retarget lên `approval_graph` THẬT**.

```
tests/test_hitl_survives_restart.py
    └→ subprocess: python -m tests.helpers.hitl_child <db_invoice_id> <approval_id>
        └→ tests/helpers/hitl_child.py::main(...) -> None
            ├→ checkpointer.py::build_checkpointer(settings=None) -> Iterator[PostgresSaver]
            │       ├→ config.py::get_settings().checkpointer_dsn      # search_path=langgraph,public
            │       ├→ PostgresSaver.from_conn_string(dsn)
            │       └→ serde.py::build_serde() -> JsonPlusSerializer
            │               # allowed_msgpack_modules: Finding, InvoiceData, InvoiceLine, LLMCallRecord
            ├→ saver.setup()
            ├→ build_counted_graph(...)      # approval_graph THẬT, mỗi node bọc một bộ đếm
            └→ .invoke(state, config={"configurable": {"thread_id": f"appr:{db_id}"}})
    # tiến trình cha SIGKILL, rồi resume qua graphs/approval.py::submit_decision(...)
```

**Số đo là toàn bộ lý do workflow này tồn tại** (2026-07-27 ở M1, xác nhận lại ở M5-B trên graph
thật), sau `kill -9` + resume:

| Node | Vị trí so với `interrupt()` | Số lần chạy |
|---|---|---|
| `suggest` | TRƯỚC cổng | **1** |
| `approval_gate` | CHỨA cổng | **2** |

Và **hệ quả** — phần M1 chưa đo được, vì spike graph không có side effect thật:

```
đúng 1 row journal_entries   ·   đúng 1 audit_log grant   ·   đúng 1 approval đã quyết định
```

Nghĩa là: *node chứa `interrupt()` chạy lại TỪ ĐẦU khi resume*, và hình dạng graph
(`post_entry` là node **riêng, SAU** `approval_gate`) là thứ giữ cho việc đó vô hại.

**`submit_decision(graph_builder=…)` tồn tại cho đúng một caller** — chính là test này. Nó cần graph
có bộ đếm, vì phép đo "`approval_gate` chạy hai lần" **không lấy ra được từ checkpoint history**:
lượt đầu của cổng raise `GraphInterrupt` và không commit gì. Cái giá là một lỗ hổng hình-dạng-test
trong mã production, và nó bị chặn lại bởi `test_the_counted_graph_matches_the_real_one`: graph được
tiêm phải render mermaid **byte-identical** với `build_approval_graph`, nên nó không thể là một graph
khác.

`serde.build_serde()` là mắt xích dễ bỏ sót: không đăng ký kiểu, một `Finding` quay về từ
checkpoint dưới dạng `dict` — im lặng, và hỏng ở xa chỗ gây ra. `tests/test_serde.py` liệt kê
từ một state đã điền đủ, nên thiếu sót fail ở CI chứ không fail trong một checkpoint.

---

# 7 — WF-7: `batch_graph`

**Điểm vào**: `graphs/batch.py::build_batch_graph(*, max_concurrency: int = 5) -> CompiledGraph`
**Lái bởi**: `api/routes/batches.py::_run` (production) hoặc trực tiếp trong test.

## 7.1 Topology

```
START → ingest → cost_gate ─cond─→ fan_out ─Send×N─→ enrich_one → serial_join
                     └─"rejected"→ END
      serial_join → route_outcomes → auto_post → open_approval → finalize → END
```

| Node | Hàm | Ghi DB? |
|---|---|---|
| `ingest` | `ingest_node(state) -> dict` | ✗ |
| `cost_gate` | `cost_gate_node` + `cost_gate_condition(state) -> "ok"\|"rejected"` | ✗ |
| `fan_out` | `fan_out_node` + `fan_out_router(state) -> list[Send]` | ✗ |
| `enrich_one` | `enrich_one_task_node(task) -> dict` | chỉ `llm_calls` (qua WF-1) |
| `serial_join` | `serial_join(state, *, conn=None) -> dict` | ✅ **điểm commit DUY NHẤT** |
| `route_outcomes` | `route_outcomes_node(state, *, conn=None) -> dict` | ✗ (chỉ đọc) |
| `auto_post` | `auto_post_node(state) -> dict` | ✅ qua guard, **1 transaction/hóa đơn** |
| `open_approval` | `open_approval_node(state) -> dict` | ✅ `approvals` + checkpoint |
| `finalize` | `finalize_node(state) -> dict` | ✅ `batches.status` |

**HÌNH DẠNG CHÍNH LÀ LẬP LUẬN**: mọi task fan-out **CHỈ ĐỌC**, mọi lần ghi nằm trong node serial
sau join. Ba tính chất rơi ra cùng lúc — bất biến thứ tự cho ngân sách, bất biến thứ tự cho trùng
lặp, và `signals` không thấy hóa đơn đang chấm trong thống kê của chính nó.

## 7.2 `thread_id_for` — trả nợ M3-C §8.5

```
graphs/batch.py::thread_id_for(batch_id: uuid.UUID | str, invoice_id: str) -> str
    # -> f"enrich:{batch_id}:{invoice_id}"
```

`enrich:{invoice_id}` trần là **ổn định qua các lần chạy lại**: batch thứ hai trên cùng hóa đơn sẽ
resume checkpoint của batch thứ nhất thay vì bắt đầu sạch — và khi đó ngữ nghĩa interrupt/resume mà
M1 chứng minh sẽ áp lên **nhầm run**.

## 7.3 `enrich_one` — wrapper quanh WF-1

```
graphs/batch.py::enrich_one_task_node(task: Any) -> dict
    ├→ graphs/enrichment.py::build_enrichment_graph() -> CompiledGraph   # MỘT graph mỗi task
    └→ graphs/batch.py::enrich_one(task: dict, *, graph) -> dict[str, Any]
            ├→ dựng EnrichmentState đủ 15 key (khác run_one — xem §1.2)
            ├→ graph.invoke(state, {"configurable": {"thread_id": thread_id_for(...)}})
            │       # try/except Exception -> {"enriched": [], "failed": [{invoice_id, error}]}
            └→ -> {"enriched": [EnrichedInvoice], "failed": []}
```

`EnrichedInvoice` (TypedDict) cố tình **nhỏ** — serial join tự dẫn xuất lại mọi thứ khác từ database:

```
invoice_id · source_path · source_type · department · extracted · findings · signals
classification · status
```

Hai chi tiết là sửa lỗi, không phải khẩu vị:

- **`department` KHÔNG có default `"MKT"`.** Một phòng ban mà wrapper không xác định được là một
  *sự thật cần mang theo*, không phải một *giá trị để bịa*: serial join bỏ qua hóa đơn nó không route
  được thay vì đối chiếu nó với ngân sách của người khác.
- **`status` được TRUYỀN TIẾP, không ghi đè.** `manual_queue_node` trả `{"status": "manual_queue"}`
  — một lần bỏ cuộc *có kiểm soát*, khác hẳn một lần enrichment thành công. Hardcode `"enriched"` ở
  đây làm hai thứ đó không phân biệt được, và hàng chờ duyệt của M5 chính là chỗ cần phân biệt.

## 7.4 `serial_join` — điểm commit DUY NHẤT của batch

```
graphs/batch.py::serial_join(state, *, conn=None) -> dict
    └→ graphs/batch.py::_join(conn, state) -> dict
        │   survivors = [e for e in state["enriched"] if e["extracted"] is not None]
        │   ordered   = sorted(survivors, key=(issue_date, invoice_series, invoice_number))
        │                                        ↑ THỨ TỰ CANONICAL, không phải thứ tự file đến
        ├→ db/repositories/batches.py::ensure_batch(conn, batch_uuid) -> None
        │
        │   ── vòng 1: mỗi hóa đơn theo thứ tự canonical ──
        │   ├─ seller_tax_code is None → raised += TAX_CODE_MISSING, `continue`
        │   │      # từng là `assert`, và lần chạy LIVE đầu tiên nó giết cả 30 hóa đơn
        │   ├→ db/repositories/vendors.py::ensure_vendor(conn, tax_code, name)
        │   ├→ db/repositories/invoices.py::insert_with_lines(conn, inv, batch_id=,
        │   │        source_type=, source_path=, category=, department=) -> UUID | None
        │   │      # None (trùng khóa tự nhiên) → raised += DUPLICATE_INVOICE, `continue`
        │   ├→ db/repositories/flags.py::raise_flag(conn, db_id, code=, severity=,
        │   │        raised_by=, detail=)                    # cho MỌI finding VÀ signal
        │   ├→ memory/vendor_profile.py::apply_invoice(conn, tax_code=, amount=,
        │   │        category=, department=)                 # O(1), trong transaction này
        │   └→ domain/anomaly.py::near_duplicate(cand, priors) -> Finding | None
        │            └→ flags.raise_flag(...)                # priors lớn dần theo thứ tự canonical
        │
        │   ── vòng 2: gom theo (department, period) rồi rút ngân sách ──
        │   ├─ không có department → unroutable, `continue`
        │   ├→ SELECT … FROM app.budgets WHERE department_code=… AND period=… FOR UPDATE
        │   │      # không có row → unroutable  (KHÔNG bịa hạn mức)
        │   ├→ domain/budget.py::consume(limit, warn_pct, inputs) -> list[Consumption]
        │   │      # consume() TỰ SẮP XẾP lại đầu vào — thắt lưng cộng dây đeo quần
        │   ├→ db/repositories/budget_consumption.py::record_consumption(conn, budget_id=,
        │   │        invoice_id=, amount=, balance_before=, balance_after=, sequence_no=)
        │   └→ flags.raise_flag(...)                         # BUDGET_WARNING / BUDGET_BREACH
        │
        └── ── vòng 3: ghi status, ĐỌC LẠI TỪ BẢNG ──
            ├→ db/repositories/flags.py::open_flags_for(conn, db_id) -> list[dict]
            └→ db/repositories/invoices.py::set_status(conn, db_id, "flagged" | "validated")
```

**Vì sao status được ghi ở vòng 3 chứ không trong vòng 1**: cờ ngân sách được raise *sau* vòng 1, nên
một hóa đơn sạch lúc insert có thể nhận `BUDGET_BREACH` trước khi node này trả về. Tính status sớm
sẽ ghi `validated` trong khi `invoice_flags` nói ngược lại — và guard đọc **bảng**.

**Ra**: `{"persisted": [...], "consumption": [...], "raised": [...], "unroutable": [...]}`.

## 7.5 `route_outcomes` — đọc bảng, không đọc state

```
graphs/batch.py::route_outcomes_node(state, *, conn=None) -> dict
    └→ graphs/batch.py::_route(conn, state) -> dict
        │   db_ids = {p["invoice_id"]: UUID(p["db_id"]) for p in state["persisted"]}
        │   for e in state["enriched"]:
        │       ├─ db_id is None                      → needs_approval  (enrich xong nhưng chưa persist)
        │       ├→ db/repositories/flags.py::open_flags_for(conn, db_id)
        │       └─ có severity ∈ tools/guards.py::BLOCKING_SEVERITIES → needs_approval
        │                                                       ngược lại → auto
        │   for f in state["failed"]:                 → needs_approval
        └→ {"auto": [...], "needs_approval": [...], "status": "routed"}
```

Hai phép đo đứng sau node này, cả hai đều ngày 2026-08-01 trên batch gate 30 hóa đơn:

| Nếu route theo… | Kết quả đo được |
|---|---|
| `findings`/`signals` **trong graph state** | **8 hóa đơn** vào nhánh auto rồi bị guard từ chối, cả 8 vì `BUDGET_BREACH` — cờ ngân sách sinh ra *sau* fan-out nên không bao giờ có trong kết quả của một task |
| **sự tồn tại** của cờ, bất kể severity | **28/28** hóa đơn tới người duyệt, `auto` rỗng — `NEW_VENDOR` (severity `info`) bắn ở lần đầu gặp mỗi NCC, 51 lần trên corpus |

Chạy **cùng một vị từ trên cùng những dòng dữ liệu** mà guard sẽ đọc là thứ làm hai bên đồng ý
**theo cấu trúc**, chứ không phải theo may mắn. `BLOCKING_SEVERITIES` được import từ `tools/guards.py`
đúng vì lý do đó — và migration 13 đưa cùng vị từ ấy xuống trigger.

## 7.6 `auto_post` và `open_approval`

```
graphs/batch.py::auto_post_node(state) -> dict
    for invoice_key in state["auto"]:
        with get_engine().begin() as conn:            # MỘT transaction MỖI hóa đơn
            ├→ db/repositories/invoices.py::get_by_id(conn, db_id) -> dict | None
            ├→ db/repositories/reference.py::get_category(conn, category) -> dict | None
            │       # None -> to_human, `continue`
            ├→ domain/accounting.py::CategoryAccounts(...)         # TT133, từ seed data
            ├→ domain/accounting.py::build_journal_entry(extracted, accounts) -> list[JournalLine]
            └→ tools/guards.py::create_journal_entry(conn, invoice_id=, posting_authority="auto_clean",
                       entry_date=, lines=, actor="system") -> UUID
                   # GuardDenied -> to_human, `continue`  (guard đã tự ghi audit_log)
    -> {"posted": [...], "auto": [...], "needs_approval": [...]}
```

```
graphs/batch.py::open_approval_node(state) -> dict
    ├→ checkpointer.py::build_checkpointer() -> PostgresSaver      # MỘT cái cho cả node
    ├→ graphs/approval.py::build_approval_graph(checkpointer=saver)
    └→ for invoice_key in needs_approval ∩ persisted:
        ├→ db/repositories/approvals.py::open_approval(conn, invoice_id=, thread_id=f"appr:{db_id}",
        │        interrupt_payload={}, flags_snapshot={}, suggestions={}) -> UUID
        ├→ graph.invoke(initial, {"configurable": {"thread_id": f"appr:{db_id}"}})
        └─ "__interrupt__" không có trong kết quả → unopened += {"reason": "no interrupt raised"}
    -> {"opened": [...], "unopened": [...], "status": "approvals_opened"}
```

**Batch KHÔNG chờ.** Mỗi thread chạy tới `interrupt()` rồi dừng, toàn bộ state checkpoint xuống
Postgres, nên người duyệt có thể quyết định vài giờ sau từ một tiến trình khác. Đó là toàn bộ lý do
biện minh cho ba graph, và `test_batch_not_blocked_by_pending.py` là phép đo của nó.

**Một checkpointer cho cả node**, không phải một cho mỗi hóa đơn: một `PostgresSaver` mỗi hóa đơn sẽ
mở một connection pool mỗi hóa đơn để chẳng làm thêm gì.

**`auto_post` bắt `GuardDenied`, `post_entry` thì KHÔNG** (§8.4) — và bất đối xứng đó là chủ đích.

---

# 8 — WF-8: `approval_graph`

## 8.1 Topology và hai điểm vào

```
START → load_snapshot → suggest → approval_gate ⏸ → apply_decision
apply_decision ─cond→ { approved|edited: post_entry, rejected: record_rejection } → learn → END
```

| Điểm vào | Hàm | Ai gọi |
|---|---|---|
| **Mở** thread, chạy tới interrupt | `build_approval_graph(checkpointer=).invoke(initial, config)` | `batch.open_approval_node` |
| **Resume** thread bằng quyết định | `approval.submit_decision(...)` | `api/routes/approvals.py::decide`, test, script |

## 8.2 `ApprovalState` — JSON thuần, có lý do

```
invoice_id · approval_id · snapshot · flags · suggestions · decision · decided_by
reason · corrections · entry_id · status
```

Một thread `appr:` sống **hàng giờ tới hàng ngày** trong khi mọi thread khác sống vài phút, nên
checkpoint của nó phải sống sót qua một lần deploy rơi vào giữa interrupt và quyết định.

```
graphs/approval.py::_jsonable(value) -> Any
    # Decimal -> str, date -> isoformat, UUID -> str, đệ quy vào dict/list
```

Đổi kiểu **ở BIÊN**, không phải sâu trong node: cả hai đích đến (`JSONB` và checkpoint) đều cần, và
một chỗ để nhìn khi một con số tới nơi dưới dạng chuỗi.

> ⚠️ **`decided_by` là tên TRẦN.** `approvals.decided_by` lưu `"ketoan"`; `audit_log.actor` lưu
> `"human:ketoan"`. `post_entry` là chỗ **DUY NHẤT** thêm tiền tố. Thêm vào một giá trị đã có tiền
> tố cho ra `"human:human:ketoan"` và **không constraint nào bắt được**.

## 8.3 Bốn node trước cổng

```
load_snapshot_node(state, *, conn=None) -> dict
    └→ _load_snapshot(conn, state)
        ├→ db/repositories/invoices.py::get_by_id(conn, invoice_id) -> dict | None
        │       # None -> ValueError (approval mở cho một hóa đơn không tồn tại)
        ├→ db/repositories/invoices.py::get_lines(conn, invoice_id) -> list[dict]
        └→ db/repositories/flags.py::open_flags_for(conn, invoice_id) -> list[dict]
    -> {"snapshot": {…, "lines": [...]}, "flags": [...], "status": "loaded"}
        # raw_xml bị loại khỏi snapshot

suggest_node(state, *, conn=None) -> dict
    └→ _suggest(conn, state)
        ├→ db/repositories/episodic.py::similar_episodes(conn, vendor_tax_code=,
        │        category=, limit=5) -> list[dict]         # KHÔNG LLM
        └→ db/repositories/approvals.py::record_context(conn, approval_id, *,
                 interrupt_payload=, flags_snapshot=, suggestions=)
                 # ghi TRƯỚC cổng — xem §8.4 điểm 1

approval_gate_node(state) -> dict
    └→ langgraph.types.interrupt({"invoice": …, "flags": …, "suggestions": …})
        # và KHÔNG GÌ KHÁC trong node này
    -> {"decision", "decided_by", "reason", "corrections"}  # từ giá trị resume

apply_decision_node(state) -> dict            # ⚠️ KHÔNG có tham số conn
    with get_engine().begin() as conn:
        ├→ db/repositories/approvals.py::record_decision(conn, approval_id, *, decision=,
        │        decided_by=, reason=, field_corrections=)
        └→ for row in flags.open_flags_for(conn, invoice_id):
               flags.resolve_flag(conn, row["id"])          # đóng cờ ở CẢ HAI kết cục
```

**`suggest` không gọi LLM** — nó nằm trên đường batch phải chờ; một model call ở đây khiến mỗi mục
trong hàng chờ duyệt tốn một token và một round trip. Trên database sạch nó trả `[]`, và điều đó được
**báo cáo như đo được** chứ không seed cho đẹp.

**Cờ được resolve ở cả hai kết cục**, vì con người đã phân xử chúng theo cả hai hướng. `resolved_at`
được set, không xóa gì, nên metric false-positive vẫn đếm được. Hệ quả cần biết: trigger DB thôi
chặn `auto_clean` cho hóa đơn này — nhưng nó vẫn không thể bị post lại, vì `UNIQUE(invoice_id)` và
nhánh idempotent của guard đều còn đó.

## 8.4 Ba điều load-bearing

**1. `approval_gate` chứa `interrupt()` VÀ KHÔNG GÌ KHÁC.** Đo 2026-07-27 (langgraph 1.2.9): node
chứa interrupt chạy **hai lần** qua `kill -9` + resume. Mọi thứ thêm vào đó xảy ra hai lần. Đó là lý
do `record_context` nằm ở `suggest` chứ không ở đây.

**2. `apply_decision` KHÔNG nhận `conn`.** `post_entry` (qua guard) đọc lại `approvals` từ Postgres
để xác nhận quyết định đã persist. Nếu một caller đưa được cho node này đúng transaction mà
`post_entry` sẽ đọc, guard sẽ đang xác minh **write chưa commit của chính caller mình** — và cách
sắp xếp đó **pass mọi test không mutate riêng cho nó**. Không nhận `conn` làm cho nó *không diễn đạt
được*. Mutation đã xác nhận: thêm `conn` vào thì test hành vi **vẫn XANH**.

**3. `post_entry` để `GuardDenied` BAY RA.** `auto_post` bắt và route sang người duyệt, nhưng ở đây
một người duyệt **đã** quyết định rồi — guard từ chối nghĩa là guard và con người bất đồng, và điều
đó phải nổi lên. Thread vẫn resume được, nên bất đồng có thể đưa lại cho họ xem.

## 8.5 `post_entry` — và vì sao nó dựng lại từ DB

```
post_entry_node(state) -> dict
    with get_engine().begin() as conn:
        ├→ db/repositories/invoices.py::get_by_id(conn, invoice_id)
        ├→ db/repositories/reference.py::get_category(conn, row["category"]) -> dict | None
        │       # None -> ValueError: "duyệt rồi nhưng category không có tài khoản nào được seed"
        ├→ graphs/approval.py::_rebuild_invoice_data(conn, invoice_id) -> InvoiceData
        │       ├→ invoices.get_by_id / invoices.get_lines
        │       └→ InvoiceData(...) + [InvoiceLine(...)]        # dựng lại TỪ DATABASE
        ├→ domain/accounting.py::build_journal_entry(invoice_data, accounts) -> list[JournalLine]
        └→ tools/guards.py::create_journal_entry(conn, invoice_id=, posting_authority="human_approved",
                   approval_id=, entry_date=, lines=, actor=f"human:{decided_by}") -> UUID
    -> {"entry_id": str(...), "status": "posted"}
```

**Dựng lại từ DATABASE, không từ snapshot.** Snapshot là JSON đã stringify để *người* đọc, và có thể
đã vài ngày tuổi. Bút toán phải tính từ nguồn sự thật, bằng **đúng** `build_journal_entry` mà nhánh
auto dùng — nếu không, hai nhánh có thể ghi hai con số khác nhau cho cùng một hóa đơn.

## 8.6 `learn` — HITL nuôi Memory

```
learn_node(state) -> dict
    │   # không có seller_tax_code -> {"status": "learned"}, không ghi gì
    with get_engine().begin() as conn:
        ├→ db/repositories/episodic.py::record_episode(conn, invoice_id=, approval_id=,
        │        vendor_tax_code=, amount=, flags={"codes": [...]}, decision=,
        │        category=, department=, reason=)
        │       # MỌI quyết định, không chỉ rejected
        ├─ decision == "rejected":
        │   ├→ db/repositories/vendors.py::get_vendor(conn, tax_code)
        │   └→ db/repositories/vendors.py::update_profile(conn, tax_code, rejection_count=+1)
        └─ có corrections:
            ├→ vendors.get_vendor(conn, tax_code)
            └→ vendors.update_profile(conn, tax_code, reviewer_notes=previous + "\n" + entry)
                    # NỐI THÊM, không ghi đè — đây là nhật ký người duyệt viết dần
```

**Ghi episode ở MỌI quyết định** là một mở rộng có chủ đích so với chữ nghĩa của design §5.4 (chỉ vẽ
mũi tên reject): `suggest` hỏi *"lần trước nhà cung cấp này ra sao"*, và câu trả lời đa số là **đã
duyệt**. Một memory chỉ chứa lời từ chối sẽ làm lệch mọi gợi ý nó đưa ra.

Vòng khép kín: `learn` ghi `vendors.reviewer_notes` → `agents/classification.py::classify` đọc
`vendor["modal_category"]` và ngữ cảnh đó ở lần sau. **HITL và Memory là một cơ chế.**

## 8.7 `submit_decision` — seam mà API bọc lại

```
graphs/approval.py::submit_decision(*, invoice_id: str, decision: str, decided_by: str,
                                    reason=None, corrections=None, graph_builder=None) -> dict
    ├─ decision not in VALID_DECISIONS -> ValueError        # TRƯỚC khi chạm graph
    ├→ checkpointer.py::build_checkpointer() -> PostgresSaver
    ├→ (graph_builder or build_approval_graph)(checkpointer=saver)
    └→ app.invoke(Command(resume={decision, decided_by, reason, corrections}),
                  {"configurable": {"thread_id": f"appr:{invoice_id}"}})
```

**Validate TRƯỚC khi chạm graph** vì `route_after_decision` gửi mọi thứ không phải approved/edited
sang `record_rejection` — một lỗi gõ phím chưa validate sẽ **âm thầm từ chối một hóa đơn tốt**.

`route_after_decision` cho `edited` đi đường post: một người duyệt đã sửa một field rồi cho hóa đơn
đi tiếp là đã **duyệt** nó (quyết định ở M5-A §4.1, nên node này thừa hưởng câu trả lời chứ không
đoán).

---

# 9 — Cửa ghi DUY NHẤT — `tools/guards.py`

Không phải một workflow, mà là **điểm hội tụ của hai workflow**: WF-7 (`auto_post`) và WF-8
(`post_entry`) đều đi qua đúng hàm này, và không gì khác trong production được import
`db/repositories/journal.py`.

```
tools/guards.py::create_journal_entry(conn, *, invoice_id, posting_authority, entry_date,
                                      lines, actor, approval_id=None) -> uuid.UUID
    │
    ├─ posting_authority ∉ ("auto_clean", "human_approved")           -> _deny
    │
    ├─ "human_approved":
    │   ├─ approval_id is None                                        -> _deny
    │   ├→ db/repositories/approvals.py::get_approval(conn, approval_id) -> dict | None
    │   │       ├─ None                                               -> _deny
    │   │       ├─ approval["invoice_id"] != invoice_id               -> _deny
    │   │       ├─ approval["decision"] is None                       -> _deny  (chưa quyết định)
    │   │       └─ decision ∉ POSTING_DECISIONS = ("approved","edited")-> _deny
    │
    ├─ "auto_clean":
    │   ├─ approval_id is not None                                    -> _deny
    │   └→ db/repositories/flags.py::open_flags_for(conn, invoice_id) -> list[dict]
    │           └─ có severity ∈ BLOCKING_SEVERITIES = ("warn","critical") -> _deny
    │
    ├→ db/repositories/journal.py::get_for_invoice(conn, invoice_id) -> dict | None
    │       └→ _resolve_existing(existing, ...) -> UUID
    │              # cùng total + cùng posting_authority -> IDEMPOTENT SUCCESS (+ audit "granted")
    │              # khác                                -> _deny "already posted with different content"
    │
    ├→ db/repositories/journal.py::create_entry_with_lines(conn, invoice_id=, entry_date=,
    │        posting_authority=, approval_id=, posted_by=actor, status="posted", lines=[...]) -> UUID
    │       └─ IntegrityError (hai tiến trình cùng qua được bước kiểm tra ở trên):
    │              └→ đọc lại trên MỘT CONNECTION MỚI -> _resolve_existing(...)
    │                 # `conn` không dùng được nữa sau IntegrityError
    │
    ├→ db/repositories/invoices.py::set_status(conn, invoice_id, "posted")
    └→ _audit(actor=, allowed=True, invoice_id=, reason="posted", entry_id=, posting_authority=)
```

```
tools/guards.py::_deny(*, actor, invoice_id, reason, **detail) -> GuardDenied
    └→ _audit(allowed=False, ...)          # ghi TRƯỚC, rồi trả về exception để caller raise

tools/guards.py::_audit(*, actor, allowed, invoice_id, reason, **detail) -> None
    with get_engine().begin() as own:      # ⚠️ CONNECTION RIÊNG, commit độc lập
        └→ db/audit.py::record(own, actor=, action=f"tool.create_journal_entry.{granted|denied}",
                 allowed=, target_table="journal_entries", target_id=, reason=, detail=)
```

**Vì sao `_audit` mở connection riêng**: guard chạy *bên trong* transaction của caller. Một lần từ
chối sẽ raise, caller rollback, và một dòng audit ghi trên cùng connection đó sẽ **biến mất cùng nó**
— câu "mọi lần từ chối đều ghi `audit_log`" của design §5.3 sẽ âm thầm sai, và một test khẳng định
dòng đó tồn tại sẽ pass chỉ vì nó cũng chưa commit. **Một cái log không được phép bị rollback bởi
chính thứ mà nó đang log.** An toàn vì `audit_log` là append-only và không giữ FK tới `invoices`.

**Vì sao `_resolve_existing` so `Decimal` chứ không so chuỗi**: `Decimal("100")` và
`Decimal("100.00")` bằng nhau dưới `==` nhưng không bằng dưới `str`, và Postgres NUMERIC trả về đúng
scale mà nó được đưa vào.

**Ba lớp, và chúng cắn độc lập** — xác nhận bằng 5/5 mutation. Một bất đối xứng đáng ghi: bỏ
`UNIQUE(invoice_id)` **không** làm case post-hai-lần đỏ, vì guard đọc entry cũ trước khi insert. Phải
gọi thẳng `repositories.journal` (bỏ qua guard) mới thấy constraint cắn. Hai test đo hai thứ khác
nhau.

---

# 10 — WF-9: HTTP API

**Lệnh**: `make api` → `uvicorn invoice_agent.api.app:app --reload --port 8000`

```
api/app.py::create_app() -> FastAPI
    ├→ api/app.py::install_error_handlers(app)
    │       └→ @app.exception_handler(GuardDenied) -> JSONResponse(status_code=409)
    └→ include_router: health · invoices · reports · approvals · batches
```

**409, không phải 500.** Guard từ chối là hệ thống *đang làm việc*; một 500 đọc như bug và chôn một
cơ chế an toàn đang chạy vào trong log. Guard đã tự ghi `audit_log` trên connection riêng, nên lần từ
chối là bền vững bất kể response này.

**KHÔNG có handler `ValueError` toàn cục**, có chủ đích: nó sẽ bắt bug thật và phục vụ chúng dưới
dạng lỗi client 422 — phiên bản HTTP của `except:` trần. Route raise `HTTPException` tại chỗ.

## 10.1 Bảng endpoint → hàm

| Method + path | Hàm | Gọi xuống |
|---|---|---|
| `GET /health` | `routes/health.py::health` | `get_engine().connect()` + `SELECT 1` → 503 nếu không nối được |
| `GET /invoices` | `routes/invoices.py::list_invoices(status, department, limit≤500)` | `db/queries.py::list_invoices` |
| `GET /invoices/{id}` | `routes/invoices.py::get_invoice` | `invoices.get_by_id` + `get_lines` + `flags.open_flags_for` |
| `GET /reports/budgets` | `routes/reports.py::budgets(period)` | `db/queries.py::budget_report` |
| `GET /reports/flags` | `routes/reports.py::flags` | `db/queries.py::flag_report` |
| `GET /reports/posting` | `routes/reports.py::posting` | `db/queries.py::posting_report` |
| `GET /reports/cost` | `routes/reports.py::cost` | `db/queries.py::cost_report` |
| `GET /approvals` | `routes/approvals.py::pending` | `approvals.list_pending` |
| `GET /approvals/{id}` | `routes/approvals.py::detail` | `approvals.get_by_invoice` → 404 |
| `POST /approvals/{id}/decision` | `routes/approvals.py::decide` | **`approval.submit_decision`** |
| `POST /batches` (202) | `routes/batches.py::create_batch` | `_spool` + `batches.ensure_batch` + thread nền |
| `GET /batches/{id}` | `routes/batches.py::get_batch` | `db/queries.py::batch_summary` → 404 |
| `GET /batches/{id}/events` | `routes/batches.py::events` | `api/progress.py::stream` (SSE) 🟡 chưa consumer |

**`GET /health` PING POSTGRES.** Một health check không chạm database sẽ báo healthy trong khi
database đã biến mất — tệ hơn không có health check, vì một pipeline deploy tin nó.

## 10.2 Đường quyết định — route không thêm logic

```
POST /approvals/{invoice_id}/decision
api/routes/approvals.py::decide(invoice_id: uuid.UUID, body: DecisionRequest) -> dict
    ├→ db/repositories/approvals.py::get_by_invoice(conn, invoice_id) -> dict | None
    │       ├─ None                       -> HTTPException(404)
    │       └─ row["decision"] is not None -> HTTPException(409, "đã được … rồi")
    └→ graphs/approval.py::submit_decision(invoice_id=str(...), decision=body.decision,
               decided_by=body.decided_by, reason=body.reason, corrections=body.corrections)
    -> {"invoice_id", "decision", "status", "entry_id"}
```

`DecisionRequest` là **ranh giới an ninh ở mép HTTP**, và lập luận giống hệt §9.2 của design doc về
prompt injection, chuyển nguyên vẹn sang client:

```
api/schemas.py::DecisionRequest(BaseModel)
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected", "edited"]
    decided_by: str = Field(min_length=1)
    reason: str | None
    corrections: dict[str, Any] | None
```

Phòng thủ **không phải** validate, mà là chỉ thị nguy hiểm **KHÔNG DIỄN ĐẠT ĐƯỢC**: không có
`posting_authority`, không có `approval_id`, không có `status`, không có `entry_id`, nên không body
nào mang được chúng. `extra="forbid"` là nửa còn lại — thiếu nó, một field lạ bị bỏ qua im lặng và
bản audit danh sách field đang kiểm một schema mà request không bị ràng buộc theo.

> `tests/test_api_boundary.py` cấm route import `tools.guards` hoặc `repositories.journal`. Một
> route làm được thế sẽ tự chọn `posting_authority` của mình, và tầng HTTP trở thành **cánh cửa thứ
> hai** vào sổ cái.

## 10.3 Đường batch — 202 rồi chạy nền

```
POST /batches   (multipart: files[], department?)
api/routes/batches.py::create_batch(files, department) -> dict     # status_code=202
    ├─ không có file -> HTTPException(422)
    ├→ batches.py::_spool(batch_id, files) -> list[dict]
    │       # ghi vào get_settings().upload_dir / {batch_id} / {tên file đã lấy basename}
    │       # source_type = "pdf" nếu đuôi .pdf, ngược lại "xml"
    ├→ db/repositories/batches.py::ensure_batch(conn, batch_id, label=f"upload:{n}")
    └→ threading.Thread(target=_run, args=(batch_id, inputs, department), daemon=True).start()
    -> {"batch_id", "invoice_count"}         # trả về NGAY

api/routes/batches.py::_run(batch_id, inputs, department) -> None     # thread nền
    ├→ graphs/batch.py::build_batch_graph().stream(state, stream_mode="updates")
    │       └→ api/progress.py::publish(batch_id, {"node": …, "status": …})
    ├─ except Exception:
    │   ├→ db/repositories/batches.py::complete_batch(conn, batch_id, status="failed", …)
    │   │       # thất bại phải tới được DATABASE, không chỉ stack của thread này
    │   └→ progress.publish({"node": "error", "detail": …})
    └─ finally: progress.publish({"event": "end"}) + progress.finish(batch_id)
```

Batch mất vài giây (4s replay, 20.4s live — đo 2026-08-01), và giữ request mở suốt thời gian đó
khiến một client rớt mạng **giết luôn cả run**.

**`api/progress.py` ĐƯỢC PHÉP MẤT.** Nó là bộ nhớ, không phải lưu trữ: restart là rỗng. Vì thế
`GET /batches/{id}` **đếm dòng trong Postgres** chứ không đọc registry này —
`tests/test_api_batches.py` xóa registry rồi khẳng định batch vẫn query được. Mất progress làm client
mất khung nhìn trực tiếp; nó không bao giờ làm mất một batch.

**`department` là TÙY CHỌN và sự vắng mặt của nó có hệ quả**: `serial_join` gom ngân sách theo phòng
ban khai trên *task*, mà `InvoiceData` không mang phòng ban — nên một upload không khai sẽ được
persist, có thể được post, và rơi vào `unroutable`, **không bao giờ được đối chiếu ngân sách**. Ghi
nhận rõ thay vì vá bằng một default (M4-D đã xóa một hạn mức bịa vì đúng lý do này).

---

# 11 — WF-10: Streamlit UI

**Lệnh**: `make demo` → uvicorn `:8000` + `streamlit run src/invoice_agent/ui/app.py --server.port 8501`

```
ui/app.py::main()                       # chạy ở cuối module — Streamlit rerun cả file mỗi lần tương tác
    └→ st.sidebar.radio("Trang", PAGES) → một trong bốn hàm trang
        ├→ page_upload()      → api.departments, api.upload_batch, api.get_batch (poll 0.5s × 120)
        ├→ page_queue()       → api.pending_approvals
        ├→ page_detail()      → api.approval_detail, api.submit_decision
        └→ page_reports()     → api.budget_report, api.flag_report, api.posting_report, api.cost_report
```

Mọi lời gọi đi qua `_guard`, không gọi thẳng:

```
ui/app.py::_guard(call, *args, **kwargs) -> Any | None
    try:    return call(*args, **kwargs)
    except  ui.client.ApiError as exc: st.error(f"API trả về lỗi {exc.status_code}: {exc.detail}")
    except  httpx.HTTPError:           st.error("Không kết nối được tới API. Hãy chạy `make demo`…")
    return  None
```

Bắt `HTTPError` chứ không chỉ `ApiError` vì **API chết là lỗi mà người duyệt hay gặp nhất** — và đó
cũng đúng là trạng thái mà `AppTest` chạy trong đó, nên nó là thứ làm smoke test khẳng định được cái
gì đó khác ngoài "nó crash".

## 11.1 `ui/client.py` — đường DUY NHẤT tới hệ thống

```
ui/client.py::_session(client: httpx.Client | None) -> Iterator[httpx.Client]     # contextmanager
    │   client is not None -> yield NGUYÊN nó, KHÔNG đóng   (caller sở hữu)
    └   None -> httpx.Client(base_url=get_settings().api_base_url, timeout=TIMEOUT_S)

ui/client.py::_get(path, *, client, params) -> Any
    └→ _unwrap(response) -> Any
            └─ not response.is_success -> raise ApiError(status_code, _detail(response))

ui/client.py::_detail(response) -> str
    # body 422 của FastAPI là một LIST các {"loc": [...], "msg": ...}
    # -> "body.decision: Input should be 'approved', 'rejected' or 'edited'"
```

| Hàm client | Endpoint |
|---|---|
| `health()` | `GET /health` |
| `pending_approvals()` | `GET /approvals` |
| `approval_detail(invoice_id)` | `GET /approvals/{id}` |
| `get_invoice(invoice_id)` | `GET /invoices/{id}` |
| `submit_decision(invoice_id, *, decision, decided_by, reason, corrections)` | `POST /approvals/{id}/decision` |
| `upload_batch(files, *, department)` | `POST /batches` |
| `get_batch(batch_id)` | `GET /batches/{id}` |
| `list_invoices(*, status, department, limit)` | `GET /invoices` |
| `budget_report(*, period)` / `flag_report()` / `posting_report()` / `cost_report()` | `GET /reports/*` |
| `departments()` | **suy ra** từ `budget_report()` — xem dưới |

**Ba quy tắc mà test khóa lại:**

1. **`ui/` chỉ được import `invoice_agent.config`.** `tests/test_ui_boundary.py` chặn `db`, `graphs`,
   `tools`, `domain`, `llm`, `memory`, `extraction`. Streamlit là Python, nên
   `from …approval import submit_decision` sẽ chạy và còn ngắn hơn — và khi đó consumer duy nhất của
   M5-C bỏ qua API, để lại nó thành code chết với một test suite xanh. Mutation đã xác nhận **bất đối
   xứng**: thêm import `invoice_agent.db` vào thì **test client vẫn XANH**, chỉ mỗi kiểm tra import
   thấy được.
2. **`app.py` không có HTTP nào của riêng nó** — `test_app_makes_no_http_call_of_its_own`. Streamlit
   rerun cả file mỗi lần tương tác và không unit test nào với tới được trong đó; đẩy hết xuống
   `client.py` là cái làm cho tầng này test được.
3. **`_unwrap` phải RAISE trên non-2xx.** Mutation: trả thẳng `response.json()` ⇒ 409/404/422 đều
   `DID NOT RAISE ApiError`, và UI sẽ lặng lẽ hiện "đã duyệt" khi một lệnh ghi vừa thất bại.

**`departments()` đọc từ report chứ không hardcode**: không có endpoint `/departments` và M5-D không
thêm hành vi backend nào, nhưng `/reports/budgets` đã trả `department_code` mỗi dòng. Hardcode năm
phòng ban sẽ là nguồn sự thật thứ hai cho seed data — đúng cái mà `scripts/seed_reference.py` cấm với
`BUDGETS`, vì hai bản sao là cách một test bắt đầu pass vì lý do sai.

**`submit_decision` chỉ gửi bốn field mà `DecisionRequest` khai.** Không phải vì lịch sự: schema cấm
extra, nên request mang thêm là 422. Điểm là client này **cũng không có cách nào diễn đạt** chúng.

> **UI KHÔNG dùng SSE.** `GET /batches/{id}/events` là stream chặn, và một stream chặn sẽ đóng băng
> vòng rerun của Streamlit. `page_upload` poll `GET /batches/{id}` mỗi 0.5s, tối đa 120 lần (60s, so
> với 20.4s đo được của một batch live). Consumer của SSE là M7.

---

# 12 — WF-11…13: Ba script vận hành

## 12.1 WF-11 `recover_pending.py` — dựng lại hàng chờ duyệt

```
scripts/recover_pending.py::main() -> None
    └→ scan() -> {"pending": [...], "orphaned": [...]}
        ├→ db/repositories/approvals.py::list_pending(conn) -> list[dict]
        │       # `approvals WHERE decision IS NULL` — LangGraph KHÔNG có API liệt kê thread pending
        ├→ checkpointer.py::build_checkpointer() -> PostgresSaver
        ├→ graphs/approval.py::build_approval_graph(checkpointer=saver)
        └→ for row in rows:
            └→ graph.get_state({"configurable": {"thread_id": row["thread_id"]}}) -> StateSnapshot
                    ├─ snapshot.interrupts  -> pending
                    └─ ngược lại            -> orphaned
```

Cái mất sau restart **không phải state** (state đã ở Postgres) mà là **danh sách thread nào đang
chờ**. Hai kết quả, và cái thứ hai mới là lý do script tồn tại: một **orphan** là một hóa đơn mà con
người chờ mãi mãi. Một script chỉ báo cáo ca lành mạnh sẽ giấu đúng cái hỏng nó được viết ra để tìm.

> `snapshot.interrupts` được truy cập **thẳng**, không `getattr(..., ())`. Xác nhận 2026-08-01 trên
> langgraph 1.2.9: `interrupts` có trong `StateSnapshot._fields`. Một default sẽ biến một lần đổi tên
> ở bản sau thành *"mọi thread đều orphaned"* — âm thầm làm rỗng hàng chờ duyệt — thay vì một
> `AttributeError` nêu đúng nguyên nhân.

## 12.2 WF-12 `rebuild_vendor_profiles.py` — và `--check`

```
scripts/rebuild_vendor_profiles.py::main() -> None      # argparse: --check
    ├→ _aggregate(conn) -> dict[tax_code, {invoice_count, total_amount, sum_sq_amount, avg, stddev}]
    │       # GROUP BY seller_tax_code trên app.invoices — ground truth
    │       └→ memory/vendor_profile.py::derive(count, total, sum_sq) -> (avg, stddev)
    │              ↑ CÙNG hàm mà apply_invoice dùng — nên "zero drift" là CHÍNH XÁC, không phải dung sai
    ├→ check(conn) -> list[str]        # mô tả mọi bất đồng; --check thì KHÔNG ghi và exit ≠ 0
    └→ recompute(conn) -> dict         # ghi sự thật trở lại
```

**Chỉ các field thống kê.** `_STAT_FIELDS` không chứa `rejection_count`, `reviewer_notes`,
`is_trusted` — chúng do con người tạo ra và không dẫn xuất được từ `invoices`; một lần rebuild xóa
chúng sẽ phá hủy đúng phần duy nhất của profile mà không gì tái tạo được.

**Một rebuild mà không ai diff là một cách sai thứ hai.** Đó là lý do `--check` tồn tại và là mode mà
CI/test lái.

## 12.3 WF-13 `eval_classification.py` — đo so với baseline

```
scripts/eval_classification.py::main() -> None
    │   LLM_MODE=record ... --out docs/notes/m4c-raw.json   |   LLM_MODE=replay ...
    ├→ scripts/generator/manifest.py::read_manifest(...)
    ├→ scripts/seed_reference.py::CATEGORIES
    ├→ agents/classification.py::classify(...)  +  agents/audit.py::audit(...)
    └→ eval/baseline.py::classify(text, keywords) -> str | None
            └→ eval/baseline.py::load_keywords(conn) -> dict[str, list[str]]
                    # đọc `keywords_vi` từ CHÍNH những dòng expense_categories đã seed
```

`eval/baseline.py::classify` **không bao giờ đoán một default**: keyword dài nhất khớp thì thắng,
không khớp gì thì trả `None`. Chính tỉ lệ không-khớp 249/285 là thứ đã phơi ra defect nhãn của corpus
ngày 2026-07-30 — một baseline biết đoán sẽ báo cáo nó thành *sai số thông thường* thay vì
*tín hiệu vắng mặt*.

Nguyên tắc 3 (CLAUDE.md): mọi metric LLM phải được báo cáo **cạnh** baseline này, và phải hơn nó
≥15 điểm, nếu không node đó không cần LLM. Kết quả M4-C: **95.4% vs 57.9%** (+37.5).

---

# 13 — Trục ngang: config → DSN → engine

Mọi workflow đều đi qua đây. Đây là **nơi duy nhất đọc environment**.

```
config.py::get_settings() -> Settings        # @lru_cache — get_settings.cache_clear() để reset trong test
    │
    ├→ Settings._with_options(options: str, base: str|None = None) -> str
    │       # quote(options, safe="") — BẮT BUỘC: giá trị chứa '=' và psycopg coi
    │       # dấu '=' thứ hai trong một query param là lỗi cú pháp
    │
    ├→ .app_dsn                    -csearch_path=app,public
    ├→ .checkpointer_dsn           -csearch_path=langgraph,public
    ├→ .runtime_dsn                -csearch_path=app,public       (base = runtime_database_url)
    ├→ .app_sqlalchemy_url         app_dsn      + postgresql+psycopg://
    ├→ .runtime_sqlalchemy_url     runtime_dsn  + postgresql+psycopg://
    └→ .alembic_sqlalchemy_url     -csearch_path=public           (base = database_url)
```

**Ai dùng property nào:**

| Người dùng | Property | Role | Vì sao |
|---|---|---|---|
| `db/engine.py::get_engine` | `runtime_sqlalchemy_url` | `invoice_app` | Đặc quyền tối thiểu; không UPDATE/DELETE trên `audit_log` |
| `db/engine.py::get_owner_engine` | `app_sqlalchemy_url` | chủ sở hữu | Seed script — việc quản trị |
| `migrations/env.py` | `alembic_sqlalchemy_url` | chủ sở hữu | `app` **không** đứng đầu search_path — xem [§4](#4--wf-4-migration) |
| `checkpointer.py::build_checkpointer` | `checkpointer_dsn` | chủ sở hữu | `setup()` tạo bảng, phải rơi vào schema `langgraph` |

**Ai đọc field nào của `Settings`:**

| Field | Người đọc |
|---|---|
| `llm_model_chain`, `groq_api_keys`, `gemini_api_keys`, `openrouter_api_keys`, `openai_api_key` | `LLMGateway.__init__` |
| `auditor_model_chain` | `agents/audit.py::audit` — chain **khác họ** với Classifier, `test_auditor_chain.py` khóa |
| `vision_model_chain` | `extraction/vision.py::extract` — chain **riêng**, vì cả 2 model groq và cả 2 model openrouter đều không hỗ trợ vision |
| `llm_mode`, `llm_cache_dir` | `LLMGateway.__init__` → `_resolve_chain`, `_complete` |
| `max_concurrency` | `graphs/batch.py` (mặc định của `build_batch_graph` là 5, design §4.2 nói 4–6) |
| `upload_dir` | `api/routes/batches.py::_spool` |
| `api_base_url` | `ui/client.py::_session` — **người đọc duy nhất**, và là lý do `ui/` được phép import `config` |
| `langfuse_*` | Chỉ để validate + tài liệu hóa; `observability.py` đọc thẳng `os.environ` |

> ⚠️ `_CsvList = Annotated[list[str], NoDecode]` không phải trang trí: pydantic-settings coi
> `list[str]` là "complex type" và chạy `json.loads()` lên giá trị env **trước khi** validator
> `mode="before"` được gọi, nên `LLM_MODEL_CHAIN=groq/a,gemini/b` sẽ raise `SettingsError` chứ
> không bao giờ tới được `_split_csv`.

`db/engine.py` cả hai hàm đều `@lru_cache` — một Engine dùng chung toàn tiến trình.

---

# 14 — Hàm đã có nhưng chưa ai gọi

Ở M3 đây là phần quan trọng nhất của tài liệu: một mảng lớn mã đã có, đã test, chưa nằm trên đường
dây production nào. **M4 và M5 đã tiêu thụ gần hết.** Danh sách còn lại, kiểm bằng `grep` toàn bộ
`src/` + `scripts/` ngày 2026-08-01:

| File | Hàm | Ghi chú |
|---|---|---|
| `llm/gateway.py` | `generate_with_model` | Móc cho Auditor — biết model nào đã trả lời để **loại nó** khỏi việc tự chấm bài. `agents/audit.py` cuối cùng dùng `auditor_model_chain` (khác họ) chứ không dùng `exclude_model`, nên hàm này vẫn chưa có consumer |
| `llm/gateway.py` | `generate_stream` | Không có định dạng fixture cho streaming, nên dưới `LLM_MODE=replay` nó **cố tình raise** thay vì lặng lẽ gọi mạng thật |
| `api/progress.py` | `stream` (qua `GET /batches/{id}/events`) | Endpoint chạy được và có test, nhưng M5-D **không** dùng: stream chặn làm đóng băng vòng rerun của Streamlit. Consumer là M7 |
| `db/repositories/reference.py` | `list_departments`, `list_budgets` | `get_department`/`get_category` đã có caller; hai hàm list thì chưa |
| `db/repositories/batches.py` | `create_batch` | `ensure_batch` (idempotent, nhận sẵn UUID) là cái mọi caller dùng |
| `db/repositories/invoices.py` | `get_by_natural_key` | Phát hiện trùng lặp đi qua `ON CONFLICT` của `insert_row` thay vì tra trước |
| `agents/families.py` | `family_of` | Consumer duy nhất là `tests/test_auditor_chain.py`, và **đó chính là mục đích của nó**: bảng `MODEL_FAMILIES` tồn tại để một test khẳng định hai chain không giao nhau về họ model. Không có đường production nào cần tra family lúc chạy |
| `observability.py` | `observe` | Chưa decorator nào dùng; gắn `@observe` lên mọi node là 🔲 M6 |
| `scripts/generator/amounts.py` | `make_line_money` | Chỉ `tests/test_generator_amounts.py` |
| `scripts/generator/defects.py` | `make_note_line`, `make_vnpt_line`, `make_non_vat_spec`, `make_header_only_vat_spec` | Chỉ `tests/test_generator_hard_shapes.py` — bốn hình dạng khó đo được từ 8 fixture thật |

**Đã chuyển từ 🟡 sang ✅ ở M4/M5** (giữ lại vì nó cho thấy thiết kế đi trước mã bao xa):

| Hàm | Nay được gọi bởi |
|---|---|
| `domain/accounting.py::build_journal_entry` | `batch.auto_post_node`, `approval.post_entry_node` |
| `domain/anomaly.py::{new_vendor, price_outlier}` | `enrichment._signals_for` |
| `domain/anomaly.py::near_duplicate` | `batch._join` |
| `domain/budget.py::consume` | `batch._join` |
| `db/repositories/journal.py::*` | **chỉ** `tools/guards.py` — allowlist một phần tử |
| `db/repositories/flags.py::{raise_flag, open_flags_for, resolve_flag}` | `batch._join`, `guards`, `approval` |
| `db/repositories/approvals.py::*` | `batch.open_approval_node`, `approval`, `api/routes/approvals.py`, `recover_pending` |
| `db/repositories/episodic.py::{record_episode, similar_episodes}` | `approval.learn_node`, `approval._suggest` |
| `db/repositories/invoices.py::{set_status, get_by_id, get_lines}` | `batch`, `approval`, `guards`, `api/routes/invoices.py` |
| `db/repositories/vendors.py::{get_vendor, update_profile}` | `approval.learn_node`, `rebuild_vendor_profiles` |
| `db/audit.py::record` | `tools/guards.py::_audit` |
| `checkpointer.py::build_checkpointer` | `batch.open_approval_node`, `approval.submit_decision`, `recover_pending` |

> **`journal.py` mang một cảnh báo trong docstring, và cảnh báo đó ĐÃ ĐỔI.** Ở M3 nó nói "đừng thêm
> caller production trước M5.3", và guard tên là `test_no_production_caller_of_journal_repository`.
> M5.3 đã đáp, nên test được **đổi tên thành
> `tests/test_schema_constraints.py::test_only_the_guard_may_call_the_journal_repository`** và thu
> hẹp thành **allowlist đúng một phần tử**: `tools/guards.py`. Xóa test đi là câu trả lời dễ; thu hẹp
> nó giữ được bất biến. `tests/test_api_boundary.py::test_no_route_reaches_the_ledger_directly` nối
> dài quy tắc đó sang tầng HTTP. Thêm phần tử thứ hai đòi hỏi sửa test — tức là một hành động có chủ
> đích.

---

# 15 — Bảng tra ngược: file → hàm → ai gọi

Đọc theo chiều ngược lại của tài liệu này: bạn đang mở một file và muốn biết nó nối vào đâu.

## 15.1 `src/invoice_agent/` — graph, agent, guard

| File | Hàm public | Ai gọi nó | Nó gọi ai |
|---|---|---|---|
| `graphs/enrichment.py` | `build_enrichment_graph`, 8 node, `route_after_validate` | `scripts/run_one.py`, `scripts/render_graph.py`, `graphs/batch.py` | `xml_parser`, `vision`, `validation`, `agents/*`, `memory/vendor_profile`, `anomaly`, `telemetry`, `db.engine` |
| `graphs/batch.py` | `build_batch_graph`, `thread_id_for`, `enrich_one`, `serial_join`, 9 node | `api/routes/batches.py`, `scripts/render_graph.py`, test | `graphs/enrichment`, `graphs/approval`, `checkpointer`, `db/repositories/*`, `memory`, `domain/*`, `tools/guards` |
| `graphs/approval.py` | `build_approval_graph`, `submit_decision`, 7 node, `route_after_decision` | `graphs/batch.py`, `api/routes/approvals.py`, `scripts/recover_pending.py`, `scripts/render_graph.py` | `db/repositories/*`, `domain/accounting`, `tools/guards`, `extraction/models`, `checkpointer` |
| `tools/guards.py` | `create_journal_entry`, `GuardDenied`, `POSTING_DECISIONS`, `BLOCKING_SEVERITIES` | `batch.auto_post_node`, `approval.post_entry_node`, `api/app.py` (chỉ bắt exception), `batch._route` (chỉ hằng số) | `db/repositories/{approvals,flags,journal,invoices}`, `db/audit`, `domain/accounting` |
| `agents/classification.py` | `classify`, `describe`, `dispute` | `graphs/enrichment.py`, `agents/audit.py`, `scripts/eval_classification.py` | `llm/structured`, `extraction/sanitize`, `domain/findings`, `agents/models` |
| `agents/audit.py` | `audit` | `graphs/enrichment.py`, `scripts/eval_classification.py` | `agents/classification.describe`, `llm/structured`, `config` |
| `agents/families.py` | `family_of`, `MODEL_FAMILIES`, `UnknownModelFamily` | `tests/test_auditor_chain.py` | — (thuần) |
| `agents/models.py` | `ClassificationResult`, `AuditVerdict`, `CATEGORIES`, `DEPARTMENTS` | `agents/*`, `graphs/enrichment.py` | pydantic |
| `memory/vendor_profile.py` | `derive`, `apply_invoice`, `stats_for` | `graphs/batch._join`, `graphs/enrichment._signals_for`, `scripts/rebuild_vendor_profiles.py` | `db/models`, `domain/anomaly` |
| `eval/baseline.py` | `classify`, `load_keywords` | `scripts/eval_classification.py`, test | `db/models` |

## 15.2 `src/invoice_agent/` — trích xuất, domain, LLM, dữ liệu

| File | Hàm public | Ai gọi nó | Nó gọi ai |
|---|---|---|---|
| `extraction/xml_parser.py` | `parse_invoice_xml` | `graphs/enrichment.py`, test | `xml_schema`, `extraction/models`, `defusedxml` |
| `extraction/xml_schema.py` | `locate_hdon`, `required_text`, `money`, `integer`, `vat_rate`, `issue_date`, `ext_bag` + hằng tag | `xml_parser`, `domain/money`, `domain/validation` | — (thuần) |
| `extraction/models.py` | `InvoiceData`, `InvoiceLine` | khắp nơi — đây là **hợp đồng trung tâm** | pydantic |
| `extraction/vision.py` | `extract`, `rasterize` | `graphs/enrichment.py` | `llm/structured`, `llm/gateway`, `config`, pypdfium2 |
| `extraction/sanitize.py` | `escape`, `wrap` | `llm/structured.py`, `agents/classification.py`, `agents/audit.py` | — (thuần) |
| `llm/structured.py` | `call_structured` | `extraction/vision.py`, `agents/*` | `llm/gateway`, `extraction/sanitize` |
| `llm/gateway.py` | `LLMGateway.generate` (+ 2 hàm 🟡) | `llm/structured.py`, `graphs/enrichment.py` (tiêm) | `llm/cache`, `llm/cost`, `config`, litellm |
| `llm/cache.py` | `make_key`, `load`, `save`, `CachedResponse`, `LLMReplayMiss` | `llm/gateway.py` | — (chỉ filesystem) |
| `llm/cost.py` | `actual_cost_usd`, `shadow_cost_usd`, `LLMCallRecord` | `llm/telemetry.py`, `llm/gateway.py` (dataclass) | — (thuần) |
| `llm/telemetry.py` | `record` | `graphs/enrichment.py::_persist_cost` (3 node) | `llm/cost`, `db/repositories/llm_calls` |
| `domain/validation.py` | `validate` | `graphs/enrichment.py` | `domain/money`, `domain/taxcode`, `domain/findings`, `xml_schema` |
| `domain/money.py` | `line_amount`, `goods_subtotal`, `parse_amount_words` | `domain/validation`, `domain/accounting` | `xml_schema`, `extraction/models` |
| `domain/taxcode.py` | `normalise`, `is_valid_checksum`, `check_tax_code` | `domain/validation` | `domain/findings` |
| `domain/findings.py` | `Finding`, `make`, `CODE_REGISTRY` (17 mã) | `validation`, `taxcode`, `anomaly`, `budget`, `agents/classification`, `graphs/batch` | — (thuần) |
| `domain/accounting.py` | `build_journal_entry`, `CategoryAccounts`, `JournalLine` | `batch.auto_post_node`, `approval.post_entry_node`, `tools/guards` (kiểu) | `domain/money` |
| `domain/anomaly.py` | `new_vendor`, `price_outlier`, `near_duplicate`, `VendorStats`, `InvoiceKey` | `enrichment._signals_for`, `batch._join`, `memory/vendor_profile` | `domain/findings` |
| `domain/budget.py` | `consume`, `BudgetInput`, `Consumption` | `batch._join` | `domain/findings` |
| `db/engine.py` | `get_engine`, `get_owner_engine` | gần như mọi thứ chạm DB | `config` |
| `db/models.py` | 16 `Table` + hằng vocabulary | mọi repository, `db/queries`, `migrations/env.py`, `eval/baseline`, `memory` | SQLAlchemy |
| `db/queries.py` | `list_invoices`, `batch_summary`, `budget_report`, `flag_report`, `posting_report`, `cost_report` | `api/routes/{invoices,reports,batches}.py` | `db/models` — **CHỈ ĐỌC**, có test khóa |
| `db/audit.py` | `record` | `tools/guards.py::_audit` | `db/models` |
| `db/repositories/invoices.py` | `insert_with_lines`, `insert_row`, `get_by_id`, `get_lines`, `set_status` (+ `get_by_natural_key` 🟡) | `run_one`, `batch`, `approval`, `guards`, `api/routes/invoices.py` | `db/models`, `repositories/{vendors,reference}` |
| `db/repositories/vendors.py` | `ensure_vendor`, `get_vendor`, `update_profile` | `repositories/invoices`, `approval.learn_node`, `batch._join`, `rebuild_vendor_profiles` | `db/models` |
| `db/repositories/flags.py` | `raise_flag`, `open_flags_for`, `resolve_flag` | `batch`, `guards`, `approval`, `api/routes/invoices.py` | `db/models` |
| `db/repositories/approvals.py` | `open_approval`, `record_decision`, `record_context`, `list_pending`, `get_approval`, `get_by_invoice` | `batch`, `approval`, `api/routes/approvals.py`, `recover_pending` | `db/models` |
| `db/repositories/journal.py` | `create_entry_with_lines`, `get_for_invoice`, `get_lines` | **chỉ `tools/guards.py`** — allowlist có test | `db/models` |
| `db/repositories/episodic.py` | `record_episode`, `similar_episodes` | `approval.learn_node`, `approval._suggest` | `db/models` |
| `db/repositories/batches.py` | `ensure_batch`, `complete_batch`, `get_batch` (+ `create_batch` 🟡) | `batch._join`, `batch.finalize_node`, `api/routes/batches.py` | `db/models` |
| `db/repositories/budget_consumption.py` | `record_consumption` | `batch._join` | `db/models` |
| `db/repositories/llm_calls.py` | `record_call` | `llm/telemetry.py` | `db/models` |
| `db/repositories/reference.py` | `upsert_department`, `upsert_category`, `upsert_budget`, `get_category`, `get_department` (+ 2 🟡) | `seed_reference`, `batch.auto_post_node`, `approval.post_entry_node`, `repositories/invoices` | `db/models` |
| `config.py` | `get_settings`, `Settings` + 6 property DSN | **tất cả** | pydantic-settings |
| `checkpointer.py` | `build_checkpointer` | `batch.open_approval_node`, `approval.submit_decision`, `recover_pending`, test | `config`, `serde`, langgraph |
| `serde.py` | `build_serde`, `ALLOWED_MSGPACK_MODULES` | `checkpointer.py` | langgraph |
| `observability.py` 🟡 | `observe`, `TRACING_ENABLED` | *chưa ai* | `os.environ`, langfuse (có điều kiện) |

## 15.3 `src/invoice_agent/api/` và `ui/`

| File | Hàm public | Ai gọi nó | Nó gọi ai |
|---|---|---|---|
| `api/app.py` | `create_app`, `install_error_handlers`, `app` | uvicorn, `TestClient` trong test | `api/routes/*`, `tools/guards.GuardDenied` |
| `api/schemas.py` | `DecisionRequest` | `api/routes/approvals.py` | pydantic |
| `api/progress.py` | `publish`, `finish`, `stream`, `clear` | `api/routes/batches.py`, test | — (queue trong RAM) |
| `api/routes/health.py` | `health` | `create_app` | `db/engine` |
| `api/routes/invoices.py` | `list_invoices`, `get_invoice` | `create_app` | `db/queries`, `repositories/{invoices,flags}` |
| `api/routes/reports.py` | `budgets`, `flags`, `posting`, `cost` | `create_app` | `db/queries` |
| `api/routes/approvals.py` | `pending`, `detail`, `decide` | `create_app` | `repositories/approvals`, **`graphs/approval.submit_decision`** |
| `api/routes/batches.py` | `create_batch`, `get_batch`, `events` | `create_app` | `graphs/batch`, `db/queries`, `repositories/batches`, `api/progress`, `config` |
| `ui/client.py` | 12 hàm HTTP + `ApiError` | `ui/app.py`, `tests/test_ui_client.py` | httpx, **`config` (import backend DUY NHẤT được phép)** |
| `ui/app.py` | `main`, 4 hàm trang, `_guard` | Streamlit runtime, `AppTest` | **chỉ `ui/client`** |

## 15.4 `scripts/`

| File | Hàm public | Ai gọi nó | Nó gọi ai |
|---|---|---|---|
| `run_one.py` | `run_one`, `main` | CLI, `tests/test_run_one.py` | `generator/manifest`, `graphs/enrichment`, `db/repositories/invoices`, `db/engine` |
| `generate_invoices.py` | `generate`, `main` | CLI (`make generate`) | `generator/*`, `seed_reference.BUDGETS` |
| `seed_reference.py` | `seed`, `main`, `DEPARTMENTS`/`CATEGORIES`/`BUDGETS` | CLI (`make seed`), `generate_invoices.py`, `eval_classification.py` | `db/repositories/reference`, `db/engine` |
| `render_graph.py` | `main` | CLI, 3 test drift guard | `graphs/{enrichment,batch,approval}` |
| `recover_pending.py` | `scan`, `main` | CLI, `tests/test_recover_pending.py` | `repositories/approvals`, `checkpointer`, `graphs/approval` |
| `rebuild_vendor_profiles.py` | `check`, `recompute`, `main` | CLI (`make rebuild-profiles`), `tests/test_rebuild_vendor_profiles.py` | `memory/vendor_profile.derive`, `db/models`, `db/engine` |
| `eval_classification.py` | `main` | CLI | `agents/*`, `eval/baseline`, `generator/manifest`, `seed_reference.CATEGORIES` |
| `generator/templates.py` | `render`, `InvoiceSpec`, `LineSpec` | `generate_invoices.py`, `generator/defects.py` | `generator/amounts`, jinja2 |
| `generator/defects.py` | 9 hàm gieo defect (`seed_*`, `*_of`, `build_budget_overrun_case`) + 4 hàm 🟡 | `generate_invoices.py` | `generator/templates`, `generator/amounts`, `generator/vendors` |
| `generator/amounts.py` | `to_amount_words` (+ `make_line_money` 🟡) | `generator/templates`, `generator/defects` | — (thuần) |
| `generator/vendors.py` | `build_vendors`, `invalidate_checksum`, `VALID_MSTS` | `generate_invoices.py`, `generator/defects.py` | — (thuần) |
| `generator/pdf.py` | `render_pdf`, `render_truncated`, `render_blank`, `render_degraded` | `generate_invoices.py` | fpdf2 |
| `generator/manifest.py` | `write_manifest`, `read_manifest`, `ManifestRecord`, `DEFECT_CODES`, `BODY_DEFECTS` | `generate_invoices.py`, `run_one.py`, `eval_classification.py` | — (chỉ JSON) |

## 15.5 Bảy kiểu dữ liệu đi xuyên nhiều file

Nếu chỉ nhớ được vài thứ, hãy nhớ các kiểu này — chúng là mặt cắt thật giữa các tầng:

| Kiểu | Định nghĩa ở | Sinh ra bởi | Tiêu thụ bởi |
|---|---|---|---|
| `InvoiceData` | `extraction/models.py` | `xml_parser.parse_invoice_xml`, `vision.extract`, `approval._rebuild_invoice_data` | `validation.validate`, `agents/*`, `accounting.build_journal_entry`, `invoices.insert_with_lines` |
| `Finding` | `domain/findings.py` | `findings.make` (từ `validation`, `taxcode`, `anomaly`, `budget`, `classification.dispute`) | `enrichment._describe`, `route_after_validate`, `flags.raise_flag` |
| `LLMCallRecord` | `llm/cost.py` | `gateway._record_attempt` | `cost.actual_cost_usd` / `shadow_cost_usd`, `telemetry.record` |
| `ClassificationResult` / `AuditVerdict` | `agents/models.py` | `call_structured` (output của model) | `classification.dispute`, `EnrichedInvoice.classification` → `serial_join`, `auto_post` |
| `EnrichmentState` | `graphs/enrichment.py` | `scripts/run_one.py`, `batch.enrich_one` | 8 node + router |
| `BatchState` / `EnrichedInvoice` | `graphs/batch.py` | `api/routes/batches.py::_run`, test | 9 node của batch |
| `ApprovalState` | `graphs/approval.py` | `batch.open_approval_node` | 7 node của approval |

`InvoiceData`, `InvoiceLine`, `Finding`, `LLMCallRecord` nằm trong `serde.ALLOWED_MSGPACK_MODULES` —
tức chúng là những gì được phép vượt qua ranh giới checkpoint. Ba `*State` không cần đăng ký vì chúng
là `TypedDict`, tức đã là `dict` khi serialize; `ApprovalState` còn đi xa hơn và **chỉ chứa JSON
thuần** ([§8.2](#82-approvalstate--json-thuần-có-lý-do)).

> Thêm một kiểu **không-JSON** mới vào `EnrichmentState` thì **phải** thêm vào `serde.py`.
> `tests/test_serde.py` liệt kê từ một state đã điền đủ, nên bỏ sót sẽ fail ở CI.

---

## Đọc tiếp

| Câu hỏi | Tài liệu |
|---|---|
| Hệ thống được cấu tạo thế nào | [`Architecture.md`](Architecture.md) |
| Cái gì xảy ra, theo thứ tự nào | [`Workflow.md`](Workflow.md) |
| Nên đọc hai tài liệu trên theo lộ trình nào | [`Reading-Guide.md`](Reading-Guide.md) |
| Thiết kế đầy đủ, gồm cả phần chưa làm | [`project-f-system-design.md`](project-f-system-design.md) |
| Milestone nào sở hữu các hàm 🟡 ở §14 | [`Plan_Project_F_2026-07-25.md`](Plan_Project_F_2026-07-25.md) §3 |
