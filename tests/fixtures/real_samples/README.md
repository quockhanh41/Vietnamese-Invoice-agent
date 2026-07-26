# Hóa đơn điện tử THẬT — mỏ neo chống eval tuần hoàn

Đây là thứ duy nhất trong repo mà **không ai trong dự án này là tác giả**.

Corpus 300 hóa đơn ở M2 do `scripts/generate_invoices.py` sinh ra, và parser cũng do
chúng ta viết. Nếu cả hai cùng dựa trên một hiểu biết sai về định dạng thì parser đạt
100% — và 100% đó chỉ chứng minh generator với parser đồng ý với nhau. Mấy file ở đây
là mỏ neo bên ngoài (design doc §10.1, cách phá vòng tuần hoàn số 2).

**Quy tắc**: `xml_schema.py` viết theo file trong thư mục này **trước**, generator Jinja
viết **sau và độc lập**. Không được sửa file ở đây cho khớp parser — sửa parser cho khớp
file.

## Danh sách — 4 file, phủ 4 nhà cung cấp

| File | Loại | `PBan` | Root | Phủ riêng cái gì |
|---|---|---|---|---|
| `vnpt_hoadon_banhang_2.1.0.xml` | Bán hàng `KHMSHDon=2` | 2.1.0 | `HDon` | `ThTien=0`, tiền giấu trong `TTKhac`; `MCCQT`+`Fkey` |
| `viettelpost_gtgt_2.1.0.xml` | **GTGT** `KHMSHDon=1` | 2.1.0 | `HDon` | `THTTLTSuat`; `TTKhac/TTin` thứ tự con **ngược**; `Ten` người mua = `'.'` |
| `phongvu_gtgt_tdiep_2.0.0.xml` | **GTGT** | **2.0.0** | **`TDiep`** | Dạng phong bì; số thập phân 8 chữ số; MST **có gạch** `…-042` |
| `hokinhdoanh_banhang_qrcode_2.1.0.xml` | Bán hàng | 2.1.0 | `HDon` + `DLQRCode` | Dòng **`TChat=4`** (ghi chú, không phải hàng hoá); MST **12 số**; không có `NMua/Ten` |

**Còn thiếu**: hóa đơn nhiều dòng hàng thật (≥3 dòng có giá trị), hóa đơn có chiết khấu
(`TLCKhau`/`STCKhau` khác 0), hóa đơn điều chỉnh/thay thế, MISA meInvoice / BKAV eHoadon.

## Luật validation suy ra TỪ DỮ LIỆU, không phải đoán

```
sum(ThTien các dòng TChat==1, fallback TTKhac[Amount]) + TgTThue  ==  TgTTTBSo
```

Đo 2026-07-26 trên các file này: **4/4 khớp**.

Luật ngây thơ `sum(ThTien) == TgTTTBSo` **bắn nhầm 4/5** hóa đơn thật, hợp lệ, đã được
Cục Thuế ký số. Ba thành phần dưới đây không cái nào đoán được, mỗi cái ứng với một file:

- **`TChat==1`** — `TChat=4` là dòng **ghi chú**, chỉ có `THHDVu`, không có giá trị tiền.
  Cộng nó vào là sai. (`hokinhdoanh_…`)
- **fallback `TTKhac`** — VNPT để `ThTien=0` và đặt tiền thật ở
  `TTKhac/TTin[TTruong='Amount']/DLieu`. (`vnpt_…`)
- **`+ TgTThue`** — với hóa đơn GTGT, `ThTien` là tiền **trước** thuế. (`viettelpost_…`,
  `phongvu_…`)

Đây chính là kịch bản §10.1: parser viết theo trí nhớ sẽ chứa luật ngây thơ, generator
cũng sinh XML khớp luật đó, 300/300 pass, và report ghi `false-positive rate 0%` — một
con số bịa.

## Khác biệt giữa nhà cung cấp — thứ `xml_schema.py` phải chịu được

