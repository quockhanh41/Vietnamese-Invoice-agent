# Đọc thế nào — hướng dẫn cho `Architecture.md` + `Workflow.md` + `Function-Map.md`

Ba tài liệu này **được viết để đọc xen kẽ**, không phải đọc hết cái này rồi mới sang cái kia.

| Tài liệu | Trả lời | Cấu trúc |
|---|---|---|
| [`Architecture.md`](Architecture.md) | *"Hệ thống được **cấu tạo** thế nào?"* | 16 mục theo **tầng** — module, graph, extraction, domain, LLM, data, bảo mật |
| [`Workflow.md`](Workflow.md) | *"Cái gì **xảy ra**, theo thứ tự nào, ai quyết định, hỏng thì sao?"* | 22 flow theo **thời gian** — A nghiệp vụ, B ngang, C vận hành, D phát triển |
| [`Function-Map.md`](Function-Map.md) | *"Hàm nào **gọi** hàm nào, vào cái gì, ra cái gì?"* | 9 mục theo **đường dây** — 6 workflow có mã chạy được, + §8 hàm chưa ai gọi, + §9 bảng tra ngược |

> **Quy tắc chung**: Architecture cho bạn **danh từ** (module này là gì, bảng này có cột gì).
> Workflow cho bạn **động từ** (bước này chạy khi nào, nó dừng ở đâu).
> Function-Map cho bạn **đường dây** (bước đó thực ra là hàm nào, nhận gì, trả gì).
> Đọc danh từ trước một nhịp, rồi lập tức xem nó **chuyển động** ở Workflow — kiến thức mới dính.
> Khi nào mở editor lên sửa code thì mới cần tới Function-Map.

---

## Chọn lộ trình

