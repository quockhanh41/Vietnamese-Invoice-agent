"""Settings — chủ yếu là test hồi quy cho cái bẫy NoDecode.

Mọi test dựng `Settings(_env_file=None)` để không đọc `.env` của máy dev.
"""

import pytest

from invoice_agent.config import DEFAULT_MODEL_CHAIN, Settings


def make_settings(**env: str) -> Settings:
    """Settings hermetic: chỉ thấy env truyền vào, không thấy .env."""
    return Settings(_env_file=None, **env)


class TestNoDecodeCsv:
    """Nếu ai đó gỡ `Annotated[..., NoDecode]` khỏi config.py, mấy test này đỏ.

    Không có NoDecode, pydantic-settings chạy `json.loads("groq/a,gemini/b")`
    trước validator và ném SettingsError — nghĩa là app chết ngay lúc khởi
    động chứ không phải lỗi mơ hồ lúc chạy.
    """

    def test_csv_model_chain_parses(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL_CHAIN", "groq/llama-3.3-70b-versatile,gemini/gemini-3.5-flash")
        assert make_settings().llm_model_chain == [
            "groq/llama-3.3-70b-versatile",
            "gemini/gemini-3.5-flash",
        ]

    def test_single_value_becomes_one_element_list(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL_CHAIN", "groq/llama-3.3-70b-versatile")
        assert make_settings().llm_model_chain == ["groq/llama-3.3-70b-versatile"]

    def test_whitespace_and_empty_entries_dropped(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEYS", " gk1 , ,gk2,")
        assert make_settings().groq_api_keys == ["gk1", "gk2"]

    def test_default_chain_when_unset(self, monkeypatch):
        monkeypatch.delenv("LLM_MODEL_CHAIN", raising=False)
        assert make_settings().llm_model_chain == DEFAULT_MODEL_CHAIN

    def test_default_chain_is_not_shared_mutable_state(self, monkeypatch):
        monkeypatch.delenv("LLM_MODEL_CHAIN", raising=False)
        make_settings().llm_model_chain.append("mutated/model")
        assert "mutated/model" not in make_settings().llm_model_chain
        assert "mutated/model" not in DEFAULT_MODEL_CHAIN


class TestKeyAliases:
    """Semantics của Project A: số nhiều thắng số ít, số ít vẫn dùng được."""

    @pytest.mark.parametrize(
        ("plural", "singular", "field"),
        [
            ("GROQ_API_KEYS", "GROQ_API_KEY", "groq_api_keys"),
            ("GEMINI_API_KEYS", "GEMINI_API_KEY", "gemini_api_keys"),
            ("OPENROUTER_API_KEYS", "OPENROUTER_API_KEY", "openrouter_api_keys"),
        ],
    )
    def test_plural_wins_over_singular(self, monkeypatch, plural, singular, field):
        monkeypatch.setenv(singular, "single")
        monkeypatch.setenv(plural, "a,b")
        assert getattr(make_settings(), field) == ["a", "b"]

    @pytest.mark.parametrize(
        ("plural", "singular", "field"),
        [
            ("GROQ_API_KEYS", "GROQ_API_KEY", "groq_api_keys"),
            ("GEMINI_API_KEYS", "GEMINI_API_KEY", "gemini_api_keys"),
            ("OPENROUTER_API_KEYS", "OPENROUTER_API_KEY", "openrouter_api_keys"),
        ],
    )
    def test_singular_still_works(self, monkeypatch, plural, singular, field):
        monkeypatch.delenv(plural, raising=False)
        monkeypatch.setenv(singular, "only-one")
        assert getattr(make_settings(), field) == ["only-one"]

    def test_no_keys_configured_is_empty_not_error(self, monkeypatch):
        for var in ("GROQ_API_KEYS", "GROQ_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert make_settings().groq_api_keys == []


class TestSchemaIsolationDsn:
    """Hai schema sống chung một database — design doc §2.10.

    Checkpointer của LangGraph phải hạ cánh ở schema `langgraph`, không được
    lẫn vào schema nghiệp vụ `app`.
    """

    def test_app_and_checkpointer_use_different_search_paths(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5433/db")
        s = make_settings()
        assert s.app_dsn.endswith("?options=-csearch_path%3Dapp%2Cpublic")
        assert s.checkpointer_dsn.endswith("?options=-csearch_path%3Dlanggraph%2Cpublic")

    def test_public_is_always_on_the_path_for_pgvector(self, monkeypatch):
        # Extension `vector` sống ở public (scripts/init_db.sql). Bỏ public ra
        # khỏi search_path thì mọi cột kiểu vector thành "type does not exist".
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5433/db")
        s = make_settings()
        assert s.app_dsn.endswith("%2Cpublic")
        assert s.checkpointer_dsn.endswith("%2Cpublic")

    def test_sqlalchemy_url_names_psycopg3_explicitly(self, monkeypatch):
        # `postgresql://` trần = psycopg2 với SQLAlchemy, mà repo này cài
        # psycopg 3 → alembic chết với ModuleNotFoundError. Đã cắn một lần.
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5433/db")
        s = make_settings()
        assert s.app_sqlalchemy_url.startswith("postgresql+psycopg://")
        # DSN cho psycopg/PostgresSaver phải ở dạng libpq thuần, KHÔNG có marker.
        assert s.checkpointer_dsn.startswith("postgresql://")

    def test_existing_query_string_is_preserved(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db?sslmode=require")
        s = make_settings()
        assert (
            s.app_dsn
            == "postgresql://u:p@host/db?sslmode=require&options=-csearch_path%3Dapp%2Cpublic"
        )



class TestLlmMode:
    def test_default_is_live(self, monkeypatch):
        monkeypatch.delenv("LLM_MODE", raising=False)
        assert make_settings().llm_mode == "live"

    def test_replay_is_accepted(self, monkeypatch):
        monkeypatch.setenv("LLM_MODE", "replay")
        assert make_settings().llm_mode == "replay"

    def test_typo_is_rejected_loudly(self, monkeypatch):
        # "replya" phải nổ lúc khởi động, không được âm thầm gọi API thật
        # trong CI — đó là cả điểm của LLM_MODE=replay.
        monkeypatch.setenv("LLM_MODE", "replya")
        with pytest.raises(ValueError):
            make_settings()


class TestDsnActuallyConnects:
    """Hồi quy cho bug 2026-07-27: `options=-csearch_path=app,public` có hai dấu
    `=` trong một query param, psycopg từ chối parse. Test M0 cũ chỉ so chuỗi
    nên không bắt được — nó chỉ cắn khi psycopg nối TRỰC TIẾP, đúng lúc
    PostgresSaver chạy."""

    def test_app_dsn_connects_and_sets_search_path(self, requires_postgres, monkeypatch):
        import psycopg

        monkeypatch.setenv("DATABASE_URL", requires_postgres)
        with psycopg.connect(make_settings().app_dsn, connect_timeout=3) as conn:
            search_path = conn.execute("SHOW search_path").fetchone()[0]
        assert search_path == "app,public"

    def test_checkpointer_dsn_connects_and_sets_search_path(
        self, requires_postgres, monkeypatch
    ):
        import psycopg

        monkeypatch.setenv("DATABASE_URL", requires_postgres)
        with psycopg.connect(make_settings().checkpointer_dsn, connect_timeout=3) as conn:
            search_path = conn.execute("SHOW search_path").fetchone()[0]
        assert search_path == "langgraph,public"

