"""Extraction layer — XML now (M1.4), PDF/vision later (M3.4).

Both produce the same contract, `InvoiceData`. That is deliberate: the
enrichment graph's strategy-escalation loop (design doc §3.3) swaps XML for
vision without the downstream nodes noticing.
"""

from invoice_agent.extraction.models import InvoiceData, InvoiceLine
from invoice_agent.extraction.xml_parser import XmlExtractionError, parse_invoice_xml

__all__ = ["InvoiceData", "InvoiceLine", "XmlExtractionError", "parse_invoice_xml"]
