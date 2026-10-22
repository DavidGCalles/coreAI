import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.database import AsyncSessionLocal
from src.repositories.relational import TaskRepository
from src.logging_config import configure_logging
from src.managers.config_manager import config_manager
from src.workers.task_router import route_task
from src.db.vector_db import get_qdrant_client
from src.repositories.vector import (
    VectorRepository,
    IMAGE_COLLECTION,
    IMAGE_VECTOR_SIZE,
    IMAGE_VECTOR_DISTANCE,
)

configure_logging(level=config_manager.get_app_config()["log_level"])
logger = logging.getLogger("coreai-worker")

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0


async def ensure_collections() -> None:
    """
    Garantía idempotente de las colecciones derivadas del sistema (ADR-007-002, #50).

    La colección de imágenes se creó a mano en su día: nada garantizaba su
    existencia en un despliegue limpio, y el handler moría asíncrono contra
    una colección inexistente. El worker la garantiza ANTES de consumir la
    cola — sin ella, la asimilación de imágenes es imposible.
    """
    client = await get_qdrant_client()
    repo = VectorRepository(client=client)
    await repo.ensure_collection(
        collection_name=IMAGE_COLLECTION,
        vector_size=IMAGE_VECTOR_SIZE,
        distance=IMAGE_VECTOR_DISTANCE,
    )


async def worker_loop():
    # =================================================================
    # ARRANQUE: garantía de colecciones con backoff (ADR-007-002 #50).
    # Qdrant puede estar todavía levantándose (el worker no declara
    # depends_on sobre él): no morimos por orden de arranque — reintentamos
    # con el mismo backoff que el bucle. Pero NO consumimos la cola hasta
    # que las colecciones estén garantizadas.
    # =================================================================
    boot_backoff = INITIAL_BACKOFF
    while True:
        try:
            await ensure_collections()
            logger.info("Colecciones Qdrant garantizadas al arranque")
            break
        except Exception as init_error:
            logger.error(
                "No se pudo garantizar las colecciones Qdrant: %s — reintento en %.1fs",
                init_error, boot_backoff,
            )
            await asyncio.sleep(boot_backoff)
            boot_backoff = min(boot_backoff * 2, MAX_BACKOFF)

    logger.info("Córtex Asíncrono iniciado. A la escucha de tareas...")
    backoff = INITIAL_BACKOFF
    
    while True:
        try:
            # Una sesión limpia por cada intento del bucle
            async with AsyncSessionLocal() as session:
                repo = TaskRepository(session)
                
                # Fase 1: Reclamación (transacción súper corta, SKIP LOCKED)
                task = await repo.claim_next_task()
                
                if task:
                    # Hay trabajo: atacamos y reseteamos el backoff
                    backoff = INITIAL_BACKOFF
                    
                    try:
                        # Fase 2: Trabajo en memoria delegando al Enrutador (Registry)
                        await route_task(task, session)
                        
                        # Fase 3 (Éxito)
                        await repo.complete_task(task.id)
                        logger.info("Tarea %s COMPLETED", task.id)
                        
                    except Exception as execution_error:
                        # Fase 3 (Fallo controlado de negocio)
                        logger.error("Fallo ejecutando tarea %s: %s", task.id, execution_error)
                        await repo.fail_task(task.id, str(execution_error))
                else:
                    # Cola vacía: relajamos el polling progresivamente
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                    
        except Exception as db_error:
            # Fallo catastrófico de red/Postgres. Backoff sin detener el demonio.
            logger.error("Fallo de infraestructura en bucle de Worker: %s", db_error)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

if __name__ == "__main__":
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logger.info("Worker detenido manualmente por señal del sistema.")
