import asyncio
import logging
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.database import AsyncSessionLocal
from src.db.bootstrap import ensure_relational_schema
from src.repositories.relational import TaskRepository
from src.logging_config import configure_logging, set_current_task_id, clear_current_task_id
from src.managers.config_manager import config_manager
from src.workers.task_router import route_task
from src.workers.failure_classification import es_fallo_determinista
from src.schemas.memory import DomainType
from src.db.vector_db import get_qdrant_client
from src.repositories.vector import (
    VectorRepository,
    IMAGE_COLLECTION,
    IMAGE_VECTOR_SIZE,
    IMAGE_VECTOR_DISTANCE,
    TEXT_VECTOR_SIZE,
    TEXT_VECTOR_DISTANCE,
)

configure_logging(level=config_manager.get_app_config()["log_level"])
logger = logging.getLogger("coreai-worker")

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0


async def ensure_collections() -> None:
    """
    Garantía idempotente de TODAS las colecciones derivadas del sistema
    (ADR-007-002 #50; BUG-001 #34).

    La colección de imágenes se creó a mano en su día: nada garantizaba su
    existencia en un despliegue limpio, y el handler moría asíncrono contra
    una colección inexistente. Las colecciones de TEXTO por dominio
    (document, conversation, system — multilingual-e5-small, 384 dims,
    coseno) tenían el mismo agujero hasta el BUG-001: un `docker-compose
    down -v` seguido de arranque limpio las auto-genera TODAS sin
    intervención manual, y el arranque es idempotente (comparación de
    nombres, nunca try/except). Si una colección preexistente trae dims
    de otro modelo, el arranque revienta explícito — la topología se
    valida ANTES de aceptar tráfico, no tarea a tarea en el upsert.
    """
    client = await get_qdrant_client()
    repo = VectorRepository(client=client)

    # Colecciones de texto: una por cada dominio del catálogo (BUG-001 #34),
    # mismas dims que el embedding de texto que genera _get_embedding.
    for domain in DomainType:
        await repo.ensure_collection(
            collection_name=domain.value,
            vector_size=TEXT_VECTOR_SIZE,
            distance=TEXT_VECTOR_DISTANCE,
        )

    # Colección de imágenes (jina-clip-v2, 1024 dims — ADR-007-002 #50).
    await repo.ensure_collection(
        collection_name=IMAGE_COLLECTION,
        vector_size=IMAGE_VECTOR_SIZE,
        distance=IMAGE_VECTOR_DISTANCE,
    )


async def _resolver_fallo(repo: TaskRepository, task_id: UUID, error: Exception) -> None:
    """
    Fase 3 (Fallo) del ciclo del worker, con la clasificación del 4.4-002
    (#104): fallo DETERMINISTA → fail_task (FAILED inmediato, cero
    reintentos — reproduce el mismo error por muchos que se pruebe); fallo
    TRANSITORIO → retry_task (PENDING diferida con backoff, o FAILED al
    agotar max_retries).

    Trabaja solo por UUID: la instancia task llega posiblemente expirada
    tras el rollback del handler (lección #61) y no se toca.
    """
    # Trace HONESTO (#61): clase + mensaje de la excepción real. str(e) a
    # secas perdía la clase (un httpx.ConnectError quedaba como «Connection
    # refused» sin culpable, y una excepción sin mensaje como trace vacío).
    # No se vuelca el traceback completo: el JSONB del payload ya viaja
    # cargado con la data URI; el diagnóstico de una línea es el contrato de
    # coreai_get_task.
    trace = f"{type(error).__name__}: {error}"

    if es_fallo_determinista(error):
        await repo.fail_task(task_id, trace)
        logger.info("Tarea %s FAILED: fallo determinista, sin reintento", task_id)
        return

    desenlace = await repo.retry_task(task_id, trace)
    if desenlace is None:
        logger.warning("Tarea %s desaparecida durante la resolución del fallo", task_id)
    elif desenlace["outcome"] == "RETRY":
        logger.info(
            "Tarea %s difiere: reintento %s disponible en %s",
            task_id, desenlace["retry_count"], desenlace["available_at"],
        )
    else:
        logger.info(
            "Tarea %s FAILED: reintentos agotados (%s), historial completo en el payload",
            task_id, desenlace["retry_count"],
        )


