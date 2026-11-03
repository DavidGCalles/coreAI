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
from src.schemas.llm import EmbeddingRequest, IMAGE_EMBEDDING_MODEL
from src.contracts import task_contract_registry
from src.managers.memory_manager import HybridMemoryManager
from src.db.models import DomainType, Visibility

import uuid
from src.repositories.relational import EventRepository, LLMAuditRepository, SessionRepository, TaskRepository
from src.repositories.vector import IMAGE_COLLECTION, VectorRepository
from src.managers.memory_manager import HybridMemoryManager
from src.managers.llm_client import LLMClient

logger = logging.getLogger(__name__)

# Entrada del gateway LiteLLM para embeddings de imagen (BUG-004 #58):
# provider infinity → jina-clip-v2. El id de grupo vive en schemas/llm.py
# (única fuente de verdad, lección de la PR #63) y se reexporta desde aquí
# para no romper a los imports existentes del worker.

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
    Extrae la relación jerárquica desde Postgres, lee el contenido con
    extracción ESTRICTA por contrato (ADR-006: la frontera ya validó y
    normalizó el payload en el dispatch), y asimila la memoria en el grafo
    híbrido.
    """
    # Máscara greenlet (lección #61): el rollback del manager expira la instancia `task`
    # (expire_on_commit=False no protege: el rollback expira SIEMPRE). Se captura task_id
    # ANTES del await; tras un rollback, task.* revienta con MissingGreenlet y reemplaza
    # al error real en el error_trace del DLQ.
    task_id = task.id
    logger.info("  [Worker] Vectorizando evento para la tarea %s", task_id)

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
    # FASE 2: Extracción Estricta (purga de la extracción tolerante, 4.3)
    # =================================================================
    # La frontera ya validó y normalizó el payload contra el contrato
    # (VectorizeEventPayload) en el dispatch: 'content' es clave obligatoria
    # y única. La lista de claves candidatas era un backstop de la era
    # pre-contratos (decisión ADR-006): hoy es código inalcanzable para
    # payloads legítimos y solo puede enmascarar corrupción de cola. Una
    # clave ausente NO es flexibilidad: es corrupción.
    content = payload.get("content")
    if not content:
        raise ValueError(
            f"El payload de la tarea {task.id} no trae 'content' (contrato "
            f"vectorize_event). La frontera ya valida y normaliza la forma "
            f"en el dispatch: esto solo puede ser corrupción de cola. "
            f"Payload actual: {payload}"
        )

    domain_str = payload.get("domain", "SYSTEM")
    visibility_str = payload.get("visibility", "PRIVATE")

    # Proyecto (FR-3, #51): clave plana y OPCIONAL del contrato. Hallazgo
    # 26 oct: el contrato vectorize_event SÍ valida 'project', pero este
    # handler lo tiraba — nunca llegaba al payload de Qdrant (única fuga
    # contrato→DB). El recall la filtra; sin ella, los puntos de texto
    # nacen huérfanos de proyecto.
    project = payload.get("project")

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
            visibility=Visibility(visibility_str.lower()),
            project=project
        )

        logger.info("  [Worker] Vectorización completada con éxito para task %s", task.id)

    except ValueError as validation_error:
        # Captura de errores de casting de Enums (como lo de las mayúsculas)
        logger.error("  [Worker] Error de validación interna (Task %s): %s", task_id, validation_error)
        raise
    except Exception as execution_error:
        # Fallos de red, Qdrant, Postgres o Infinity
        logger.error("  [Worker] Error de ejecución en vectorización (Task %s): %s", task_id, execution_error)
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

    Descripción textual legible (BUG-009 #84): el contrato admite un
    `content` opcional que persiste TAL CUAL en el payload del punto —
    cero estado de sesión en el cliente, quien registra con descripción
    la recibe de vuelta en el hit del recall. No se embebe ni crea punto
    de texto: es la misma simetría de contenido que ya llevan los puntos
    de texto (el sustrato no piensa la descripción, la transporta).
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
        content = payload.get("content")
        project = payload.get("project")
        domain = DomainType(str(payload.get("domain", DomainType.SYSTEM.value)).lower())
        visibility = Visibility(str(payload.get("visibility", Visibility.PRIVATE.value)).lower())
    except (KeyError, ValueError, TypeError) as contract_error:
        raise ValueError(
            f"El payload de la tarea {task.id} no cumple el contrato "
            f"vectorize_image_event (image como data URI base64 + content "
            f"opcional + project opcional + domain + visibility). La frontera "
            f"ya valida y normaliza la forma en el dispatch: esto solo puede "
            f"ser corrupción de cola. Detalle: {contract_error}"
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
        # El content (BUG-009 #84) sí viaja TAL CUAL: clave plana legible,
        # simétrica con los puntos de texto — el recall la mapea al hit
        # (presente si se registró, None si no: retrocompatible).
        point_payload = {
            "tenant_id": str(entity_id),
            "session_id": str(task.session_id),
            "domain": domain.value,
            "visibility": visibility.value,
            "content": content,
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

    Limitación conocida de la v1 (BUG-006 #69): la agrupación es puramente
    temporal — una ventana heterogénea fusiona recuerdos sin relación en un
    mismo resumen (contaminación semántica permanente: el resumen queda
    embebido y se sirve en cada recall futuro). Evolución por temática
    (selección semántica) en el BUG-006 #69.
    """
    # Máscara greenlet (lección #61): el rollback del manager expira la instancia `task`
    # (expire_on_commit=False no protege: el rollback expira SIEMPRE). Se captura task_id
    # ANTES del await; tras un rollback, task.* revienta con MissingGreenlet y reemplaza
    # al error real en el error_trace del DLQ.
    task_id = task.id
    logger.info("  [Worker] Consolidando memoria para la tarea %s", task_id)

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
        logger.error("  [Worker] Error de validación en consolidación (Task %s): %s", task_id, validation_error)
        raise
    except Exception as execution_error:
        logger.error("  [Worker] Error de ejecución en consolidación (Task %s): %s", task_id, execution_error)
        raise

