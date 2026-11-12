"""
Tests del bootstrap idempotente del esquema relacional (BUG-010 #87; ampliado por ADR-011 #99/#100).

ADR-011: el esquema evoluciona solo de forma aditiva y los modelos ORM son la
única fuente de verdad (Alembic fuera del repo). El bootstrap garantiza:
enums (CREATE TYPE condicional) -> create_all -> ALTER TYPE ADD VALUE IF NOT
EXISTS (commit intermedio por valor) -> ALTER TABLE ADD COLUMN IF NOT EXISTS
(derivado de information_schema).

- Unitarios: engine mockeado, cero red — se verifica el SQL emitido y el orden.
- Integración: contra coreai_test_db (el cortafuegos de conftest.py fija la
  URL) se ejecuta de verdad dos veces y se verifica pg_type + pg_tables + pg_enum.
"""
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from src.db.bootstrap import (
    ENUM_TYPES,
    ensure_columns,
    ensure_enum_values,
    ensure_enums,
    ensure_relational_schema,
)
from src.db.database import Base
from src.db.models import SessionStatus, TaskStatus
from src.schemas.memory import DomainType, Visibility

EXPECTED_TABLES = ["entities", "sessions", "messages", "tasks", "events", "llm_audit"]
TOTAL_ENUM_VALUES = sum(len(enum_cls) for _, enum_cls in ENUM_TYPES)


def _all_columns_by_table() -> dict:
    return {t.name: {c.name for c in t.columns} for t in Base.metadata.sorted_tables}


def _mock_conn(order: list, existing_columns: dict | None = None) -> MagicMock:
    """Conn mockeada: information_schema reporta las columnas de existing_columns."""
    conn = MagicMock()
    conn.dialect = postgresql.dialect()

    async def _execute(stmt, params=None, **kw):
        sql = str(stmt)
        if "information_schema.columns" in sql:
            order.append("col_query")
            cols = (existing_columns or {}).get(params["t"], set())
            return [(c,) for c in cols]
        if sql.startswith("ALTER TYPE"):
            order.append("alter_value")
            return []
        if sql.startswith("ALTER TABLE"):
            order.append("alter_column")
            return []
        order.append("enum")
        return []

    conn.execute = AsyncMock(side_effect=_execute)
    conn.run_sync = AsyncMock(side_effect=lambda *a, **k: order.append("create_all"))
    conn.commit = AsyncMock(side_effect=lambda *a, **k: order.append("commit"))
    return conn


