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
6. Entidades declarativas (ADR-012-001 #112): carga del YAML del despliegue
   (volumen de datos, jamás env — D2 del ADR-012) y INSERT ... ON CONFLICT
   (slug) DO NOTHING por entidad + system_default implícito. Solo aditivo:
   la tabla jamás se recorta (quitar una entidad del YAML no la borra). El
   YAML se valida ANTES de tocar la BD: malformado = fallo DETERMINISTA de
   arranque (EntitiesProvisionError, sin backoff); ausente o vacío =
   arranque normal con solo system_default.

Los SQLEnum(..., create_type=False) de models.py exigen que los tipos enum
existan ANTES del create_all, así que enums y create_all van en una única
transacción; los ALTER TYPE van después, con su propio commit. Columnas e
índices cierran juntos en una transacción propia con commit explícito.
scripts/db/ permanece vetado.
"""
import logging

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateIndex

from src.db.database import Base, engine
import src.db.models  # noqa: F401 — registra las tablas en Base.metadata
from src.db.entities_config import (
    DECLARED_ROLE,
    SYSTEM_DEFAULT_METADATA,
    SYSTEM_DEFAULT_ROLE,
    SYSTEM_DEFAULT_SLUG,
    EntityDeclaration,
    load_entity_declarations,
)
from src.db.models import SessionStatus, TaskStatus
from src.managers.config_manager import config_manager
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


async def ensure_entities(conn: AsyncConnection, declarations: list[EntityDeclaration]) -> None:
    """
    INSERT IF NOT EXISTS por entidad declarada + system_default implícito.

    [ADR-012-001] #112: las entidades se gobiernan en el despliegue y el
    bootstrap las garantiza — patrón ensure_* del ADR-011 aplicado a datos:
    INSERT ... ON CONFLICT (slug) DO NOTHING, jamás UPDATE ni DELETE de
    entidades declaradas (la tabla nunca se recorta: quitar una entidad del
    YAML la deja viva en la BD; es gobernanza, no limpieza).

    Reclamo dirigido de la fila histórica (excepción aprobada por David,
    25 nov): los despliegues con historia ya tienen un system_default
    auto-vivificado con role='system_default' y sin slug. Sin el reclamo,
    el INSERT crearía una segunda identidad system_default y la frontera
    del [ADR-012-002] tendría que tratar la fila legacy como alias para
    siempre. Esta única UPDATE es idempotente y circunscrita: solo toca
    filas role='system_default' con slug NULL y solo si ninguna fila ha
    reclamado aún el slug.
    """
    await conn.execute(
        text(
            "UPDATE entities SET slug = :slug "
            "WHERE role = :role AND slug IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM entities WHERE slug = :slug)"
        ),
        {"slug": SYSTEM_DEFAULT_SLUG, "role": SYSTEM_DEFAULT_ROLE},
    )
    # system_default implícito primero (lo declare o no el YAML), declaradas
    # en el orden del fichero para logs deterministas.
    rows = [
        {
            "slug": SYSTEM_DEFAULT_SLUG,
            "role": SYSTEM_DEFAULT_ROLE,
            "label": None,
            "metadata_payload": dict(SYSTEM_DEFAULT_METADATA),
        },
    ]
    rows.extend(
        {"slug": d.id, "role": DECLARED_ROLE, "label": d.label, "metadata_payload": {}}
        for d in declarations
    )
    for row in rows:
        stmt = pg_insert(Base.metadata.tables["entities"]).values(**row)
        stmt = stmt.on_conflict_do_nothing(index_elements=["slug"])
        await conn.execute(stmt)
        logger.info("Entidad garantizada (si ausente): %s", row["slug"])


async def ensure_relational_schema() -> None:
    """
    Garantiza enums + tablas + valores de enum nuevos + columnas nuevas +
    índices nuevos + entidades declarativas. Idempotente.

    Orden (ADR-011 #100; índices de 4.4-001 #103; entidades de ADR-012-001 #112):
    0. validación del YAML de entidades — determinista ANTES de tocar la BD:
       un fichero malformado aborta el arranque sin haber emitido DDL alguno;
    1. enums (CREATE TYPE condicional) + create_all en una única transacción —
       models.py declara create_type=False y los tipos deben existir antes;
    2. ALTER TYPE ... ADD VALUE IF NOT EXISTS con commit intermedio por valor;
    3. ALTER TABLE ... ADD COLUMN IF NOT EXISTS derivado de information_schema
       y CREATE INDEX IF NOT EXISTS derivado de pg_indexes, en la misma tx,
       cerrada con commit explícito;
    4. INSERT ... ON CONFLICT (slug) DO NOTHING de las entidades del YAML +
       system_default implícito (con reclamo de la fila legacy), en tx propia:
       la tabla y su índice único ya existen cuando corre.
    """
    declarations = load_entity_declarations(config_manager.get_entities_file_path())
    async with engine.begin() as conn:
        await ensure_enums(conn)
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        await ensure_enum_values(conn)
        await ensure_columns(conn)
        await ensure_indexes(conn)
        await conn.commit()
    async with engine.begin() as conn:
        await ensure_entities(conn, declarations)
    logger.info(
        "Esquema relacional garantizado: enums + tablas + valores + columnas + índices "
        "+ %d entidades (%d declaradas en el YAML), idempotente (ADR-011 / ADR-012-001)",
        len(declarations) + 1,
        len(declarations),
    )
