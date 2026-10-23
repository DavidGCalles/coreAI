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
                    
                    # Ids capturados ANTES de ejecutar (hallazgo E2E 22 oct, #61):
                    # el handler es dueño del ciclo de vida de la sesión y su
                    # cortafuegos (rollback en fallos de ejecución) EXPIRA la
                    # instancia task. Tocar task.* después de un rollback dispara
                    # un refresh síncrono en contexto async (greenlet_spawn) que
                    # revienta el fail path y deja la tarea huérfana en RUNNING.
                    # Con el id capturado, el fallo de ejecución SIEMPRE aterriza
                    # en fail_task (que ya trabaja por UUID, sin tocar el ORM).
                    task_id = task.id
                    
                    try:
                        # Fase 2: Trabajo en memoria delegando al Enrutador (Registry)
                        await route_task(task, session)
                        
                        # Fase 3 (Éxito)
                        await repo.complete_task(task_id)
                        logger.info("Tarea %s COMPLETED", task_id)
                        
                    except Exception as execution_error:
                        # Fase 3 (Fallo controlado de negocio)
                        logger.error("Fallo ejecutando tarea %s: %s", task_id, execution_error)
                        # Trace HONESTO (#61): clase + mensaje de la excepción real.
                        # str(e) a secas perdía la clase (un httpx.ConnectError quedaba
                        # como «Connection refused» sin culpable, y una excepción sin
                        # mensaje como trace vacío). No se vuelca el traceback
                        # completo: el JSONB del payload ya viaja cargado con la
                        # data URI; el diagnóstico de una línea es el contrato de
                        # coreai_get_task.
                        await repo.fail_task(
                            task_id,
                            f"{type(execution_error).__name__}: {execution_error}",
                        )
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
