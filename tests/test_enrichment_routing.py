"""route_after_validate — extraction error vs data error.

Design §3.3: "With XML this is theatre. The XML parser is a pure function: same
input, same output, byte-identical. Retrying three times gives exactly three
identical results." The distinction IS the self-correction; the loop is not.
"""

from langgraph.graph import END

from invoice_agent.domain import findings
from invoice_agent.graphs.enrichment import MAX_VISION_ATTEMPTS, route_after_validate

A_FINDING = [findings.make(findings.ARITHMETIC_MISMATCH)]


def state(**overrides) -> dict:
    base = {
        "invoice_id": "INV-0001",
        "source_path": "corpus/INV-0001.xml",
        "pdf_path": None,
        "source_type": "xml",
        "strategy": "xml",
        "extracted": object(),
        "extraction_attempts": 0,
        "current_errors": [],
        "error_history": [],
        "findings": [],
        "llm_calls": [],
        "status": "received",
    }
    base.update(overrides)
    return base


class TestExtractionFailed:
    def test_xml_extraction_failure_climbs_to_vision_when_a_pdf_exists(self):
        assert (
            route_after_validate(state(extracted=None, pdf_path="corpus/INV-0001.pdf"))
            == "vision_extract"
        )

    def test_xml_extraction_failure_without_a_pdf_gives_up(self):
        assert route_after_validate(state(extracted=None)) == "manual_queue"

    def test_vision_extraction_failure_retries_while_attempts_remain(self):
        assert (
            route_after_validate(
                state(
                    extracted=None,
                    strategy="vision",
                    source_type="pdf",
                    extraction_attempts=1,
                )
            )
            == "vision_extract"
        )

    def test_vision_gives_up_after_three_attempts(self):
        assert (
            route_after_validate(
                state(
                    extracted=None,
                    strategy="vision",
                    source_type="pdf",
                    extraction_attempts=MAX_VISION_ATTEMPTS,
                )
            )
            == "manual_queue"
        )


class TestExtractionSucceeded:
    def test_a_clean_invoice_ends(self):
        assert route_after_validate(state()) == END

    def test_xml_findings_never_retry(self):
        """The heart of it. A finding on an XML invoice is a fact ABOUT THE
        INVOICE — a DATA error. Retrying a pure function three times produces
        three identical results; the flag travels to the human instead."""
        assert route_after_validate(state(findings=A_FINDING)) == END

    def test_vision_findings_do_retry(self):
        """The extractor is LOSSY, so the same ARITHMETIC_MISMATCH may be
        misread digits — and design §3.3 says this is the retry that works."""
        assert (
            route_after_validate(
                state(
                    strategy="vision",
                    source_type="pdf",
                    findings=A_FINDING,
                    extraction_attempts=1,
                )
            )
            == "vision_extract"
        )

    def test_vision_findings_stop_retrying_at_the_bound(self):
        assert (
            route_after_validate(
                state(
                    strategy="vision",
                    source_type="pdf",
                    findings=A_FINDING,
                    extraction_attempts=MAX_VISION_ATTEMPTS,
                )
            )
            == "manual_queue"
        )
