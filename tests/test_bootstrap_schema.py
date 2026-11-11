"""
Tests del bootstrap idempotente del esquema relacional (BUG-010 #87).

Gemelo del BUG-001 en el lado Postgres: un despliegue limpio no creaba ni
los 4 enums ni las tablas (UndefinedTableError en el primer dispatch). El
arranque de core-mcp garantiza ambos, en orden (enums antes que create_all,
porque models.py declara create_type=False) y de forma idempotente.

- Unitarios: engine mockeado, cero red — se verifica el SQL emitido (CREATE
  TYPE condicional por pg_type con los valores exactos de cada enum) y el
  orden enums -> create_all dentro de la misma transacción.
- Integración: contra coreai_test_db (el cortafuegos de conftest.py fija la
  URL) se ejecuta de verdad dos veces y se verifica pg_type + pg_tables.
"""
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import text

from src.db.bootstrap import ENUM_TYPES, ensure_enums, ensure_relational_schema
from src.db.database import Base, engine
from src.db.models import SessionStatus, TaskStatus
from src.schemas.memory import DomainType, Visibility

EXPECTED_TABLES = ["entities", "sessions", "messages", "tasks", "events", "llm_audit"]


def _mock_conn(order: list) -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=lambda *a, **k: order.append("enum"))
    conn.run_sync = AsyncMock(side_effect=lambda *a, **k: order.append("create_all"))
    return conn


def _patch_engine(monkeypatch, conn: MagicMock) -> None:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    eng = MagicMock()
    eng.begin = MagicMock(return_value=ctx)
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


async def test_ensure_relational_schema_enums_antes_que_create_all(monkeypatch):
    order: list = []
    conn = _mock_conn(order)
    _patch_engine(monkeypatch, conn)

    await ensure_relational_schema()

    assert order == ["enum", "enum", "enum", "enum", "create_all"]
    # create_all va con checkfirst (default) sobre el metadata de los modelos
    assert conn.run_sync.await_args.args[0] == Base.metadata.create_all


async def test_ensure_relational_schema_segunda_corrida_no_cambia_el_contrato(monkeypatch):
    order: list = []
    conn = _mock_conn(order)
    _patch_engine(monkeypatch, conn)

    await ensure_relational_schema()
    await ensure_relational_schema()

    # cada corrida emite enum×4 → create_all; dos corridas = (enum×4 + create_all) × 2
    assert order == (["enum"] * 4 + ["create_all"]) * 2


async def test_integracion_contra_test_db_dos_veces_es_idempotente():
    """
    Integración real contra coreai_test_db (conftest fija POSTGRES_URL y
    aborta si no apunta ahí): garantiza el esquema, lo re-garantiza, y
    verifica los 4 enums en pg_type y las 6 tablas en pg_tables.
    """
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

    assert set(types) == {"session_status", "task_status", "memory_domain_type", "memory_visibility"}
    for table in EXPECTED_TABLES:
        assert table in tables
