"""Jinja rendering. Templates come from the REAL FIXTURES, not from xml_schema.

`hdon.xml.j2` was transcribed from `viettelpost_gtgt_2.1.0.xml` and
`tdiep.xml.j2` from `phongvu_gtgt_tdiep_2.0.0.xml`. That is the independence
mechanism: the parser derives its paths from its own map, the generator from
the documents, and only then does agreement mean something.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import pathlib

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from scripts.generator.amounts import to_amount_words

_TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"

_ENV = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    # StrictUndefined: a typo in a template must explode, not silently render
    # an empty element that the parser then reports as missing data.
    undefined=StrictUndefined,
    autoescape=True,
    keep_trailing_newline=False,
)


@dataclass(frozen=True)
class LineSpec:
    line_no: int
    item_kind: int
    description: str
    unit: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal
    vat_rate: Decimal | None
    vat_amount: Decimal | None
    ext_amount: Decimal | None      # VNPT shape: ThTien=0, real amount in TTKhac
    self_closing: bool              # `<SLuong />` -> None, not 0

    @property
    def vat_percent(self) -> str:
        """`Decimal('0.08')` -> `'8%'`. TSuat is a percent STRING in every real
        file; a bare number is not something any vendor emits."""
        return f"{(self.vat_rate * 100).normalize():f}%" if self.vat_rate is not None else ""


@dataclass(frozen=True)
class InvoiceSpec:
    root: str                       # 'hdon' | 'tdiep'
    schema_version: str
    template_no: str
    invoice_series: str
    invoice_number: str
    issue_date: date
    seller_name: str
    seller_tax_code: str
    seller_address: str
    buyer_name: str
    buyer_tax_code: str
    buyer_address: str
    ext_order: str                  # 'dlieu_first' | 'ttruong_first'
    thousand: str                   # 'nghìn' | 'ngàn'
    lines: list[LineSpec]
    subtotal: Decimal | None
    vat_total: Decimal | None
    total_amount: Decimal
    total_in_words: str | None      # None -> derived; set explicitly to seed the defect
    header_ext: list[tuple[str, str]] = field(
        default_factory=lambda: [("Trạng thái thanh toán", "Đã thanh toán")]
    )
    seller_ext: list[tuple[str, str]] = field(
        default_factory=lambda: [("Tỉnh/Thành phố người bán", "Hà Nội")]
    )

    @property
    def doc_id(self) -> str:
        return f"Id-{self.invoice_series}-{self.invoice_number}"

    @property
    def transmitter_mst(self) -> str:
        """Deliberately NOT the seller's — that difference is the whole point
        of the TDiep envelope as a parser trap."""
        return "REDACTED-MST"

    @property
    def invoice_kind_text(self) -> str:
        """THDon is free-text garbage in reality — 6 variants observed including
        'TT78 AC'. Only KHMSHDon identifies the kind, so this field is
        deliberately inconsistent."""
        return "Hóa đơn giá trị gia tăng" if self.template_no == "1" else "HÓA ĐƠN BÁN HÀNG"

    @property
    def words(self) -> str:
        return self.total_in_words or to_amount_words(self.total_amount, self.thousand)


def render(spec: InvoiceSpec) -> str:
    inner = _ENV.get_template("hdon.xml.j2").render(s=spec)
    if spec.root == "hdon":
        return inner
    body = inner[inner.index("<HDon>") :]
    return _ENV.get_template("tdiep.xml.j2").render(s=spec, inner=Markup(body))
