"""
Bootstrap idempotente del esquema relacional (BUG-010 #87).

Gemelo del BUG-001 en el lado Postgres: el worker garantiza las colecciones
Qdrant al arranque (ensure_collections), pero nada creaba el esquema
relacional — un despliegue limpio moría en el primer coreai_dispatch_task
con UndefinedTableError. Los SQLEnum(..., create_type=False) de models.py
exigen que los 4 tipos enum existan ANTES del create_all, así que dentro de
una única transacción el orden es: enums primero, tablas después.

Idempotente por construcción: CREATE TYPE condicional por pg_type y
create_all con checkfirst (default). Los valores de cada enum se derivan
del enum Python correspondiente — models/schemas siguen siendo la única
fuente de verdad del esquema. scripts/db/ permanece vetado.
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from src.db.database import Base, engine
import src.db.models  # noqa: F401 — registra las tablas en Base.metadata
from src.db.models import SessionStatus, TaskStatus
from src.schemas.memory import DomainType, Visibility

logger = logging.getLogger(__name__)

# (nombre del tipo en Postgres, enum Python que aporta los valores)
ENUM_TYPES = [
    ("session_status", SessionStatus),
    ("task_status", TaskStatus),
    ("memory_domain_type", DomainType),
    ("memory_visibility", Visibility),
]


async def ensure_enums(conn: AsyncConnection) -> None:
    """Crea los 4 tipos enum de Postgres si no existen (condicional por pg_type)."""
    for type_name, enum_cls in ENUM_TYPES:
        values = ", ".join(f"'{member.value}'" for member in enum_cls)
        await conn.execute(
            text(
                "DO $$ BEGIN "
                f"IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{type_name}') THEN "
                f"CREATE TYPE {type_name} AS ENUM ({values}); "
                "END IF; END $$;"
            )
        )
        logger.info("Enum Postgres '%s' garantizado", type_name)


async def ensure_relational_schema() -> None:
    """
    Garantiza enums + tablas del esquema relacional, idempotente.

    Una única transacción: enums primero (models.py declara create_type=False
    en sus SQLEnum), create_all después (checkfirst: no toca nada existente).
    """
    async with engine.begin() as conn:
        await ensure_enums(conn)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Esquema relacional garantizado: 4 enums + tablas (idempotente)")