async def garantia_arranque() -> None:
    """
    ARRANQUE (BUG-011 #107): el worker garantiza TODO el sustrato que va a
    tocar antes de consumir la cola — esquema relacional Y topología
    vectorial. Hasta ahora cada proceso asumía que el otro ya había
    arrancado (el esquema lo garantizaba solo core-mcp, BUG-010 #87; las
    colecciones solo este worker, BUG-001 #34): un worker solo contra una
    BD limpia reclamaba en bucle de error de infra hasta que el otro
    arrancara.

    Orden: esquema primero — el claim es lo primero que toca el bucle.

    Sin engine.dispose() tras el ensure_relacional_schema: ese cierre
    existe en core-mcp porque SU bootstrap corre en un event loop efímero
    que se cierra antes de servir; el worker vive entero en UN solo loop
    (asyncio.run(worker_loop())), así que las conexiones de pool creadas
    por el bootstrap siguen válidas para el bucle entero.
    """
    # =================================================================
    # Esquema relacional con backoff (BUG-011 #107). Postgres puede estar
    # todavía levantándose: no morimos por orden de arranque — y NO
    # consumimos la cola hasta tener el esquema garantizado (el claim de
    # una BD limpia y sin core-mcp revienta en bucle de error de infra).
    # =================================================================
    boot_backoff = INITIAL_BACKOFF
    while True:
        try:
            await ensure_relational_schema()
            logger.info("Esquema relacional garantizado al arranque (BUG-011 #107)")
            break
        except Exception as init_error:
            logger.error(
                "No se pudo garantizar el esquema relacional: %s — reintento en %.1fs",
                init_error, boot_backoff,
            )
            await asyncio.sleep(boot_backoff)
            boot_backoff = min(boot_backoff * 2, MAX_BACKOFF)

    # =================================================================
    # Colecciones con backoff (ADR-007-002 #50, BUG-001 #34). Qdrant puede
    # estar todavía levantándose (el worker no declara depends_on sobre
    # él). Pero NO se consume la cola hasta que la topología completa esté
    # garantizada y validada.
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


async def worker_loop():
    # Ninguna reclamación antes de tener el sustrato garantizado (BUG-011 #107).
    await garantia_arranque()

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
                        # Traza Loki por UUID (Issue 4.3, ADR-003): el contexto de
                        # logging correlaciona TODAS las líneas de este ciclo
                        # (router, handler, manager) con la tarea. El formato
                        # lo renderiza como task=<uuid>; en Loki/Grafana se busca
                        # el UUID y aparece la traza completa del ciclo de vida.
                        set_current_task_id(task_id)
                        
                        # Fase 2: Trabajo en memoria delegando al Enrutador (Registry)
                        await route_task(task, session)
                        
                        # Fase 3 (Éxito)
                        await repo.complete_task(task_id)
                        logger.info("Tarea %s COMPLETED", task_id)
                        
                    except Exception as execution_error:
                        # Fase 3 (Fallo controlado de negocio): la clasificación
                        # determinista/transitoria del 4.4-002 (#104) decide el
                        # desenlace — FAILED inmediato o reintento con backoff.
                        # El trace honesto (clase + mensaje, lección #61) lo
                        # construye _resolver_fallo, que trabaja por UUID.
                        logger.error("Fallo ejecutando tarea %s: %s", task_id, execution_error)
                        await _resolver_fallo(repo, task_id, execution_error)
                    finally:
                        # El ciclo de la tarea termina (bien o mal): suelta el
                        # UUID del contexto para no filtrarlo a la siguiente.
                        clear_current_task_id()
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
