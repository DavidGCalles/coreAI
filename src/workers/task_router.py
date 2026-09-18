"""
Registry puro para enrutamiento de tareas del worker.

Este modulo implementa el patron Registry para evitar que las llamadas a MCP
inventen tipos de tareas inexistentes. La validacion se realiza en la puerta
de entrada usando Pydantic (src/schemas/tasks.py), y aqui centralizamos
todos los manejadores de tareas.
"""

import logging
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import Task
from src.db.vector_db import get_qdrant_client
from src.schemas.tasks import TaskType
from src.managers.memory_manager import HybridMemoryManager
from src.db.models import DomainType, Visibility

import uuid
from src.repositories.relational import EventRepository, LLMAuditRepository
from src.repositories.vector import VectorRepository
from src.managers.memory_manager import HybridMemoryManager
from src.managers.llm_client import LLMClient

logger = logging.getLogger(__name__)

# =====================================================================
# MANEJADORES INDIVIDUALES (Funciones asincronas independientes)
# =====================================================================

async def handle_extract_entities(task: Task, session: AsyncSession) -> None:
    """
    Manejador para tareas de extraccion de entidades.
    Asimila datos e inyecta en Postgres/Qdrant.
    
    TODO: Implementar logica real de asimilacion
    """
    logger.info("  [Worker] Extraccion de entidades para la tarea %s", task.id)


async def handle_init_session(task: Task, session: AsyncSession) -> None:
    """
    Manejador para inicializacion de contexto de sesion.
    Prepara el entorno para una nueva interaccion.
    
    TODO: Implementar logica real de inicializacion
    """
    logger.info("  [Worker] Inicializando contexto de sesion para la tarea %s", task.id)


async def handle_follow_up(task: Task, session: AsyncSession) -> None:
    """
    Manejador para seguimiento de interaccion.
    Respuesta al usuario tras una tarea anterior.
    
    TODO: Implementar logica real de follow-up
    """
    logger.info("  [Worker] Ejecutando follow_up para la tarea %s", task.id)


async def handle_dummy_test_task(task: Task, session: AsyncSession) -> None:
    """
    Manejador para tareas dummy (solo satisfacen la suite de tests).
    
    TODO: Implementar logica real de test
    """
    logger.info("  [Worker] Dummy test ejecutado con exito para la tarea %s", task.id)


# =====================================================================
# HANDLER DE VECTORIZACION DE EVENTOS (Issue 4.3)
# =====================================================================

async def handle_vectorize_event(task: Task, session: AsyncSession) -> None:
    """
    Manejador para tareas de vectorización de eventos.
    Lee el contenido de la tarea y genera el embedding a través del LLMClient,
    sincronizándolo en Qdrant.
    """
    logger.info("  [Worker] Vectorizando evento para la tarea %s", task.id)

    # =================================================================
    # FASE 1: Extracción y validación
    # =================================================================
    payload = task.payload
    
    entity_id_str = payload.get("entity_id")
    content = payload.get("content")
    domain_str = payload.get("domain", "SYSTEM")
    visibility_str = payload.get("visibility", "PRIVATE")

    if not entity_id_str or not content:
        raise ValueError(
            f"Faltan campos esenciales (entity_id, content) en task.payload para tarea {task.id}. "
            f"Payload actual: {payload}"
        )

    try:
        entity_id = uuid.UUID(entity_id_str)
    except (TypeError, ValueError) as e:
        raise ValueError(f"entity_id debe ser un UUID válido para tarea {task.id}") from e

    # =================================================================
    # FASE 2: Inyección de Dependencias Limpia
    # =================================================================
    try:
        # Obtenemos la conexión global al motor vectorial
        qdrant_client = await get_qdrant_client()
        
        # Instanciamos la artillería pesada
        vector_repo = VectorRepository(client=qdrant_client)
        event_repo = EventRepository(session)
        audit_repo = LLMAuditRepository(session)
        llm_client = LLMClient()

        memory_manager = HybridMemoryManager(
            session=session,
            relational_repo=event_repo,
            vector_repo=vector_repo,
            audit_repo=audit_repo,
            llm_client=llm_client
        )

        # =================================================================
        # FASE 3: Ejecución Transaccional
        # =================================================================
        await memory_manager.add_memory(
            entity_id=entity_id,
            domain=DomainType(domain_str.lower()),
            content=content,
            visibility=Visibility(visibility_str.lower())
        )

        logger.info("  [Worker] Vectorización completada para task %s", task.id)

    except ValueError as validation_error:
        logger.error("  [Worker] Error de validación en vectorización (Task %s): %s", task.id, validation_error)
        raise
    except Exception as execution_error:
        logger.error("  [Worker] Error de ejecución en vectorización (Task %s): %s", task.id, execution_error)
        raise


# =====================================================================
# REGISTRO CENTRAL (Diccionario TASK_REGISTRY)
# =====================================================================

TASK_REGISTRY: Dict[TaskType, Any] = {
    TaskType.EXTRACT_ENTITIES: handle_extract_entities,
    TaskType.INIT_SESSION: handle_init_session,
    TaskType.FOLLOW_UP: handle_follow_up,
    TaskType.DUMMY_TEST_TASK: handle_dummy_test_task,
    TaskType.VECTORIZE_EVENT: handle_vectorize_event,
}


# =====================================================================
# ORQUESTADORA CENTRAL
# =====================================================================

async def route_task(task: Task, session: AsyncSession) -> None:
    """
    Enrutador central del worker.

    Extrae el valor bruto del tipo de tarea desde el JSON (task.payload.get("task_type")).
    Valida que existe en el catalogo TASK_REGISTRY. Si el tipo es desconocido o corrupto,
    levanta un ValueError. Si esta en el catalogo pero no tiene manejador asignado,
    levanta un NotImplementedError.
    Ejecuta el manejador pasandole la tarea y la sesion asincrona de base de datos.

    Args:
        task: Instancia de la tarea SQLAlchemy
        session: Sesion asincrona de Postgres

    Raises:
        ValueError: Si el tipo de tarea es desconocido o corrupto
        NotImplementedError: Si el tipo esta en el esquema pero carece de manejador
        Exception: Cualquier error durante la ejecucion del manejador
    """
    # Extraer el valor bruto del tipo de tarea desde el JSON
    raw_type = task.payload.get("task_type")

    if raw_type is None:
        raise ValueError(f"Tipo de tarea no proporcionado en task.payload para la tarea {task.id}")

    # Validar que existe en el catalogo TaskType (Pydantic/StrEnum)
    try:
        task_type = TaskType(raw_type)
    except ValueError as e:
        raise ValueError(f"Tipo de tarea desconocido, corrupto o fuera de catalogo: {raw_type}") from e

    # Recuperar el manejador desde el diccionario TASK_REGISTRY
    handler = TASK_REGISTRY.get(task_type)

    if handler is None:
        raise NotImplementedError(
            f"El tipo de tarea '{task_type.value}' esta en el esquema pero carece de manejador asignado."
        )

    # Ejecutar el manejador pasandole la tarea y la sesion asincrona
    await handler(task, session)

