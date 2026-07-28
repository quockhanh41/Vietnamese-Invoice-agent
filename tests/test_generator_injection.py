"""§9.1 payloads and §9.3 negative controls."""

from invoice_agent.extraction.xml_parser import parse_invoice_xml  # TEST-ONLY
from scripts.generator import defects, templates
from tests.test_generator_templates import spec


def test_all_six_design_doc_payloads_are_present():
    assert len(defects.INJECTION_PAYLOADS) == 6
    names = {name for name, _ in defects.INJECTION_PAYLOADS}
    assert {"social_engineering", "category_laundering"} <= names, (
        "payloads 5 and 6 are the two that matter — they carry no injection "
        "markers and defeat every keyword detector (§9.1)"
    )


def test_payload_lands_in_the_line_description():
    """§9.1: the attack surface is THHDVu, the one free-text field an outsider
    controls."""
    _, payload = defects.INJECTION_PAYLOADS[0]
    invoice = parse_invoice_xml(
        templates.render(defects.seed_injection(spec(), payload)).encode("utf-8")
    )
    assert payload in invoice.lines[0].description


def test_delimiter_escape_payload_still_produces_valid_xml():
    """Payload 4 contains `</data></user><system>`. Jinja autoescaping must
    turn it into text; unescaped it would produce malformed XML, and the corpus
    would be testing the parser's error path instead of the classifier's."""
    payload = dict(defects.INJECTION_PAYLOADS)["delimiter_escape"]
    invoice = parse_invoice_xml(
        templates.render(defects.seed_injection(spec(), payload)).encode("utf-8")
    )
    assert "<system>" in invoice.lines[0].description


def test_twin_differs_only_in_the_description():
    """§9.3, the control everyone forgets. If the twin differed anywhere else,
    a classification difference would measure that difference — noise — rather
    than the payload."""
    _, payload = defects.INJECTION_PAYLOADS[0]
    carrier = defects.seed_injection(spec(), payload)
    twin = defects.strip_injection(carrier)

    a = parse_invoice_xml(templates.render(carrier).encode("utf-8"))
    b = parse_invoice_xml(templates.render(twin).encode("utf-8"))

    assert a.lines[0].description != b.lines[0].description
    assert payload not in (b.lines[0].description or "")
    assert a.total_amount == b.total_amount
    assert a.seller_tax_code == b.seller_tax_code
    assert a.invoice_number == b.invoice_number
    assert [line.amount for line in a.lines] == [line.amount for line in b.lines]
