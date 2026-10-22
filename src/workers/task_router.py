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
from src.schemas.llm import EmbeddingRequest
from src.contracts import task_contract_registry
from src.managers.memory_manager import HybridMemoryManager
from src.db.models import DomainType, Visibility

import uuid
from src.repositories.relational import EventRepository, LLMAuditRepository, SessionRepository
from src.repositories.vector import VectorRepository
from src.managers.memory_manager import HybridMemoryManager
from src.managers.llm_client import LLMClient

logger = logging.getLogger(__name__)

# Entrada del gateway LiteLLM para embeddings de imagen (BUG-004 #58): provider
# infinity → jina-clip-v2. El handler NUNCA habla con Infinity directo: la
# latencia (~4,3 s/imagen en CPU) y los tokens quedan en Prometheus sin
# instrumentar nada nuevo.
IMAGE_EMBEDDING_MODEL = "image-embedding"

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
    
    TODO: Implementar logica real de follow_up
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


# =====================================================================
# HANDLER DE VECTORIZACION DE IMAGENES POR EVENT LANE (ADR-007-003, #61)
# =====================================================================

async def handle_vectorize_image_event(task: Task, session: AsyncSession) -> None:
    """
    Manejador para tareas de vectorización de imágenes (ADR-007-003, #61).

    Vectorizar = generar el embedding NUMÉRICO de la imagen para búsqueda
    semántica — nada que ver con gráficos vectoriales (SVG/potrace).

    Evento auto-anclado (decisión de David, 22 oct): la tarea de imagen
    YA es el evento relacional — vive en Postgres, cuelga de una sesión y
    de un inquilino. La correlación con el registro textual de la misma
    pieza la da la SESIÓN (mismo session_id), no una referencia encadenada
    a otra tarea: coreografiar al inquilino desde el core era redundancia
    pura y el cliente la manejaba fatal. El embedding se consume del
    GATEWAY LiteLLM (entrada image-embedding, provider infinity — BUG-004
    #58), nunca de Infinity directo. El vector aterriza en la colección de
    imágenes (dim 1024 jina-clip-v2, espacio propio — init idempotente del
    #50) y la imagen en sí NO se persiste: el vector es dato derivado del
    propio evento.
    """
    logger.info("  [Worker] Vectorizando imagen para la tarea %s", task.id)

    payload = task.payload

    if not isinstance(payload, dict):
        raise ValueError(
            f"Payload corrupto para la tarea {task.id}: no es un objeto JSON."
        )

    # =================================================================
    # FASE 1: Resolución Relacional (el inquilino dueño del vector)
    # =================================================================
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
    # FASE 2: Extracción Estricta (sin extracción tolerante — ADR-006)
    # =================================================================
    # La frontera ya validó y normalizó el payload contra el contrato
    # (VectorizeImageEventPayload) en el dispatch. Aquí NO se buscan claves
    # alternativas: una clave ausente solo puede ser corrupción de cola.
    try:
        image_data_uri = payload["image"]
        project = payload.get("project")
        domain = DomainType(str(payload.get("domain", DomainType.SYSTEM.value)).lower())
        visibility = Visibility(str(payload.get("visibility", Visibility.PRIVATE.value)).lower())
    except (KeyError, ValueError, TypeError) as contract_error:
        raise ValueError(
            f"El payload de la tarea {task.id} no cumple el contrato "
            f"vectorize_image_event (image como data URI base64 + project "
            f"opcional + domain + visibility). La frontera ya valida y "
            f"normaliza la forma en el dispatch: esto solo puede ser "
            f"corrupción de cola. Detalle: {contract_error}"
        ) from contract_error

    # =================================================================
    # FASE 3: Inyección de Dependencias
    # =================================================================
    try:
        qdrant_client = await get_qdrant_client()

        vector_repo = VectorRepository(client=qdrant_client)
        audit_repo = LLMAuditRepository(session)
        llm_client = LLMClient()

        # =================================================================
        # FASE 4: Embedding vía GATEWAY LiteLLM (entrada image-embedding)
        # =================================================================
        # La legibilidad/decodificación real de la imagen es fallo legítimo
        # de esta fase (asíncrono), no de contrato: si el data URI no es
        # decodificable, el modelo de visión lo dirá aquí.
        response = await llm_client.generate_embedding(
            EmbeddingRequest(
                model=IMAGE_EMBEDDING_MODEL,
                input=image_data_uri,
                modality="image",
            )
        )
        vector = response.data[0].embedding if response.data else None

        if not vector:
            raise ValueError(
                f"El gateway devolvió un embedding vacío para la imagen de la "
                f"tarea {task.id}: imagen ilegible o corrupta para el modelo de "
                f"visión, o respuesta del gateway sin datos."
            )

        # Telemetría simétrica con el flujo textual: la factura de tokens de
        # imagen (char-proxy de Infinity, ~2429/imagen COCO) queda en Postgres
        # además de en Prometheus.
        await audit_repo.create(
            entity_id=entity_id,
            model_used=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=0,
            total_tokens=response.usage.total_tokens,
        )

        # =================================================================
        # FASE 5: Upsert a la colección de imágenes (espacio propio, #50)
        # =================================================================
        # El punto ES la tarea de imagen (idempotencia por re-dispatch:
        # imágenes distintas = tareas distintas). El payload ancla el
        # vector a su sesión (la correlación texto↔imagen ES la sesión) y
        # al proyecto (FR-3 #51: dato del inquilino en el propio evento).
        # La imagen NO viaja en el payload: el vector es dato derivado.
        point_payload = {
            "tenant_id": str(entity_id),
            "session_id": str(task.session_id),
            "domain": domain.value,
            "visibility": visibility.value,
            "project": project,
        }
        await vector_repo.upsert_image(
            point_id=task.id,
            vector=vector,
            payload=point_payload,
        )

        logger.info(
            "  [Worker] Vectorización de imagen completada con éxito para la "
            "tarea %s (sesión %s)",
            task.id,
            task.session_id,
        )

    except ValueError as validation_error:
        logger.error(
            "  [Worker] Error de validación en vectorización de imagen (Task %s): %s",
            task.id,
            validation_error,
        )
        raise
    except Exception as execution_error:
        # Fallos de red, gateway LiteLLM, Qdrant o Postgres. Cortafuegos del
        # flujo textual: nada a medias queda en la transacción.
        # OJO (hallazgo E2E 22 oct, #61): el rollback EXPIRA la instancia
        # task — después de él NO se toca task.* en el handler ni en el
        # worker: leer un atributo de una instancia expirada dispara un
        # refresh síncrono que, en contexto async, revienta con
        # greenlet_spawn y enmascara el error real (la tarea queda
        # huérfana en RUNNING). El id de la tarea lo registra el worker,
        # que captura task_id ANTES de ejecutar.
        await session.rollback()
        logger.error(
            "  [Worker] Error de ejecución en vectorización de imagen: %s",
            execution_error,
        )
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
    TaskType.VECTORIZE_IMAGE_EVENT: handle_vectorize_image_event,
}


