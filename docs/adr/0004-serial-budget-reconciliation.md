# ADR 0004 — Ghi DB và đối soát ngân sách tuần tự theo thứ tự chuẩn hoá

**Ngày**: 2026-08-01 · **Trạng thái**: chấp nhận
**Bối cảnh**: M4-D · **Liên quan**: `src/invoice_agent/graphs/batch.py`, `tests/test_budget_serialization.py`, design §0 Constraint 4 & §2.3

## Bối cảnh

Trong xử lý theo lô (batch handling), bước làm giàu dữ liệu (enrichment) diễn ra song song để tối ưu hoá hiệu năng (fan-out `Send`). Tuy nhiên, ở giai đoạn ghi DB, kiểm tra trùng lặp và đối soát ngân sách (budget consumption):
1. **Rủi ro Race Condition & Non-determinism:** Nếu ghi DB hoặc trừ ngân sách song song, kết quả số dư ngân sách còn lại (`balance_after`) và cờ cảnh báo bội chi (`BUDGET_BREACH`) sẽ phụ thuộc vào thứ tự hoàn thành ngẫu nhiên của các luồng mạng. Cùng một lô hoá đơn xử lý 2 lần sẽ ra 2 chuỗi ngân sách khác nhau.
2. **Xung đột UNIQUE Index:** Bảng hoá đơn có khoá tự nhiên `(seller_tax_code, invoice_series, invoice_number)`. Nếu nhiều worker cùng ghi song song, race condition tại DB gây ra deadlock hoặc lỗi trùng lặp không thể kiểm soát.

## Quyết định

1. **Phân tách Read-Only và Mutating Operations:**
   - Luồng Fan-Out (`enrich_one`) là **hoàn toàn Read-Only**. Mỗi worker chỉ đọc file, gọi LLM, tính toán score và trả kết quả về state mà không chạm vào DB hay mutate bất kỳ trạng thái chung nào.
   - Luồng Serial Join (`serial_join`) là **nút ghi duy nhất trong duy nhất 1 database transaction**.
2. **Sắp xếp theo thứ tự chuẩn hoá (Canonical Order):**
   - Trước khi ghi và trừ ngân sách, `serial_join` sắp xếp toàn bộ hoá đơn trong lô theo thứ tự: `(issue_date, invoice_series, invoice_number)`.
   - Quá trình ghi DB, cập nhật hồ sơ nhà cung cấp (`vendor_profile`), kiểm tra trùng lặp (`duplicate_invoice`, `near_duplicate`) và trừ ngân sách (`domain_budget.consume`) diễn ra tuần tự đúng theo thứ tự đã sắp xếp.

## Bằng chứng & Hệ quả

- **Bằng chứng (Portfolio Test 3 / `test_budget_serialization.py`)**:
  - Thực hiện 10 lần xáo trộn ngẫu nhiên thứ tự đến của 30 hoá đơn.
  - Sử dụng `SAVEPOINT` rollback để đảm bảo 10 lần chạy hoàn toàn độc lập.
  - Kết quả: Tất cả 10 lần chạy tạo ra chuỗi số dư ngân sách (`balance_after`) và các cờ cảnh báo (`BUDGET_BREACH` tại `INV-0001`) khớp nhau tuyệt đối từng byte.
  - Mutation check: Tạm thời gỡ bỏ bước `sorted(..., key=_canonical_key)` lập tức khiến test đỏ (`AssertionError: arrival order changed persistence order`), chứng minh test kiểm tra đúng thuộc tính cần bảo vệ.
- **Hệ quả**: Đảm bảo tính nhất quán dữ liệu kế toán tuyệt đối (deterministic transaction log), loại bỏ race condition và giúp hệ thống dễ dàng audit / replay.
