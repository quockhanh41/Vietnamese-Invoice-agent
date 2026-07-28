"""The flag vocabulary is shared with the database, and must not drift from it."""

import dataclasses

import pytest

from invoice_agent.db import models
from invoice_agent.domain import findings


def test_sources_match_the_database_check_constraint():
    """`domain/` deliberately does NOT import `db/` (purity), so the two
    vocabularies are written independently. This test is what stops them
    drifting: a mismatch would surface in M3 as an IntegrityError from
    `ck_flags_raised_by` at runtime, on a real invoice, instead of here."""
    assert findings.SOURCES == models.FLAG_SOURCES


def test_severities_match_the_database_check_constraint():
    assert findings.SEVERITIES == models.FLAG_SEVERITIES


def test_every_declared_code_maps_to_a_legal_source_and_severity():
    for code, (severity, source) in findings.CODE_REGISTRY.items():
        assert severity in findings.SEVERITIES, f"{code}: bad severity {severity}"
        assert source in findings.SOURCES, f"{code}: bad source {source}"


def test_finding_is_frozen():
    """Findings travel from domain -> node -> repository. A mutable one could be
    edited after the deterministic rule produced it, which is exactly the hole
    the safety property closes."""
    f = findings.arithmetic_mismatch(expected="1", actual="2")
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.code = "SOMETHING_ELSE"