# =====================================================================
# ORQUESTADORA CENTRAL
# =====================================================================

async def route_task(task: Task, session: AsyncSession) -> None:
    """
    Enrutador central del worker.

    Extrae el valor bruto del tipo de tarea desde el JSON (task.payload.get("task_type")).
    Taxonomía de fallo separada a propósito (hallazgo del smoke del 22 oct, #61 —
    el mensaje «fuera de catálogo» no puede mentir sobre un tipo contratado):
    - Tipo desconocido/corrupto/fuera de catálogo → ValueError citando los tipos
      despachables (con contrato).
    - Tipo CONTRATADO sin handler → NotImplementedError de brecha de implementación:
      el dispatch lo acepta (hay contrato) y la tarea muere aquí. Para el DLQ (#30)
      no es lo mismo esta clase que un tipo nunca visto.
    - Tipo del esquema SIN contrato → NotImplementedError distinto: no es despachable
      por la frontera (ADR-006-001); solo llega aquí por corrupción de cola.

    Ejecuta el manejador pasandole la tarea y la sesion asincrona de base de datos.

    Args:
        task: Instancia de la tarea SQLAlchemy
        session: Sesion asincrona de Postgres

    Raises:
        ValueError: Si el tipo de tarea es desconocido o corrupto
        NotImplementedError: Si el tipo carece de manejador (brecha de
            implementación si está contratado) o de contrato
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
        contracted = ", ".join(task_contract_registry.list()) or "ninguno"
        raise ValueError(
            f"Tipo de tarea desconocido, corrupto o fuera de catalogo: '{raw_type}' "
            f"(tarea {task.id}). Tipos despachables (con contrato): {contracted}."
        ) from e

    # Recuperar el manejador desde el diccionario TASK_REGISTRY
    handler = TASK_REGISTRY.get(task_type)

    if handler is None:
        if task_contract_registry.has_contract(task_type):
            raise NotImplementedError(
                f"El tipo de tarea '{task_type.value}' está CONTRATADO (despachable) "
                f"pero su handler no está registrado en TASK_REGISTRY (tarea {task.id}): "
                f"brecha de implementación — no es un tipo fuera de catálogo ni un "
                f"payload corrupto."
            )
        raise NotImplementedError(
            f"El tipo de tarea '{task_type.value}' está en el esquema pero SIN "
            f"contrato registrado (tarea {task.id}): no es despachable por la "
            f"frontera (ADR-006-001) y no debería haber llegado hasta el worker."
        )

    # Ejecutar el manejador pasandole la tarea y la sesion asincrona
    await handler(task, session)