def _ctx(conn: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _patch_engine(monkeypatch, conn_begin: MagicMock, conn_alter: MagicMock) -> None:
    eng = MagicMock()
    eng.begin = MagicMock(return_value=_ctx(conn_begin))
    eng.connect = MagicMock(return_value=_ctx(conn_alter))
    monkeypatch.setattr("src.db.bootstrap.engine", eng)


def test_los_enums_del_bootstrap_coinciden_con_los_de_los_modelos():
    """Guardia de drift: el bootstrap deriva los valores de los enums Python."""
    assert [m.value for m in SessionStatus] == ["ACTIVE", "CLOSED"]
    assert [m.value for m in TaskStatus] == ["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    assert [m.value for m in DomainType] == ["document", "conversation", "system"]
    assert [m.value for m in Visibility] == ["private", "shared", "public"]
    assert [name for name, _ in ENUM_TYPES] == [
        "session_status", "task_status", "memory_domain_type", "memory_visibility",
    ]


async def test_ensure_enums_emite_los_4_create_type_condicionales():
    conn = _mock_conn([])
    await ensure_enums(conn)

    assert conn.execute.await_count == 4
    emitted = [c.args[0].text for c in conn.execute.await_args_list]
    for (type_name, enum_cls), sql in zip(ENUM_TYPES, emitted):
        assert "IF NOT EXISTS" in sql
        assert f"typname = '{type_name}'" in sql
        assert f"CREATE TYPE {type_name} AS ENUM" in sql
        values = ", ".join(f"'{m.value}'" for m in enum_cls)
        assert values in sql


async def test_ensure_enum_values_emite_add_value_if_not_exists_con_commit_intermedio():
    """ADR-011: cada valor nuevo es ALTER TYPE ADD VALUE IF NOT EXISTS + commit intermedio."""
    order: list = []
    conn = _mock_conn(order)

    await ensure_enum_values(conn)

    assert order == ["alter_value", "commit"] * TOTAL_ENUM_VALUES
    emitted = [c.args[0].text for c in conn.execute.await_args_list]
    for sql in emitted:
        assert "ALTER TYPE" in sql
        assert "ADD VALUE IF NOT EXISTS" in sql
    expected_values = {m.value for _, enum_cls in ENUM_TYPES for m in enum_cls}
    emitted_values = {sql.rsplit("'", 2)[-2] for sql in emitted}
    assert emitted_values == expected_values


async def test_ensure_columns_emite_alter_solo_para_las_columnas_que_faltan():
    """Solo las columnas ausentes en information_schema producen ALTER TABLE."""
    order: list = []
    existing = _all_columns_by_table()
    existing["tasks"].discard("updated_at")  # simulamos la columna nueva del ADR-011/#30
    conn = _mock_conn(order, existing_columns=existing)

    await ensure_columns(conn)

    alters = [c.args[0].text for c in conn.execute.await_args_list if c.args[0].text.startswith("ALTER TABLE")]
    assert len(alters) == 1
    assert "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS updated_at" in alters[0]
    assert order.count("col_query") == len(Base.metadata.sorted_tables)


async def test_ensure_columns_esquema_completo_no_emite_ningun_alter():
    """Idempotencia: con el esquema completo en information_schema no se emite ALTER."""
    order: list = []
    conn = _mock_conn(order, existing_columns=_all_columns_by_table())

    await ensure_columns(conn)

    alters = [c for c in conn.execute.await_args_list if c.args[0].text.startswith("ALTER TABLE")]
    assert alters == []
    assert order.count("col_query") == len(Base.metadata.sorted_tables)


async def test_ensure_relational_schema_orden_completo(monkeypatch):
    """enums -> create_all (misma tx) -> alter_value+commit por valor -> col_query por tabla."""
    order: list = []
    conn_begin = _mock_conn(order)
    conn_alter = _mock_conn(order, existing_columns=_all_columns_by_table())
    _patch_engine(monkeypatch, conn_begin, conn_alter)

    await ensure_relational_schema()

    expected = (
        ["enum"] * 4
        + ["create_all"]
        + ["alter_value", "commit"] * TOTAL_ENUM_VALUES
        + ["col_query"] * len(Base.metadata.sorted_tables)
    )
    assert order == expected
    # create_all va con checkfirst (default) sobre el metadata de los modelos
    assert conn_begin.run_sync.await_args.args[0] == Base.metadata.create_all


async def test_ensure_relational_schema_segunda_corrida_no_cambia_el_contrato(monkeypatch):
    order: list = []
    conn_begin = _mock_conn(order)
    conn_alter = _mock_conn(order, existing_columns=_all_columns_by_table())
    _patch_engine(monkeypatch, conn_begin, conn_alter)

    await ensure_relational_schema()
    await ensure_relational_schema()

    one_run = (
        ["enum"] * 4
        + ["create_all"]
        + ["alter_value", "commit"] * TOTAL_ENUM_VALUES
        + ["col_query"] * len(Base.metadata.sorted_tables)
    )
    assert order == one_run * 2


async def test_integracion_contra_test_db_dos_veces_es_idempotente():
    """
    Integración real contra coreai_test_db (conftest fija POSTGRES_URL y
    aborta si no apunta ahí): garantiza el esquema, lo re-garantiza, y
    verifica los 4 enums en pg_type, sus valores completos en pg_enum y las
    6 tablas en pg_tables.
    """
    from src.db.database import engine

    await ensure_relational_schema()
    await ensure_relational_schema()

    async with engine.connect() as conn:
        types = (
            await conn.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname IN "
                    "('session_status', 'task_status', 'memory_domain_type', 'memory_visibility')"
                )
            )
        ).scalars().all()
        tables = (
            await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        ).scalars().all()
        enum_rows = (
            await conn.execute(
                text(
                    "SELECT t.typname, e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname IN ('session_status', 'task_status', "
                    "'memory_domain_type', 'memory_visibility')"
                )
            )
        ).all()

    assert set(types) == {"session_status", "task_status", "memory_domain_type", "memory_visibility"}
    for table in EXPECTED_TABLES:
        assert table in tables
    assert len(enum_rows) == TOTAL_ENUM_VALUES
