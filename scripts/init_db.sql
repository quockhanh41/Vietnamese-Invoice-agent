-- Chạy MỘT LẦN lúc container khởi tạo volume lần đầu
-- (docker-entrypoint-initdb.d). Xoá volume thì chạy lại.
--
-- Hai schema sống chung một database — design doc §2.10:
--
--   app       — system of record. Alembic sở hữu.
--   langgraph — checkpoints/checkpoint_blobs/checkpoint_writes/
--               checkpoint_migrations. `PostgresSaver.setup()` sở hữu.
--
-- ⚠️ Vì sao phải tách: `alembic revision --autogenerate` nhìn thấy bảng của
-- LangGraph và sinh ra `DROP TABLE checkpoints` — tức là một lệnh migration
-- vô tình xoá sạch mọi HITL đang chờ duyệt. Tách schema là điều kiện CẦN;
-- điều kiện ĐỦ là `include_object` trong migrations/env.py loại schema
-- langgraph, và `version_table_schema='app'` (làm ở M1.6, có gate riêng).

CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS langgraph;

-- pgvector đặt ở `public` chứ không ở `app`: nó là kiểu dữ liệu dùng chung,
-- và cả hai connection (search_path=app,public / langgraph,public) đều phải
-- resolve được kiểu `vector`. Đặt trong `app` thì connection checkpointer
-- không thấy.
CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;
