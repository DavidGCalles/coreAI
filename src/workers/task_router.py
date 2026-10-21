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
from sqlalchemy import select
from src.db.models import Task, Session
from src.db.vector_db import get_qdrant_client
from src.schemas.tasks import TaskType
from src.managers.memory_manager import HybridMemoryManager
from src.db.models import DomainType, Visibility

import uuid
from src.repositories.relational import EventRepository, LLMAuditRepository, SessionRepository
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
    Extrae la relación jerárquica desde Postgres, busca el contenido de forma flexible,
    y asimila la memoria en el grafo híbrido.
    """
    logger.info("  [Worker] Vectorizando evento para la tarea %s", task.id)

    payload = task.payload

    # =================================================================
    # FASE 1: Resolución Relacional (Buscando al dueño real)
    # =================================================================
    # El LLM no sabe de UUIDs, así que navegamos el grafo: Task -> Session -> Entity
    stmt = select(Session).where(Session.id == task.session_id)
    result = await session.execute(stmt)
    db_session = result.scalar_one_or_none()

    if not db_session:
        raise ValueError(
            f"Inconsistencia relacional fatal: La tarea {task.id} apunta a una "
            f"sesión inexistente ({task.session_id})."
        )
    
    entity_id = db_session.entity_id

    # =================================================================
    # FASE 2: Extracción Resiliente (Protección contra alucinaciones de claves)
    # =================================================================
    content = None
    # Buscamos en una lista de candidatos comunes en lugar de forzar una sola clave
    for candidate_key in ["content", "summary_content", "text", "data", "detailed_summary"]:
        if candidate_key in payload and payload[candidate_key]:
            content = payload[candidate_key]
            break

    if not content:
        raise ValueError(
            f"No se encontró texto para vectorizar. Claves buscadas: "
            f"content, summary_content, text, data. Payload actual: {payload}"
        )

    domain_str = payload.get("domain", "SYSTEM")
    visibility_str = payload.get("visibility", "PRIVATE")

    # =================================================================
    # FASE 3: Inyección de Dependencias
    # =================================================================
    try:
        qdrant_client = await get_qdrant_client()
        
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
        # FASE 4: Ejecución Transaccional
        # =================================================================
        await memory_manager.add_memory(
            entity_id=entity_id,
            domain=DomainType(domain_str.lower()),
            content=content,
            visibility=Visibility(visibility_str.lower())
        )

        logger.info("  [Worker] Vectorización completada con éxito para task %s", task.id)

    except ValueError as validation_error:
        # Captura de errores de casting de Enums (como lo de las mayúsculas)
        logger.error("  [Worker] Error de validación interna (Task %s): %s", task.id, validation_error)
        raise
    except Exception as execution_error:
        # Fallos de red, Qdrant, Postgres o Infinity
        logger.error("  [Worker] Error de ejecución en vectorización (Task %s): %s", task.id, execution_error)
        raise


async def handle_consolidate_memory(task: Task, session: AsyncSession) -> None:
    """
    Manejador para tareas de consolidación de memoria.
    Procesa una ventana temporal del dominio especificado y asimila 
    la memoria consolidada en el grafo híbrido.
    """
    logger.info("  [Worker] Consolidando memoria para la tarea %s", task.id)

    payload = task.payload

    # =================================================================
    # FASE 1: Validación de Parámetros Esenciales
    # =================================================================
    domain_str = payload.get("domain")
    time_window_str = payload.get("time_window")

    if not domain_str or not time_window_str:
        raise ValueError(
            f"Parámetros 'domain' o 'time_window' faltantes en el payload para la tarea {task.id}. "
            f"Payload actual: {payload}"
        )

    # =================================================================
    # FASE 2: Resolución Relacional (Vía Repositorio Puro)
    # =================================================================
    session_repo = SessionRepository(session)
    db_session = await session_repo.get(task.session_id)

    if not db_session:
        raise ValueError(
            f"Inconsistencia relacional fatal: La tarea {task.id} apunta a una "
            f"sesión inexistente ({task.session_id})."
        )
    
    entity_id = db_session.entity_id

    # =================================================================
    # FASE 3: Inyección de Dependencias
    # =================================================================
    try:
        qdrant_client = await get_qdrant_client()
        
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
        # FASE 4: Ejecución Transaccional (Orquestador Híbrido)
        # =================================================================
        await memory_manager.consolidate_memory(
            entity_id=entity_id,
            domain=DomainType(domain_str.lower()),
            time_window=time_window_str
        )

        logger.info("  [Worker] Consolidación de memoria completada con éxito para task %s", task.id)

    except ValueError as validation_error:
        logger.error("  [Worker] Error de validación en consolidación (Task %s): %s", task.id, validation_error)
        raise
    except Exception as execution_error:
        logger.error("  [Worker] Error de ejecución en consolidación (Task %s): %s", task.id, execution_error)
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
    TaskType.CONSOLIDATE_MEMORY: handle_consolidate_memory,
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

