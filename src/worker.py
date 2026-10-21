import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.database import AsyncSessionLocal
from src.repositories.relational import TaskRepository
from src.logging_config import configure_logging
from src.managers.config_manager import config_manager
from src.workers.task_router import route_task

configure_logging(level=config_manager.get_app_config()["log_level"])
logger = logging.getLogger("coreai-worker")

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0

async def worker_loop():
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