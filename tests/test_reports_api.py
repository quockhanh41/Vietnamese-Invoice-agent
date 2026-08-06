"""The commentary endpoint reads the same aggregates the dashboard reads.

The single-source-of-truth test is the load-bearing one: a route that ran its
own SELECT could disagree with the numbers on the same page, which is the exact
failure `api/routes/reports.py`'s module docstring was written to prevent.
"""

import inspect

import pytest
from fastapi.testclient import TestClient

from invoice_agent.api.app import create_app
from invoice_agent.api.routes import reports
from invoice_agent.llm.cache import LLMReplayMiss


@pytest.fixture
def api():
    return TestClient(create_app())


def test_the_route_module_runs_no_sql_of_its_own():
    source = inspect.getsource(reports)
    for token in ("select(", "text(", "SELECT ", "sa.select"):
        assert token not in source, (
            f"{token!r} in the reports route: aggregates come from db/queries, "
            "so the narrative and the dashboard cannot disagree"
        )


@pytest.fixture
def unreachable_model(monkeypatch):
    """No test in this file may make a live API call.

    `tests/conftest.py` does NOT pin `LLM_MODE`, so on a developer machine with
    keys in `.env` the route below would call a real provider during an ordinary
    `make check`. M4-D shipped a gate that was making live calls while claiming
    to replay; this fixture is why that cannot happen here.
    """

    class _Unreachable:
        def generate(self, *a, **kw):
            raise LLMReplayMiss("no fixture in tests")

    monkeypatch.setattr(reports, "LLMGateway", lambda *a, **kw: _Unreachable())


def test_commentary_survives_an_unreachable_model(api, unreachable_model):
    """The endpoint must degrade to the template, never to a 500.

    The public demo runs `LLM_MODE=replay` against a fixture set recorded for a
    fixed dataset; a visitor asking for an unrecorded period must still get a
    report page (spec §8 risk 2)."""
    response = api.get("/reports/commentary", params={"period": "2026-08"})
    assert response.status_code == 200
    assert response.json()["source"] == "template"


def test_commentary_returns_facts_beside_the_prose(api, unreachable_model):
    """`facts` ships with the prose on purpose: a reader can check the sentence
    against the numbers it was built from."""
    body = api.get("/reports/commentary", params={"period": "2026-08"}).json()
    assert body["period"] == "2026-08"
    assert "facts" in body and "summary" in body
    assert isinstance(body["observations"], list)