| Điểm | Thực tế quan sát được |
|---|---|
| **Root** | `HDon` (3 file) **hoặc** `TDiep → TTChung + DLieu → … → HDon` (1 file). Parser phải định vị `HDon`, không giả định nó là root |
| **Namespace** | Phần dữ liệu **không** namespace ở cả 4 file. Chỉ subtree `Signature` mang `xmldsig#` |
| **Thứ tự con `TTKhac/TTin`** | Viettel: `DLieu, KDLieu, TTruong` — VNPT/Phong Vũ: `TTruong, KDLieu, DLieu`. **Đọc theo vị trí là vỡ** |
| **Thứ tự con `TToan`** | `THTTLTSuat` đứng **đầu** ở Viettel, đứng **cuối** ở Phong Vũ |
| **`TSuat`** | Chuỗi kèm `%`: `'8%'`, `'10%'`. Không phải `0.08`, không phải `8`. Vắng hoàn toàn ở hóa đơn không-GTGT |
| **Số tiền** | Cả `47777` lẫn `21618409.09000000` (8 chữ số thập phân). Bắt buộc `Decimal`, không `float` |
| **MST** | 3 hình dạng: `0100000003` (10 số), `0300000018-042` (**có gạch**, chi nhánh), `050000000000` (**12 số**, hộ kinh doanh). Plan M2.6 ghi "10/13 số" — thiếu cả hai dạng sau |
| **"Thiếu"** | Hai kiểu: element rỗng `<MST />` → `''`, và element vắng mặt → `None`. Phải coi cả hai là thiếu |
| **`PBan`** | `2.0.0` và `2.1.0` cùng tồn tại |
| **Con của root** | `DLHDon`, `MCCQT`, `DSCKS`, `Fkey`, `DLQRCode` — tuỳ nhà cung cấp |

**Dữ liệu rác có thật** (validator đòi "hợp lệ" sẽ gắn cờ oan): `NMua/Ten = '.'`,
`NMua/STKNHang = '.'`, `HVTNMHang` chứa **email** thay vì họ tên, `NMua/DChi` =
`'Người mua không lấy hóa đơn'`, và một file **không có `NMua/Ten`** nào cả.

**Câu hỏi mở cho M2.6**: MST 12 số của hộ kinh doanh sinh từ CCCD — chưa rõ có quy tắc
checksum công khai nào áp dụng được không. Đừng cho validator từ chối nó cho tới khi trả
lời được câu này.

## Ẩn danh

Các file ở đây **đã được ẩn danh**. Nguyên tắc: **giữ nguyên cấu trúc, chỉ thay giá trị**
— cấu trúc mới là thứ có giá trị kiểm thử.

Đã thay: mọi PII người mua (tên, email, SĐT, địa chỉ, mã KH), tên + MST + địa chỉ + liên
hệ người bán, tên sản phẩm, mã tham chiếu/PO, nội dung `X509Certificate` /
`SignatureValue` / `DigestValue` / `X509SubjectName`, `MCCQT`, `DLQRCode`, `Fkey`, mã bí mật.

**MST thay thế giữ đúng hình dạng gốc** và các dạng 10 số dùng số **hợp lệ checksum**
(W=31,29,23,19,17,13,7,5,3), nên vẫn dùng được làm ca dương tính cho validator M2.6.

Kiểm chứng khi tạo — cây tag bản gốc vs bản ẩn danh **giống hệt từng node**:
`173 vs 173`, `133 vs 133`, `89 vs 89`, `110 vs 110`. Và luật validation ở trên vẫn cho
**4/4** sau khi ẩn danh.

⚠️ Hóa đơn thật mang PII **và chứng thư số thật**. gitleaks **không bắt được** — nó tìm
token, không tìm PII. `.gitignore` chặn `/*.xml` và `/*.pdf` ở root; chỉ bản đã ẩn danh
trong thư mục này mới được commit.