# =====================================================================
# HANDLER DE CORRECCIÓN DE METADATA DE MEMORIA (ADR-009, #89)
# =====================================================================

class MemoryPointNotFoundError(Exception):
    """
    El point_id referenciado por un evento de corrección no existe en
    ninguna colección registrada (ADR-009, #89).

    Fallo explícito, jamás silencio (decisión B del ADR-009): un no-op
    COMPLETED escondería bugs del cliente — point_id mal copiado, punto
    absorbido por consolidación — detrás de un éxito aparente. Clase
    propia y determinista, la que el futuro DLQ (#30) podrá clasificar
    como veneno no-reintentable: la corrección re-orienta lo almacenado,
    no fabrica puntos.
    """


async def handle_amend_memory_point(task: Task, session: AsyncSession) -> None:
    """
    Manejador del evento de corrección de metadata de un punto ya
    almacenado (ADR-009, #89): v1 corrige `project` por point_id, SIN
    re-embedding — el contenido vectorizado no cambia y el vector
    existente sigue válido.

    Estrategia dual-store (decisión A del ADR-009, cerrada con evidencia
    del esquema): Qdrant primero, Postgres después, cero compensación
    activa.

    - El `project` (FR-3) vive como clave plana del payload del punto en
      Qdrant — su única morada operativa, la que consume el recall. La
      fuente relacional de los puntos de TEXTO (events) no almacena
      project (cero migración por diseño): en texto la simetría la da el
      propio evento de corrección — esta task row con point_id y valor
      objetivo ES la traza relacional, y el Event original queda intacto
      (log append-only).
    - En IMAGEN el registro relacional del punto ES la task original
      (ADR-007-003: los tasks ya son eventos) y su JSONB sí lleva
      project: ahí aplica la actualización simétrica, atómica por UUID.
    - Ambas escrituras son sets idempotentes: un fallo a mitad (p. ej.
      Postgres tras el set_payload) deja la tarea FAILED con error_trace
      honesto y el re-dispatch converge — no se compensa nada activamente.

    Punto inexistente (decisión B): MemoryPointNotFoundError — veneno
    determinista no-reintentable, jamás un no-op silencioso.

    La identidad multi-tenant queda diferida como en el recall: el
    payload del punto conserva su tenant_id; el aislamiento por
    inquilino es su propia pieza de diseño.
    """
    # Máscara greenlet (lección #61): el rollback expira la instancia
    # task y task.* revienta con MissingGreenlet reemplazando al error
    # real. El id se captura ANTES de cualquier await.
    task_id = task.id
    logger.info("  [Worker] Corrigiendo metadata de punto para la tarea %s", task_id)

    payload = task.payload

    if not isinstance(payload, dict):
        raise ValueError(
            f"Payload corrupto para la tarea {task_id}: no es un objeto JSON."
        )

    # =================================================================
    # FASE 1: Extracción Estricta (sin extracción tolerante — ADR-006)
    # =================================================================
    # La frontera ya validó y normalizó el payload contra el contrato
    # (AmendMemoryPointPayload) en el dispatch. Aquí NO se buscan claves
    # alternativas: una clave ausente solo puede ser corrupción de cola.
    try:
        point_id = payload["point_id"]
        nuevo_project = payload["project"]
    except (KeyError, TypeError) as contract_error:
        raise ValueError(
            f"El payload de la tarea {task_id} no cumple el contrato "
            f"amend_memory_point (point_id + project). La frontera ya "
            f"valida y normaliza la forma en el dispatch: esto solo puede "
            f"ser corrupción de cola. Detalle: {contract_error}"
        ) from contract_error

    # =================================================================
    # FASE 2: Inyección de Dependencias
    # =================================================================
    try:
        qdrant_client = await get_qdrant_client()
        vector_repo = VectorRepository(client=qdrant_client)
        task_repo = TaskRepository(session)

        # =================================================================
        # FASE 3: Localización del punto (auto-resolución en las
        # colecciones registradas — la topología es del substrato)
        # =================================================================
        localizado = await vector_repo.locate_point(str(point_id))

        if localizado is None:
            # Decisión B del ADR-009: fallo explícito, jamás silencio. El
            # punto pudo ser absorbido por consolidación o el point_id mal
            # copiado del hit — cualquiera de los dos se oye, no se entierra.
            raise MemoryPointNotFoundError(
                f"El point_id '{point_id}' no existe en ninguna colección "
                f"registrada del vector store. La corrección re-orienta lo "
                f"almacenado, no fabrica puntos: verifica el point_id contra "
                f"el hit del recall, o re-ingesta el registro si debe existir."
            )

        collection, point_payload = localizado
        old_project = point_payload.get("project")

        # =================================================================
        # FASE 4: set_payload en Qdrant (el read path del recall) —
        # set idempotente, sin tocar el vector
        # =================================================================
        await vector_repo.amend_payload(
            collection_name=collection,
            point_id=str(point_id),
            fields={"project": nuevo_project},
        )

        # =================================================================
        # FASE 5: Simetría relacional + traza audible del ciclo de vida
        # =================================================================
        # En imagen, el registro relacional del punto (la task original)
        # sí almacena project: actualización simétrica atómica por UUID.
        if collection == IMAGE_COLLECTION:
            espejo = await task_repo.set_payload_fields(
                uuid.UUID(str(point_id)), {"project": nuevo_project}
            )
            if not espejo:
                raise MemoryPointNotFoundError(
                    f"El punto '{point_id}' vive en '{IMAGE_COLLECTION}' pero "
                    f"su registro relacional (la task original) no existe en "
                    f"Postgres: inconsistencia dual-store — revisar manualmente."
                )

        # El propio evento de corrección queda como traza audible: colección
        # donde vive el punto y valor previo, en su task row (append-only: los
        # registros origen no se reescriben). Sin commit: la transacción
        # pertenece al ciclo del worker (complete_task asienta).
        await task_repo.set_payload_fields(
            task_id,
            {"amended_collection": collection, "old_project": old_project},
        )

        logger.info(
            "  [Worker] Corrección completada para la tarea %s: punto %s en "
            "colección '%s', project %r → %r",
            task_id, point_id, collection, old_project, nuevo_project,
        )

    except (ValueError, MemoryPointNotFoundError):
        # Fallos de negocio explícitos: suben tal cual al fail_task del
        # worker (trace honesto {Clase}: {mensaje}). El cortafuegos de
        # rollback es para los fallos de EJECUCIÓN, no para estos.
        raise
    except Exception as execution_error:
        # Cortafuegos (patrón del handler de imagen): fallo a mitad de la
        # mutación dual-store → rollback de lo relacional pendiente y
        # re-raise. El set de Qdrant NO se compensa: es idempotente y el
        # re-dispatch converge (estrategia registrada en el ADR-009).
        await session.rollback()
        logger.error(
            "  [Worker] Error de ejecución en corrección de memoria (Task %s): %s",
            task_id,
            execution_error,
        )
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
    TaskType.AMEND_MEMORY_POINT: handle_amend_memory_point,
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

