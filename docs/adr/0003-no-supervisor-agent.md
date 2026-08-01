# ADR 0003 — Luồng thực thi cố định, không dùng LLM Supervisor Agent

**Ngày**: 2026-08-01 · **Trạng thái**: chấp nhận
**Bối cảnh**: M4-C, M4-D · **Liên quan**: `src/invoice_agent/graphs/batch.py`, `src/invoice_agent/graphs/enrichment.py`, design §6.2

## Bối cảnh

Trong thiết kế các hệ thống multi-agent với LangGraph, một pattern phổ biến là sử dụng một **Supervisor Agent** (dùng LLM) để dynamically quyết định routing và thứ tự gọi các node con dựa trên kết quả trung gian.

Tuy nhiên, trong bài toán đối soát hoá đơn doanh nghiệp:
1. **Ranh giới quy trình chuẩn hoá:** Thứ tự các bước (Trích xuất → Phân loại → Kiểm tra an toàn/Pháp lý → Reconcile & Ghi DB) là **business invariant** của quy trình kế toán. Việc cho phép LLM Supervisor tự do thay đổi thứ tự node tạo ra tính không định hình (non-determinism), rủi ro bỏ sót bước kiểm tra quan trọng và tăng chi phí latency / token không cần thiết.
2. **Hiệu quả của LLM "Second Opinion":** Kết quả thực nghiệm ở M4-C đánh giá độ chính xác của việc LLM phản biện/đánh giá lại phân loại cho thấy: tỷ lệ chính xác khi phản biện lại classifier đạt **12.5%** (CI 0.043–0.310, n=24). Tức là việc để một LLM khác "second-guess" một classifier đã đạt 95.4% mang lại tác hại th ròng (net harmful), chủ yếu tạo ra false alert.

## Quyết định

1. **Sử dụng Deterministic Control Flow:** Thứ tự điều hướng giữa các node trong `enrichment_graph` và `batch_graph` được quy định cứng bằng code Python và các hàm điều kiện chuẩn (`conditional_edges`).
2. **Không dùng Supervisor Agent:** Loại bỏ hoàn toàn role Supervisor dùng LLM điều phối trong đồ thị. Mỗi agent đảm nhận đúng một vai trò chuyên biệt (trích xuất, phân loại, audit) và được gọi theo thứ tự cố định.

## Bằng chứng & Hệ quả

- **Bằng chứng**:
  - Đo đạc M4-C: Phản biện thứ hai từ LLM chỉ có độ chính xác 12.5%, làm tăng false positive và giảm chất lượng chung của hệ thống.
  - Đo đạc M4-D: Luồng thực thi cố định qua LangGraph với Send fan-out giúp 30 hoá đơn chạy qua lô mượt mà, đạt 100% tính lặp lại (reproducibility) và cách ly lỗi hoàn hảo.
- **Hệ quả**: Tiết kiệm đáng kể latency và chi phí token, đảm bảo tính tuân thủ tuyệt đối quy trình kế toán, đồng thời dễ dàng kiểm thử và tạo replay test.
