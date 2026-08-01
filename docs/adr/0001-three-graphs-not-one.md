# ADR 0001 — Ba đồ thị độc lập, không phải một đồ thị khổng lồ

**Ngày**: 2026-08-01 · **Trạng thái**: chấp nhận
**Bối cảnh**: M4-D · **Liên quan**: `src/invoice_agent/graphs/batch.py`, `src/invoice_agent/graphs/enrichment.py`, design §1.1

## Bối cảnh

Khi thiết kế kiến trúc orchestration với LangGraph, một lựa chọn ban đầu là đưa toàn bộ vòng đời của hoá đơn (từ ingest, fan-out enrichment, kiểm tra tài chính, đến chờ duyệt HITL và hạch toán) vào duy nhất **một graph lớn**.

Tuy nhiên, mô hình đơn thị (single-graph) gặp các hạn chế nghiêm trọng:
1. **Ranh giới thời gian và thực thi:** Tiến trình xử lý theo batch diễn ra tự động trong vài giây/phút, trong khi luồng chờ con người duyệt (HITL) có thể kéo dài hàng giờ hoặc hàng ngày. Việc để một luồng batch treo checkpoint chờ human input làm phức tạp hoá state management và chốt hạ lô.
2. **Khôi phục và checkpoint:** Mỗi lượt chạy batch cần có môi trường checkpoint cách ly. Nếu dùng chung `thread_id = enrich:{invoice_id}` giữa các đợt chạy lại, lượt batch sau sẽ nạp lại checkpoint của lượt batch trước thay vì chạy mới (đã được ghi nhận tại M3-C §8.5 và giải quyết bởi `enrich:{batch_id}:{invoice_id}`).

## Quyết định

Hệ thống được chia thành **ba đồ thị độc lập**:
1. **`enrichment_graph`**: Đồ thị xử lý một hoá đơn (read-only fan-out unit), chịu trách nhiệm trích xuất, phân loại và kiểm tra quy tắc.
2. **`batch_graph`**: Đồ thị quản lý song song lô (fan-out `Send`), thu thập kết quả và thực hiện serial join để ghi DB và đối soát ngân sách.
3. **Approval / Post Graph (M5)**: Đồ thị độc lập quản lý vòng đời duyệt của con người và hạch toán kế toán.

## Bằng chứng & Hệ quả

- **Bằng chứng**: Bổ sung `batch_id` vào `thread_id` (`enrich:{batch_id}:{invoice_id}`) đảm bảo tính độc lập tuyệt đối giữa các đợt chạy. Các thử nghiệm tại M4-D xác nhận `batch_graph` hoàn thành trọn vẹn và phân loại hoá đơn vào `auto` / `needs_approval` mà không bị nghẽn bởi bất kỳ luồng chờ duyệt nào.
- **Hệ quả**: Giảm độ phức tạp của đồ thị, giúp mỗi component dễ kiểm thử (unit & integration) và tránh rò rỉ state.
