"""Một nơi duy nhất đọc environment.

Nợ kỹ thuật cần tránh lặp lại từ Project A: `os.getenv` rải rác khắp nơi và
`_get_model_chain` bị copy ở 2 file. Mọi thứ đọc env đi qua đây.

⚠️ `NoDecode` — cái bẫy đã được cảnh báo trước ở roadmap M0.2:
pydantic-settings coi `list[str]` là "complex type" và chạy `json.loads()` lên
giá trị env **trước khi** validator `mode="before"` được gọi. Nghĩa là
`LLM_MODEL_CHAIN=groq/a,gemini/b` sẽ raise `SettingsError` chứ không bao giờ
tới được validator tách dấu phẩy. `Annotated[list[str], NoDecode]` tắt bước
decode đó, để chuỗi thô rơi đúng vào `_split_csv`. Xem `tests/test_config.py`
— có test khẳng định đúng hành vi này, đừng gỡ annotation.
"""

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Chain mặc định kế thừa từ Project A `src/llm/gateway.py` (đã live-verify
# 2026-07-19 ở project đó). Groq trước vì free tier rộng nhất, Gemini flash
# sau vì là quota ngoài lớn nhất, OpenRouter :free là đuôi vét chứ không phải
# ngựa kéo (~50 req/ngày/key, tính cả account chứ không phải cả model).
DEFAULT_MODEL_CHAIN = [
    "groq/llama-3.3-70b-versatile",
    "groq/openai/gpt-oss-120b",
    "gemini/gemini-3.5-flash",
    "openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-4o-mini",
]

# Hai schema sống chung một database — xem design doc §2.10. `langgraph` do
# `PostgresSaver.setup()` sở hữu, Alembic phải bị cấm nhìn vào đó.
APP_SCHEMA = "app"
LANGGRAPH_SCHEMA = "langgraph"

_CsvList = Annotated[list[str], NoDecode]


def _split_csv(value: object) -> object:
    """`"a, b ,"` -> `["a", "b"]`. Giá trị không phải str thì trả nguyên."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database -----------------------------------------------------------
    # Mặc định khớp docker-compose.yml. Deploy thì trỏ sang Neon.
    database_url: str = "postgresql://invoice:invoice@localhost:5433/invoice_agent"

    # Runtime role. NOT the owner: measured 2026-07-28 (spec §2.1), `invoice`
    # is a SUPERUSER, so REVOKE against it lands in the ACL and is then
    # bypassed. Repositories connect as this role; Alembic and the seed script
    # keep using `database_url` because both are administrative.
    runtime_database_url: str = "postgresql://invoice_app:invoice_app@localhost:5433/invoice_agent"

    llm_model_chain: _CsvList = Field(default_factory=lambda: list(DEFAULT_MODEL_CHAIN))
    # Vision needs its own chain. Measured 2026-07-29 (spec M3-B §2.1):
    # litellm.supports_vision is False for BOTH groq models and BOTH openrouter
    # models, and groq is first in DEFAULT_MODEL_CHAIN — so a vision call on the
    # text chain fails twice before it reaches a model that can see, burning two
    # llm_calls rows per invoice on a failure knowable in advance.
    vision_model_chain: _CsvList = Field(
        default_factory=lambda: ["gemini/gemini-3.5-flash", "openai/gpt-4o-mini"]
    )

    # Mỗi key thuộc một account riêng, nên rotation nhân thật sự capacity
    # (đã xác nhận ở Project A 2026-07-19, không phải giả định).
    #
    # `AliasChoices(plural, singular)` giữ đúng semantics của Project A: biến
    # số nhiều thắng số ít, và vì `_split_csv` tách dấu phẩy nên một key đơn ở
    # `GROQ_API_KEY` tự thành list 1 phần tử. Nhờ vậy file `.env` sẵn có của
    # Project A dùng lại được nguyên xi, không phải sửa.
    groq_api_keys: _CsvList = Field(
        default_factory=list,
        validation_alias=AliasChoices("groq_api_keys", "groq_api_key"),
    )
    gemini_api_keys: _CsvList = Field(
        default_factory=list,
        validation_alias=AliasChoices("gemini_api_keys", "gemini_api_key"),
    )
    openrouter_api_keys: _CsvList = Field(
        default_factory=list,
        validation_alias=AliasChoices("openrouter_api_keys", "openrouter_api_key"),
    )
    openai_api_key: str = ""

    # replay = đọc fixture, không chạm mạng. CI và demo trực tiếp dùng cái này
    # để không bao giờ hỏng vì rate limit của người khác (design doc §11.4).
    llm_mode: Literal["live", "record", "replay"] = "live"
    llm_cache_dir: str = "tests/fixtures/llm_cache"

    # --- Batch --------------------------------------------------------------
    # Kiểm soát CHÍNH chống 429 khi fan-out; key rotation chỉ là lưới đỡ.
    max_concurrency: int = 4

    # --- Observability ------------------------------------------------------
    # observability.py đọc thẳng os.environ (copy nguyên vẹn từ Project A, cố
    # ý không sửa), nên các field này là để validate + tài liệu hoá, không
    # phải nguồn đọc duy nhất.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_tracing_enabled: bool = True

    @field_validator(
        "llm_model_chain",
        "vision_model_chain",
        "groq_api_keys",
        "gemini_api_keys",
        "openrouter_api_keys",
        mode="before",
    )
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @property
    def app_dsn(self) -> str:
        """Connection nghiệp vụ — schema `app` trước, `public` sau."""
        return self._with_options(f"-csearch_path={APP_SCHEMA},public")

    @property
    def checkpointer_dsn(self) -> str:
        """Connection của PostgresSaver — schema `langgraph` trước.

        Tách riêng để `checkpoints`/`checkpoint_blobs`/`checkpoint_writes` không
        rơi vào schema nghiệp vụ (design doc §2.10).

        `,public` ở đuôi cả hai DSN là bắt buộc, không phải cho chắc: extension
        `vector` nằm ở `public` (scripts/init_db.sql), bỏ nó ra thì mọi cột
        kiểu `vector` thành "type does not exist". Schema đầu tiên trong
        search_path là nơi `CREATE TABLE` không chỉ định schema hạ cánh — nên
        thứ tự này cũng chính là thứ giữ bảng của LangGraph đúng chỗ.
        """
        return self._with_options(f"-csearch_path={LANGGRAPH_SCHEMA},public")

    @property
    def app_sqlalchemy_url(self) -> str:
        """Cùng một database, nhưng ở dạng SQLAlchemy hiểu được.

        Hai thư viện muốn hai định dạng khác nhau từ cùng một DSN:
        - `psycopg` / `PostgresSaver` muốn DSN libpq thuần `postgresql://…`
        - SQLAlchemy đọc scheme để chọn driver, và `postgresql://` trần nghĩa
          là **psycopg2** (không cài) → `ModuleNotFoundError` lúc chạy Alembic.
          Phải nói rõ `postgresql+psycopg://` mới ra psycopg 3.

        Giữ `database_url` ở dạng libpq và convert tại đây, thay vì bắt người
        dùng viết `+psycopg` trong `.env` rồi phải gỡ ra cho checkpointer.
        """
        return self.app_dsn.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def alembic_sqlalchemy_url(self) -> str:
        """Alembic's connection. `app` must NOT come first in search_path.

        Measured 2026-07-28: with `-csearch_path=app,public`, Alembic treats
        `app` as the connection's DEFAULT schema and reflects its tables under
        the `None` key, while `db/models.py` declares them under `'app'`. Every
        foreign key then compares as removed-and-re-added — a probe
        `--autogenerate` against a database that exactly matches its migrations
        emitted **32 diffs (16 `remove_fk` + 16 `add_fk`)**:

            remove_fk  table: None.approvals -> referred None.invoices
            add_fk     table: app.approvals  -> referred app.invoices

        That is not cosmetic. The generated `op.drop_constraint` calls omit
        `schema='app'`, so running them unedited fails; and real drift hides in
        30 lines of noise, which inverts the purpose of the M1.6 isolation
        guard. With `-csearch_path=public` the probe migration is empty.

        Safe because every DDL op in `migrations/versions/` names `schema="app"`
        explicitly (verified 2026-07-28: 16/16 `create_table` calls), and
        `version_table_schema="app"` in `migrations/env.py` places
        `alembic_version`. `public` stays in the path so the `vector` extension
        type still resolves when M7 adds `episodic_decisions.reason_embedding`.
        """
        return self._with_options("-csearch_path=public", base=self.database_url).replace(
            "postgresql://", "postgresql+psycopg://", 1
        )

    @property
    def runtime_dsn(self) -> str:
        """Business connection with least privilege — see `app_dsn` for the options rules."""
        return self._with_options(
            f"-csearch_path={APP_SCHEMA},public", base=self.runtime_database_url
        )

    @property
    def runtime_sqlalchemy_url(self) -> str:
        return self.runtime_dsn.replace("postgresql://", "postgresql+psycopg://", 1)

    def _with_options(self, options: str, base: str | None = None) -> str:
        # `quote(..., safe="")` là bắt buộc, không phải cho chắc: giá trị options
        # chứa dấu `=` (`-csearch_path=app,public`), và psycopg coi dấu `=` thứ
        # hai trong một query param là lỗi cú pháp — `ProgrammingError: extra
        # key/value separator "="`. Đã đo 2026-07-27: bản chưa encode nối được
        # qua SQLAlchemy (nó tự dựng lại conninfo) nhưng psycopg trực tiếp thì
        # không, tức là PostgresSaver chết còn Alembic vẫn chạy — kiểu bug chỉ
        # lộ ra ở đúng chỗ tệ nhất.
        url = self.database_url if base is None else base
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}options={quote(options, safe='')}"


@lru_cache
def get_settings() -> Settings:
    """Instance dùng chung. `get_settings.cache_clear()` để reset trong test."""
    return Settings()
