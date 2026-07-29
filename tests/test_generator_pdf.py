"""Corpus PDF rendering. Generator-side: no parser, no domain.

Byte-stability matters as much as legibility here — M2-C established that
`make generate --seed 42` is reproducible, and PDF is the first format in this
corpus that embeds a clock.
"""

import time

import pypdfium2
import pytest

from scripts.generator import pdf
from tests.helpers.invoice_specs import make_spec


class TestRendering:
    def test_output_is_a_pdf_that_opens(self):
        blob = pdf.render_pdf(make_spec())
        assert blob.startswith(b"%PDF-")
        doc = pypdfium2.PdfDocument(blob)
        assert len(doc) == 1

    def test_vietnamese_diacritics_survive_rendering(self):
        """fpdf2's core fonts are Latin-1. Rendering `Đ`, `ị`, `ữ` with one
        raises or mangles them, and a corpus of mangled Vietnamese would make
        every vision accuracy number meaningless."""
        blob = pdf.render_pdf(make_spec())
        page = pypdfium2.PdfDocument(blob)[0]
        text = page.get_textpage().get_text_range()
        assert "DỊCH VỤ" in text
        assert "Dịch vụ tư vấn quản trị doanh nghiệp" in text

    def test_the_total_appears_so_a_model_can_read_it(self):
        text = (
            pypdfium2.PdfDocument(pdf.render_pdf(make_spec()))[0]
            .get_textpage()
            .get_text_range()
        )
        assert "1.290.107" in text or "1290107" in text


class TestByteStability:
    def test_two_renders_more_than_a_second_apart_are_identical(self):
        """MEASURED 2026-07-29 (spec §2.3): fpdf2 embeds a wall-clock
        /CreationDate. Two renders back to back are identical because they land
        in the same second — a guard written without the sleep PASSES while
        guarding nothing. 2.2s apart, unpinned output differs
        (D:20260729014047Z vs …49Z)."""
        first = pdf.render_pdf(make_spec())
        time.sleep(1.2)
        second = pdf.render_pdf(make_spec())
        assert first == second

    def test_creation_date_is_the_pinned_one(self):
        blob = pdf.render_pdf(make_spec())
        assert pdf.PINNED_CREATION_DATE.strftime("D:%Y%m%d").encode() in blob


class TestBrokenVariants:
    """Spec §5.1: three different failures, because they exercise three routes."""

    def test_truncated_pdf_cannot_be_opened(self):
        with pytest.raises(pypdfium2.PdfiumError):
            pypdfium2.PdfDocument(pdf.render_truncated(make_spec()))

    def test_blank_pdf_opens_but_carries_no_invoice_text(self):
        doc = pypdfium2.PdfDocument(pdf.render_blank())
        assert len(doc) == 1
        assert doc[0].get_textpage().get_text_range().strip() == ""

    def test_degraded_pdf_opens_and_is_not_the_readable_one(self):
        degraded = pdf.render_degraded(make_spec())
        assert len(pypdfium2.PdfDocument(degraded)) == 1
        assert degraded != pdf.render_pdf(make_spec())
