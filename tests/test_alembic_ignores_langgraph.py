"""tests/test_alembic_ignores_langgraph.py

`include_object` viết ở M0.8 nhưng tới giờ CHƯA TỪNG được kiểm, vì trước M1.5
không có bảng checkpoint nào tồn tại để nó lọc.

Test này mạnh nhất đúng lúc này: `langgraph` có 4 bảng thật, `app` gần như
trống. `include_object` sai thì autogenerate sinh 4 lệnh DROP TABLE — tức một
lệnh migration xoá sạch mọi HITL đang chờ duyệt.

Nó chạy `alembic revision --autogenerate` THẬT, tức là nạp `migrations/env.py`
và dùng đúng `include_object` của production. Bản đầu tiên tự viết lại logic
lọc trong test rồi gọi `compare_metadata`; đo 2026-07-27 cho thấy bản đó vô
dụng: thay thân `include_object` trong `env.py` bằng `return True` thì test vẫn
XANH, trong khi lệnh autogenerate thật sinh ra `drop_table('checkpoints')`,
`checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations` và cả
`alembic_version`. Một test không chạm vào thứ nó tuyên bố bảo vệ thì không bảo
vệ gì cả.
"""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from invoice_agent.checkpointer import build_checkpointer

LANGGRAPH_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_autogenerate_never_drops_langgraph_tables(requires_postgres, tmp_path):
    # Điều kiện tiên quyết: bảng checkpoint phải TỒN TẠI THẬT thì phép thử mới
    # có nghĩa. Không có chúng, test xanh một cách vô nghĩa.
    with build_checkpointer() as saver:
        saver.setup()

    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    # PHẢI có CẢ HAI thư mục, không chỉ tmp_path. Đo 2026-07-27: trỏ
    # `version_locations` vào riêng tmp_path thì Alembic không thấy revision
    # `4776f151f9e6` mà `app.alembic_version` đang ghi và chết với
    # `CommandError: Can't locate revision`. Ngăn cách bằng `os.pathsep` vì
    # alembic.ini đặt `path_separator = os`.
    cfg.set_main_option(
        "version_locations",
        f"{REPO_ROOT / 'migrations' / 'versions'}{os.pathsep}{versions_dir}",
    )

    # `version_path` để file sinh ra rơi vào tmp_path — test KHÔNG BAO GIỜ được
    # để rác lại trong `migrations/versions/`, một file migration lạc vào đó sẽ
    # làm hỏng `alembic upgrade head` của repo.
    command.revision(
        cfg, message="probe", autogenerate=True, version_path=str(versions_dir)
    )

    generated = list(versions_dir.glob("*.py"))
    assert len(generated) == 1, f"kỳ vọng 1 file migration, có {len(generated)}"
    body = generated[0].read_text(encoding="utf-8")

    assert "drop_table" not in body, (
        f"autogenerate đề nghị DROP — include_object hỏng.\n{body}"
    )
    for table in LANGGRAPH_TABLES:
        assert table not in body, f"autogenerate nhìn thấy bảng langgraph `{table}`"
    assert "langgraph" not in body
