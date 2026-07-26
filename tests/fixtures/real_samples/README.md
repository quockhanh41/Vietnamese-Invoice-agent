# Hóa đơn điện tử THẬT — mỏ neo chống eval tuần hoàn

Đây là thứ duy nhất trong repo mà **không ai trong dự án này là tác giả**.

Corpus 300 hóa đơn ở M2 do `scripts/generate_invoices.py` sinh ra, và parser cũng do
chúng ta viết. Nếu cả hai cùng dựa trên một hiểu biết sai về định dạng thì parser đạt
100% — và 100% đó chỉ chứng minh generator với parser đồng ý với nhau. Mấy file ở đây
là mỏ neo bên ngoài (design doc §10.1, cách phá vòng tuần hoàn số 2).

**Quy tắc**: `xml_schema.py` viết theo file trong thư mục này **trước**, generator Jinja
viết **sau và độc lập**. Không được sửa file ở đây cho khớp parser — sửa parser cho khớp
file.

## Danh sách

| File | Nhà cung cấp | Loại | `PBan` | Ghi chú |
|---|---|---|---|---|
| `vnpt_hoadon_banhang_2.1.0.xml` | VNPT (`MSTTCGP=0100684378`) | Hóa đơn bán hàng (`KHMSHDon=2`) — **không GTGT** | 2.1.0 | Phát hành 2025-09-08, đã có chữ ký CQT |

**Còn thiếu** (ưu tiên theo thứ tự này):

1. **Hóa đơn GTGT** (`KHMSHDon=1`) — quan trọng nhất. Toàn bộ pipeline phân loại thuế +
   tài khoản TT133 xoay quanh hóa đơn GTGT, mà mẫu hiện có **không có trường thuế nào**.
   Hóa đơn điện/nước/internet/cước điện thoại đều là GTGT.
2. Nhà cung cấp khác: Viettel Invoice / MISA meInvoice / BKAV eHoadon. Phần lõi do
   QĐ-1450 quy định thì giống nhau, nhưng chỗ quy định để mở thì mỗi bên một kiểu.
3. Hóa đơn nhiều dòng hàng (mẫu hiện có chỉ 1 dòng).

## Ẩn danh

Các file ở đây **đã được ẩn danh**. Nguyên tắc: **giữ nguyên cấu trúc, chỉ thay giá trị**
— cấu trúc mới là thứ có giá trị kiểm thử.

Đã thay: tên/email/mã người mua, tên + MST + địa chỉ + số tài khoản người bán, nội dung
`X509Certificate` / `SignatureValue` / `DigestValue`, `MCCQT`, `Fkey`.

Đã **cố ý giữ nguyên**, vì đây chính là những thứ cần test:

- `ThTien=0` trong khi `TgTTTBSo=3500000` — tiền thật nằm ở `TTKhac`, xem bên dưới
- `<SLuong />`, `<DGia />`, `<TLCKhau />`, `<STCKhau />` rỗng dạng self-closing
- `TSuat` và `DVTinh` **vắng mặt** trên dòng hàng
- `NMua` **không có `MST`** (người mua là cá nhân)
- `TgTTTBChu` = `'Ba  triệu năm trăm ngàn đồng./.'` — hai dấu cách sau "Ba", "ngàn"
  chứ không phải "nghìn", hậu tố `./.`
- Toàn bộ cây `DSCKS` với namespace `xmldsig#` (parser phải bỏ qua đúng cách)

Kiểm chứng khi tạo: cây tag của bản gốc và bản ẩn danh **giống hệt, 110 node cả hai**.

MST người bán đổi thành `0300000018` — số này **hợp lệ checksum** (W=31,29,23,19,17,13,7,5,3),
nên nó vẫn dùng được làm ca dương tính cho validator ở M2.6.

## Bốn phát hiện phá giả định của plan

Ghi ở đây vì chúng là lý do tồn tại của thư mục này.

1. **Không có `TDiep`.** Root là `HDon` thẳng. `TDiep` là phong bì khi truyền lên CQT;
   file giao cho người mua không có nó. Parser mong `TDiep` fail 100% ngay file đầu tiên.
2. **Phần dữ liệu không có namespace.** Chỉ subtree `Signature` mang
   `xmlns="http://www.w3.org/2000/09/xmldsig#"`.
3. **`TSuat` không phải lúc nào cũng có.** `KHMSHDon=2` là hóa đơn bán hàng theo phương
   pháp trực tiếp — không có trường thuế suất nào. Mọi field thuế phải là Optional.
4. **`TTKhac` — khối extension `(TTruong, KDLieu, DLieu)`** xuất hiện ở cả `TTChung` lẫn
   từng dòng hàng. Plan không hề nhắc tới nó. Ở file này **tiền thật của dòng hàng nằm
   trong đó** (`TTruong='Amount'` → `3500000`) chứ không phải ở `ThTien` (=0).

Hệ quả của (4): validation `sum(ThTien) == TgTTTBSo` sẽ gắn cờ một hóa đơn **thật, hợp lệ,
đã được Cục Thuế ký số** là sai số học. Đó là false positive trên hóa đơn sạch — đúng cái
metric `flag false-positive rate ≤5%` đang đo.