| Bạn có | Đọc |
|---|---|
| **5 phút** | [Đường tắt](#đường-tắt--5-phút) |
| **~25 phút** | [Lộ trình nhanh](#lộ-trình-nhanh--25-phút) — đủ để nói chuyện về dự án |
| **~100 phút** | [Lộ trình đầy đủ](#lộ-trình-đầy-đủ--6-chặng) — 6 chặng, mỗi chặng trả lời một câu hỏi |
| **Đang tìm một thứ cụ thể** | [Bảng tra chéo](#bảng-tra-chéo) |
| **Sắp sửa code** | [`Function-Map.md`](Function-Map.md) §9 (bảng tra ngược file → hàm) rồi §8 (hàm chưa ai gọi) |

---

## Đường tắt — 5 phút

1. `Architecture.md` **§1 Tóm tắt điều hành** — 3 câu định hình toàn bộ hệ thống
2. `Workflow.md` **A0** — chỉ nhìn **sơ đồ** vòng đời một hóa đơn + bảng *"Ba ranh giới"*
3. `Workflow.md` **E** — bảng trạng thái 22 flow, biết cái gì đã chạy và cái gì chưa

Xong bước 3 bạn đã biết: hệ thống làm gì, ranh giới an toàn nằm ở đâu, và hôm nay nó đi được tới đâu.

---

## Lộ trình nhanh — 25 phút

| # | Đọc | Vì sao ở đây |
|:--:|---|---|
| 1 | Arch **§1**, **§2** | Bối cảnh + 3 câu định hình |
| 2 | WF **A0** | Thấy toàn cảnh trước khi thấy chi tiết |
| 3 | Arch **§6.1** | *Vì sao ba graph, không phải một* — quyết định kiến trúc đắt nhất |
| 4 | WF **A3** | Leo thang & self-correction — flow thú vị nhất, và nó giải thích §6.2 |
| 5 | Arch **§3.1**, **§3.3** | Safety property ba lớp + structured output là ranh giới an ninh |
| 6 | WF **A7** | Thấy ba lớp đó **chặn thật** trong một flow |
| 7 | Arch **§15** hoặc WF **E** | Hôm nay ở đâu, còn nợ gì |

---

## Lộ trình đầy đủ — 6 chặng

Mỗi chặng là một **câu hỏi**. Đọc theo thứ tự trong chặng; nghỉ giữa các chặng thoải mái.

### Chặng 1 — "Dự án này làm gì, và vì sao nó được thiết kế như vậy?" (~15 phút)

```
Arch §1 Tóm tắt  →  Arch §2 Bối cảnh  →  Arch §3 Năm nguyên tắc  →  WF phần mở đầu (quy ước ✅/🚧/🔲)
                                                                  →  WF A0 Toàn cảnh
```

**Đọng lại**: ba ranh giới — *Extraction ⟂ Data*, *Máy ⟂ Người*, *State ⟂ Sự thật*. Mọi thứ còn lại
là hệ quả của ba ranh giới này.

### Chặng 2 — "Một hóa đơn đi qua hệ thống thế nào?" (~25 phút)

```
Arch §4 Sơ đồ tổng thể  →  Arch §5 Bản đồ module  →  Arch §6.1 Vì sao ba graph
   →  Arch §6.2 enrichment_graph   ⇄   WF A2 Enrichment      ← đọc SONG SONG, cùng một graph
   →  WF A3 Leo thang & self-correction                       ← trung tâm của chặng
   →  Arch §7 Lớp trích xuất                                  ← giờ §7.2 mới có ý nghĩa
```

**Đọng lại**: vì sao XML có finding thì **không retry**, còn vision có finding thì **có**. Câu trả
lời nằm ở một tính chất duy nhất: parser XML là hàm thuần, vision thì không.

### Chặng 3 — "Cờ ở đâu ra, và vì sao LLM không bật được?" (~15 phút)

```
Arch §8 Lõi domain  ⇄  WF B4 Vòng đời một cờ
   →  Arch §3.1 Safety ba lớp  +  Arch §3.3 Structured output
   →  WF A7 Post bút toán                                     ← ba lớp chặn thật, có sơ đồ
   →  Arch §10.2 Khóa định danh  ⇄  WF B1 Trùng lặp
```

**Đọng lại**: `CHECK (raised_by IN (...))` là **mã hóa DDL** của nguyên tắc "LLM không bật cờ" —
thêm một agent vào đó đòi hỏi một migration, nên nó không thể xảy ra vô tình trong một node.

### Chặng 4 — "Người duyệt bước vào chỗ nào?" (~20 phút)

```
Arch §6.3 batch_graph  →  WF A1 Ingest  →  WF A4 Ngân sách  →  WF A5 Route outcomes
   →  Arch §6.4 approval_graph  ⇄  WF A6 HITL                ← đọc SONG SONG
   →  Arch §11 Bền vững HITL                                  ← interrupt, kill -9, serde
   →  WF A8 Learn  →  WF C3 Phục hồi
```

**Đọng lại**: *node chứa `interrupt()` chạy lại TỪ ĐẦU khi resume* — và vì sao hệ quả của câu đó là
side effect không được nằm cùng node với cổng duyệt.

### Chặng 5 — "Tiền, model, và dữ liệu" (~20 phút)

```
Arch §9.1 gateway  →  Arch §9.2 ba mode  ⇄  WF C2 Record/replay
   →  Arch §9.3 cost  ⇄  WF B2 Telemetry chi phí
   →  Arch §9.4 structured  (quay lại WF A3 một nhịp — hai tầng retry)
   →  Arch §10 Lớp dữ liệu  →  Arch §13 Cấu hình  ⇄  WF C1 Bootstrap
   →  WF B3 Ba loại memory
```

**Đọng lại**: vì sao có **hai** cột tiền, và vì sao `LLM_MODE=replay` không phải tiện ích test mà là
mode **duy nhất** khiến gateway chạy được trên CI không có key.

### Chặng 6 — "Làm sao biết nó đúng?" (~20 phút)

```
Arch §14 Kiểm thử/CI/corpus  ⇄  WF D2 Mutation guard  +  WF D4 CI
   →  Arch §12 Bảo mật  →  WF C4 Eval  (§10.1 vấn đề tuần hoàn)
   →  WF D1 Vòng lặp milestone  +  WF D3 Migration
   →  Arch §15 Trạng thái & nợ  ⇄  WF E Bảng 22 flow
```

**Đọng lại**: *"Test xanh không chứng minh gì. Test ĐỎ khi luật bị phá mới chứng minh."* — và
`docs/notes/` ghi lại **6 lần review độc lập bắt được lỗi mà bản implement đã báo là đạt**.

---

## Bảng tra chéo

Khi bạn đã biết mình đang tìm gì.

| Chủ đề | Architecture (cấu tạo) | Workflow (diễn biến) |
|---|---|---|
| Ba graph, vì sao tách | §6.1 | A0 · A5 |
| Enrichment, 8 node | §6.2 | A2 |
| Leo thang XML→vision→manual | §6.2, §7.2 | **A3** |
| Parse XML QĐ-1450 | §7.1, §7.2 | A2 bước 2 |
| Nhánh vision / PDF | §7.3 | A2 bước 3 · A3 |
| Luật validation, số học | §8 | A2 bước 4 · B4 |
| Cờ — sinh ra và lưu | §3.1, §8 | **B4** |
| Ngân sách serial | §8 (`budget.py`) | **A4** |
| Trùng lặp | §10.2 | B1 |
| HITL, interrupt, kill -9 | §6.4, **§11** | **A6** · C3 |
| Post bút toán, guard | §3.1, §10.6 | **A7** |
| Memory (3 loại) | §8, §10.1 | B3 · A8 |
| Gateway, fallback, key rotation | §9.1 | — |
| Chi phí, shadow cost | §9.3 | **B2** |
| record / replay | §9.2 | **C2** |
| Prompt injection | §12.1, §12.2, §7.4 | A3 (⚠️ mục cuối) · C4 |
| Schema, ràng buộc, quyền | §10 | A7 |
| Migration, Alembic | §10.5 | D3 |
| Cấu hình, env, DSN | §13 | C1 |
| Test, mutation guard | §14.2 | **D2** |
| CI | §14.4 | D4 |
| Corpus synthetic | §14.5 | C4 |
| Eval, chống tuần hoàn | §14.5 | **C4** |
| MCP | — | C5 |
| Trạng thái & nợ kỹ thuật | §15 | E |

### …và khi câu hỏi là "hàm nào?"

Hai cột trên trả lời ở mức module và mức bước. Khi bạn cần **chữ ký cụ thể**, đây là chỗ
[`Function-Map.md`](Function-Map.md) tiếp lời:

| Chủ đề | Function-Map |
|---|---|
| Chuỗi gọi đầy đủ của một hóa đơn | §1 (11 mục con, có sequence diagram) |
| `EnrichmentState` — field nào, reducer nào, node nào ghi | §1.3 |
| 5 node: đọc field gì, trả field gì | §1.4 · router ở §1.5 |
| Parse XML — từng helper của `xml_schema` | §1.6 |
| Vision → structured → gateway, xuống tận `litellm.completion` | §1.7 |
| Validate — 10 mã finding và ngưỡng của chúng | §1.8 |
| Chi phí: từ `LLMCallRecord` tới hai cột tiền trong DB | §1.9 |
| Ghi `invoices` + backfill `llm_calls` qua `thread_id` | §1.10 |
| Sinh corpus · seed · migration · render graph · HITL spike | §2–§6 |
| `get_settings()` → 6 property DSN → ai dùng cái nào | §7 |
| **Hàm đã có nhưng chưa ai gọi** (10 repository, 3 domain, 6 hạ tầng) | **§8** |
| Mở một file bất kỳ và hỏi "nó nối vào đâu?" | **§9** |

---

## Ba mẹo đọc

1. **Gặp 🔲 thì đừng bỏ qua.** Phần chưa xây vẫn được viết đầy đủ, vì ý nghĩa của một bước chỉ hiện
   ra khi thấy bước trước và bước sau nó. A5 (*"batch kết thúc ở đây, không chờ"*) là vô nghĩa nếu
   chưa đọc A6.

2. **Con số nào cũng truy được về nguồn.** Mọi hằng số và mọi phép đo trong hai tài liệu đều mang
   **ngày + phép đo**. Thấy một con số lạ thì `grep` nó trong `src/` — comment ở đó nói vì sao.
   Chỗ nào **chưa đo được** thì tài liệu nói thẳng là chưa đo (ví dụ `NEAR_DUP_WINDOW_DAYS`).

3. **Sơ đồ có nguồn.** Mỗi PNG trong `docs/diagrams/` có file `.mmd` đi kèm. Riêng
   `enrichment_graph.mmd` được **sinh từ graph đã compile** và có test chống trôi lệch — nó không
   thể âm thầm ngừng mô tả đúng code.

---

## Đọc tiếp

| Câu hỏi | Tài liệu |
|---|---|
| Hàm nào gọi hàm nào, input/output là gì | [`Function-Map.md`](Function-Map.md) |
| Thiết kế đầy đủ, gồm cả phần chưa làm | [`project-f-system-design.md`](project-f-system-design.md) |
| Milestone, gate, trạng thái từng hạng mục | [`Plan_Project_F_2026-07-25.md`](Plan_Project_F_2026-07-25.md) §3, §7 |
| Chuyện gì đã xảy ra ở mỗi milestone | [`notes/`](notes/) — 6 file |
| Vì sao cô lập schema `langgraph` | [`adr/0002-…`](adr/0002-alembic-langgraph-schema-isolation.md) |
