# Hóa đơn điện tử THẬT — mỏ neo chống eval tuần hoàn

Đây là thứ duy nhất trong repo mà **không ai trong dự án này là tác giả**.

Corpus 300 hóa đơn ở M2 do `scripts/generate_invoices.py` sinh ra, và parser cũng do
chúng ta viết. Nếu cả hai cùng dựa trên một hiểu biết sai về định dạng thì parser đạt
100% — và 100% đó chỉ chứng minh generator với parser đồng ý với nhau. Mấy file ở đây
là mỏ neo bên ngoài (design doc §10.1, cách phá vòng tuần hoàn số 2).

**Quy tắc**: `xml_schema.py` viết theo file trong thư mục này **trước**, generator Jinja
viết **sau và độc lập**. Không được sửa file ở đây cho khớp parser — sửa parser cho khớp
file.

## Danh sách — 8 file, 8 nhà cung cấp, 3 phiên bản schema

| File | Loại | `PBan` | Root | Phủ riêng cái gì |
|---|---|---|---|---|
| `vnpt_hoadon_banhang_2.1.0.xml` | Bán hàng `KHMSHDon=2` | 2.1.0 | `HDon` | `ThTien=0`, tiền giấu trong `TTKhac`; `MCCQT`+`Fkey` |
| `viettelpost_gtgt_2.1.0.xml` | **GTGT** `KHMSHDon=1` | 2.1.0 | `HDon` | `THTTLTSuat`; `TTKhac/TTin` thứ tự con **ngược**; `Ten` người mua = `'.'` |
| `phongvu_gtgt_tdiep_2.0.0.xml` | GTGT | **2.0.0** | **`TDiep`** | Dạng phong bì; số thập phân 8 chữ số; MST **có gạch** `…-042` |
| `hokinhdoanh_banhang_qrcode_2.1.0.xml` | Bán hàng | 2.1.0 | `HDon`+`DLQRCode` | Dòng **`TChat=4`** (ghi chú); MST **12 số**; không có `NMua/Ten` |
| `lienket_gtgt_7dong_2.1.0.xml` | GTGT | 2.1.0 | `HDon` | **7 dòng hàng có giá trị** — file duy nhất lộ được sai lệch làm tròn; `THDon='TT78 AC'` |
| `newimage_gtgt_pban2.0.1.xml` | GTGT | **2.0.1** | `HDon` | Phiên bản schema **thứ ba**; `HVTNMHang` ≠ `Ten` (**hai người khác nhau**) |
| `dienluc_gtgt_cccd_2.1.0.xml` | GTGT | 2.1.0 | `HDon` | `CCCDan`; `TLCKhau`/`STCKhau` **vắng hẳn** (không phải `'0'`) |
| `ktx_banhang_cccd_2.1.0.xml` | Bán hàng | 2.1.0 | `HDon` | `CCCDan`; `TGTKCThue`, `TGTKhac` |

**Còn thiếu**: hóa đơn có chiết khấu (`TLCKhau`/`STCKhau` khác 0), hóa đơn **điều chỉnh/thay thế**,
hóa đơn nhiều mức thuế suất trong một tờ (`THTTLTSuat` nhiều `LTSuat`), MISA meInvoice / BKAV.

## Sai lệch làm tròn — đo được, và quyết định thiết kế validation

Đo trên toàn bộ file ở đây:

| Phép kiểm | Sai lệch lớn nhất | Kết luận |
|---|---:|---|
| `SLuong × DGia` vs `ThTien` | **0.01** | **Không** được so bằng `==` |
| `ThTien × TSuat` vs `TThue` | **0.48** | Thuế làm tròn tới đồng, lệch **cả hai chiều** |
| `Σ ThTien` vs `TgTCThue` | **0** | Khớp tuyệt đối |
| `Σ TThue` vs `TgTThue` | **0** | Khớp tuyệt đối |

→ **Kiểm ở mức TỔNG thì so `==`; kiểm ở mức DÒNG thì phải có tolerance.**

Ví dụ cụ thể trong `lienket_gtgt_7dong`: dòng STT7 có `3 × 398181.33 = 1194543.99` nhưng
`ThTien = 1194544`. Người bán tính đơn giá bằng cách chia ngược từ thành tiền đã làm tròn, nên nhân
lại không ra đúng. Đây là thứ **chỉ hóa đơn nhiều dòng có đơn giá lẻ mới lộ** — corpus tự sinh sẽ
luôn chia hết vì generator nhân ra từ đơn giá.

