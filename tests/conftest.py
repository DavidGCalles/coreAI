import asyncio
import os

import asyncpg
import pytest
from sqlalchemy.engine import make_url

# Secuestramos la variable de entorno ANTES de que ningún módulo la lea.
# Así obligamos al ConfigManager a apuntar siempre a la base de datos de pruebas.
os.environ["POSTGRES_URL"] = "postgresql+asyncpg://coreai_user:postgres_secure_password@localhost:5432/coreai_test_db"


@pytest.fixture(scope="session", autouse=True)
def ensure_test_database():
    """
    Autonomous test bench [David, 25 Nov, request after the pytest run]: if
    coreai_test_db does not exist on the host Postgres, it gets created —
    idempotent, the same autonomy guarantee of the bootstrap (ADR-011 /
    ADR-012-001) applied to the tests. Without this fixture, a VM with a
    volume wiped by compose left the entire integration suite red with
    InvalidCatalogNameError.

    Direct connection with asyncpg to the maintenance database: CREATE
    DATABASE cannot go within a transaction. Check first in pg_database
    and DuplicateDatabaseError as a shield against the race (single dev
    dispatcher, nothing to protect): N pytest runs leave the rig identical.
    """
    url = make_url(os.environ["POSTGRES_URL"]).set(database="postgres")
    dsn = f"postgresql://{url.username}:{url.password}@{url.host}:{url.port}/postgres"

    async def _ensure() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", "coreai_test_db"
            )
            if exists is None:
                await conn.execute('CREATE DATABASE "coreai_test_db"')
        except asyncpg.exceptions.DuplicateDatabaseError:
            pass  # escudo de la carrera: otra ejecución la creó primero
        finally:
            await conn.close()

        # Esquema al día: la misma autonomía del bootstrap aplicada al rig
        # [ADR-012-002 #113]. La suite de frontera despacha con columnas nuevas
        # (tasks.entity_id, entities.slug) y depender de que algún test
        # anterior haya corrido el bootstrap contra la BD de pruebas es
        # exactamente la trampa de autonomía que este fixture ya mató con el
        # catálogo: una pasada idempotente de ensure_relational_schema por
        # sesión y el rig queda SIEMPRE al esquema de la rama (ADR-011).
        from src.db.bootstrap import ensure_relational_schema
        from src.db.database import engine

        await ensure_relational_schema()
        # El engine vive en el loop de este asyncio.run: el pool se disuelve
        # AQUÍ para que ninguna conexión atraviese loops (misma lección que
        # el fixture per-test dispose_engine_pool).
        await engine.dispose()

    asyncio.run(_ensure())


@pytest.fixture(autouse=True)
def protect_dev_database():
    """
    Cortafuegos paranoico: Si por algún motivo la URL no apunta a la DB de test, 
    aborta la ejecución inmediatamente para proteger los datos de desarrollo.
    """
    from src.managers.config_manager import config_manager
    url = config_manager.get_postgres_url()
    
    if "coreai_test_db" not in url:
        pytest.exit(f"¡ALERTA! Pytest intentó conectarse a la BD de desarrollo: {url}")


@pytest.fixture(autouse=True)
async def dispose_engine_pool():
    """
    pytest-asyncio crea un event loop nuevo por test, pero el pool del engine
    asyncpg retiene conexiones creadas en el loop del test anterior: el
    siguiente test que reutilice una conexión del pool revienta con
    "Future attached to a different loop". Dispose tras cada test para que
    cada uno arranque con el pool limpio.
    """
    yield
    from src.db.database import engine

    await engine.dispose()
