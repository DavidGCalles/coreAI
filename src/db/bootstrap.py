"""
Bootstrap idempotente del esquema relacional (BUG-010 #87; ampliado por ADR-011 #99/#100 y 4.4-001 #103).

Gemelo del BUG-001 en el lado Postgres: el arranque garantiza el esquema
relacional completo sin pasos manuales. ADR-011 fija la regla: el esquema
evoluciona SOLO de forma aditiva y los modelos ORM son la única fuente de
verdad — no hay migraciones (Alembic queda fuera del repo).

Pasos, todos idempotentes:
1. Enums: CREATE TYPE condicional por pg_type (si el tipo ya existe no se toca
   aquí; sus valores nuevos se añaden en el paso 3).
2. create_all(checkfirst): crea tablas nuevas, no altera las existentes.
3. Valores de enum nuevos: ALTER TYPE ... ADD VALUE IF NOT EXISTS, uno por valor
   con commit intermedio (Postgres no permite usar el valor nuevo dentro de
   la misma transacción).
4. Columnas nuevas sobre tablas existentes: ALTER TABLE ... ADD COLUMN IF NOT
   EXISTS, derivado de information_schema vs Base.metadata. Incluye el DEFAULT
   de las columnas con server_default (4.4-001 #103): el tipo a secas dejaría
   la columna NULL en las filas existentes; con DEFAULT, Postgres backfillea
   sin reescribir la tabla (>= 11). Nunca DROP ni cambio de tipo (regla aditiva
   ADR-011).
5. Índices nuevos sobre tablas existentes: CREATE INDEX IF NOT EXISTS, derivado
   de pg_indexes vs Base.metadata (4.4-001 #103). Es el create_all de los
   índices para despliegues con historia: sin este paso, un índice declarado
   en models.py solo llegaría a los despliegues limpios (lección BUG-001/BUG-010:
   la garantía vive en el arranque, no en la memoria de quién despliega).

Los SQLEnum(..., create_type=False) de models.py exigen que los tipos enum
existan ANTES del create_all, así que enums y create_all van en una única
transacción; los ALTER TYPE van después, con su propio commit. Columnas e
índices cierran juntos en una transacción propia con commit explícito.
scripts/db/ permanece vetado.
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateIndex

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


async def ensure_enum_values(conn: AsyncConnection) -> None:
    """
    Añade a los enums YA existentes los valores nuevos que haya ganado el enum Python.

    ADR-011 (regla aditiva): ALTER TYPE ... ADD VALUE IF NOT EXISTS, uno por valor.
    Postgres no permite usar el valor nuevo dentro de la misma transacción, así
    que cada ALTER confirma con commit intermedio.
    """
    for type_name, enum_cls in ENUM_TYPES:
        for member in enum_cls:
            await conn.execute(
                text(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{member.value}'")
            )
            await conn.commit()
            logger.info("Valor de enum garantizado: %s.%s", type_name, member.value)


def _server_default_sql(column, dialect) -> str | None:
    """
    Compila el server_default de una columna a SQL de DEFAULT, o None si no lleva.

    Literal de cadena -> citado; expresión SQL (func.now(), text("0")) ->
    compilada con el dialecto de la conexión.
    """
    if column.server_default is None:
        return None
    arg = column.server_default.arg
    if isinstance(arg, str):
        escaped = arg.replace("'", "''")
        return f"'{escaped}'"
    return str(arg.compile(dialect=dialect))


async def ensure_columns(conn: AsyncConnection) -> None:
    """
    ALTER TABLE ... ADD COLUMN IF NOT EXISTS para las columnas nuevas del metadata.

    create_all(checkfirst) no altera tablas existentes: esta pasada compara
    information_schema con Base.metadata y emite el ALTER solo para las columnas
    que faltan, con el tipo compilado del dialecto de la conexión y el DEFAULT
    cuando la columna declara server_default (4.4-001 #103) — el DEFAULT es el
    que backfillea el valor en las filas existentes: el tipo a secas las deja
    NULL. Postgres >= 11 lo hace sin reescribir la tabla.

    Solo aditivo (ADR-011): nunca DROP ni cambio de tipo. Ojo: una columna NOT
    NULL sin default sobre una tabla con datos existentes falla en Postgres; la
    regla aditiva exige que toda columna nueva sea nullable o lleve default.
    """
    for table in Base.metadata.sorted_tables:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table.name},
        )
        existing = {row[0] for row in result}
        for column in table.columns:
            if column.name in existing:
                continue
            col_type = column.type.compile(dialect=conn.dialect)
            default_sql = _server_default_sql(column, conn.dialect)
            alter = (
                f"ALTER TABLE {table.name} "
                f"ADD COLUMN IF NOT EXISTS {column.name} {col_type}"
            )
            if default_sql is not None:
                alter += f" DEFAULT {default_sql}"
            await conn.execute(text(alter))
            logger.info(
                "Columna garantizada: %s.%s (%s%s)",
                table.name, column.name, col_type,
                f" DEFAULT {default_sql}" if default_sql else "",
            )


async def ensure_indexes(conn: AsyncConnection) -> None:
    """
    CREATE INDEX IF NOT EXISTS para los índices nuevos del metadata.

    create_all(checkfirst) no altera tablas existentes y ensure_columns solo
    hace columnas: sin esta pasada, un índice declarado en models.py jamás
    llegaría a un despliegue con historia (lección BUG-001/BUG-010). El DDL se
    compila desde el metadata con CreateIndex(if_not_exists=True): misma
    fuente de verdad y mismo compilador que create_all, incluido el WHERE de
    los índices parciales.

    Solo aditivo (ADR-011): nunca DROP INDEX ni redefición — si ya existe un
    índice con ese nombre, se respeta tal cual.
    """
    for table in Base.metadata.sorted_tables:
        result = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = :t"
            ),
            {"t": table.name},
        )
        existing = {row[0] for row in result}
        for index in table.indexes:
            if index.name in existing:
                continue
            ddl = str(CreateIndex(index, if_not_exists=True).compile(dialect=conn.dialect))
            await conn.execute(text(ddl))
            logger.info("Índice garantizado: %s", index.name)


async def ensure_relational_schema() -> None:
    """
    Garantiza enums + tablas + valores de enum nuevos + columnas nuevas +
    índices nuevos. Idempotente.

    Orden (ADR-011 #100; índices de 4.4-001 #103):
    1. enums (CREATE TYPE condicional) + create_all en una única transacción —
       models.py declara create_type=False y los tipos deben existir antes;
    2. ALTER TYPE ... ADD VALUE IF NOT EXISTS con commit intermedio por valor;
    3. ALTER TABLE ... ADD COLUMN IF NOT EXISTS derivado de information_schema
       y CREATE INDEX IF NOT EXISTS derivado de pg_indexes, en la misma tx,
       cerrada con commit explícito.
    """
    async with engine.begin() as conn:
        await ensure_enums(conn)
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        await ensure_enum_values(conn)
        await ensure_columns(conn)
        await ensure_indexes(conn)
        await conn.commit()
    logger.info(
        "Esquema relacional garantizado: enums + tablas + valores + columnas + índices "
        "(idempotente, ADR-011)"
    )
