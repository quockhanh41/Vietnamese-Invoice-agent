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


import json
import pathlib


def _read_manifest(out: pathlib.Path) -> list[dict]:
    with (out / "manifest.jsonl").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


class TestCorpusWiring:
    """The 15 pdf_vision entries carried `path: None` from M2-C, deliberately
    labelled 'rendered at M3.4'. This is M3.4."""

    def test_every_pdf_entry_has_a_file_on_disk(self, tmp_path):
        from scripts.generate_invoices import generate

        generate(count=60, seed=42, out=tmp_path)
        rows = _read_manifest(tmp_path)

        pdf_rows = [r for r in rows if r["source_type"] == "pdf_vision"]
        assert pdf_rows, "the 60-invoice slice produced no PDF entries"
        for row in pdf_rows:
            assert row["path"] is not None, f"{row['invoice_id']} still has path=None"
            assert (tmp_path.parent / row["path"]).exists() or (
                tmp_path / pathlib.Path(row["path"]).name
            ).exists(), f"{row['path']} named in the manifest but not on disk"

    def test_exactly_three_xml_entries_carry_a_pdf_twin(self, tmp_path):
        """M3-C's gate needs 'an XML invoice with an extraction error escalates
        to vision', and no M2-C entry had both."""
        from scripts.generate_invoices import generate

        generate(count=300, seed=42, out=tmp_path)
        rows = _read_manifest(tmp_path)
        twins = [r for r in rows if r.get("pdf_path")]
        assert len(twins) == 3
        for row in twins:
            assert row["source_type"] == "xml"
            assert (tmp_path / pathlib.Path(row["pdf_path"]).name).exists()

    def test_regenerating_is_byte_identical_including_pdfs(self, tmp_path):
        """M2-C's property, extended to the format that embeds a clock."""
        from scripts.generate_invoices import generate

        first, second = tmp_path / "a", tmp_path / "b"
        generate(count=60, seed=42, out=first)
        time.sleep(1.2)
        generate(count=60, seed=42, out=second)

        names = sorted(p.name for p in first.iterdir())
        assert names == sorted(p.name for p in second.iterdir())
        for name in names:
            assert (first / name).read_bytes() == (second / name).read_bytes(), name
        assert any(n.endswith(".pdf") for n in names), "no PDF was compared"