## Luật validation suy ra TỪ DỮ LIỆU, không phải đoán

```
sum(ThTien các dòng TChat==1, fallback TTKhac[Amount]) + TgTThue  ==  TgTTTBSo
```

Đo lại 2026-07-27 trên **cả 8 file: 8/8 khớp**.

Luật ngây thơ `sum(ThTien) == TgTTTBSo` bắn nhầm hóa đơn thật, hợp lệ, đã được Cục Thuế
ký số. Ba thành phần dưới đây không cái nào đoán được, mỗi cái ứng với một file:

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
| **Root** | `HDon` (7 file) **hoặc** `TDiep → TTChung + DLieu → … → HDon` (1 file). Parser phải định vị `HDon`, không giả định nó là root |
| **Namespace** | Phần dữ liệu **không** namespace ở cả 8 file. Chỉ subtree `Signature` mang `xmldsig#` |
| **Thứ tự con `TTKhac/TTin`** | Viettel: `DLieu, KDLieu, TTruong` — VNPT/Phong Vũ: `TTruong, KDLieu, DLieu`. **Đọc theo vị trí là vỡ** |
| **Thứ tự con `TToan`** | `THTTLTSuat` đứng **đầu** ở Viettel, đứng **cuối** ở Phong Vũ |
| **`TSuat`** | Chuỗi kèm `%`: `'8%'`, `'10%'`. Không phải `0.08`, không phải `8`. Vắng hoàn toàn ở hóa đơn không-GTGT |
| **Số tiền** | Cả `47777` lẫn `21618409.09000000` (8 chữ số thập phân). Bắt buộc `Decimal`, không `float` |
| **MST** | 3 hình dạng: `0100000003` (10 số), `0300000018-042` (**có gạch**, chi nhánh), `050000000000` (**12 số**, hộ kinh doanh). Plan M2.6 ghi "10/13 số" — thiếu cả hai dạng sau |
| **"Thiếu"** | **Ba** biểu hiện: giá trị `'0'`, element rỗng `<MST />` → `''`, và element vắng hẳn → `None` |
| **`PBan`** | **Ba** phiên bản cùng tồn tại: `2.0.0`, `2.0.1`, `2.1.0` |
| **`THDon` là free-text RÁC** | 6 biến thể quan sát được, gồm `'TT78 AC'` và `'HÓA ĐƠN ĐIỆN TỬ GIÁ TRỊ GIA TĂNG (ND123 - TT78)'`. **Chỉ dùng `KHMSHDon` để nhận loại hóa đơn** |
| **`HVTNMHang` không phải bản sao của `Ten`** | Ở `newimage_…`: `Ten='NGUYỄN THỊ C'` nhưng `HVTNMHang='LÊ VĂN D'` — **hai người khác nhau** (chủ tài khoản vs người trực tiếp mua). Ở `hokinhdoanh_…` nó lại chứa **email**. Trường này bị dùng loạn ngoài đời |
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

Kiểm chứng khi tạo — cây tag bản gốc vs bản ẩn danh **giống hệt từng node**, cả 8 file:
`110/110`, `173/173`, `133/133`, `89/89`, `112/112`, `429/429`, `79/79`, `216/216`.
Luật validation vẫn cho **8/8** sau khi ẩn danh, và các đặc điểm cần test đều sống sót
(`HVTNMHang` vẫn khác `Ten`, `PBan` vẫn `2.0.1`, `CCCDan` vẫn có mặt với 12 chữ số,
`lienket_…` vẫn đủ 7 dòng và vẫn giữ sai lệch làm tròn 0.01).

**Bốn file `lienket_`, `newimage_`, `dienluc_`, `ktx_` là hóa đơn của người nhà chủ repo,
được họ đồng ý cho dùng.** Số CCCD đã thay bằng `000000000000`/`000000000001` — giữ đúng
12 chữ số để test hình dạng, không còn truy về ai được.

⚠️ Hóa đơn thật mang PII **và chứng thư số thật**. gitleaks **không bắt được** — nó tìm
token, không tìm PII. `.gitignore` chặn `*.xml`/`*.pdf`/`*.zip` ở **mọi cấp** (nhà cung cấp
giao hóa đơn trong thư mục — neo `/` ở root từng để lọt nguyên một thư mục có 2 PDF
chứa CCCD), và whitelist lại `tests/**`; chỉ bản đã ẩn danh trong thư mục này mới được commit.
