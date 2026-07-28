def test_alembic_autogenerate_produces_no_schema_ops():
    """Spec §5.2 — alembic must ignore non-`app` tables (like LangGraph
    checkpoints, which M5 will create in the public schema). If autogenerate
    detects changes here, env.py's `include_object` filter is leaking.
    """
    import pathlib

    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    script = command.revision(alembic_cfg, message="probe_test", autogenerate=True)
    assert script is not None

    script_path = pathlib.Path(script.path)
    try:
        content = script_path.read_text(encoding="utf-8")
        assert "op.create_table" not in content, f"autogenerate created tables: {content}"
        assert "op.drop_table" not in content, f"autogenerate dropped tables: {content}"
    finally:
        if script_path.exists():
            script_path.unlink()
