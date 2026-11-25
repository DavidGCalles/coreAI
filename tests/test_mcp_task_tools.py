import pytest
import json
import uuid
from sqlalchemy import delete, select, func, update

from src.db.database import AsyncSessionLocal, engine
from src.db.models import Task, Session, Entity, TaskStatus
from src.mcp.tools.task_tools import handle_task_tool
from src.contracts import contract_uri

@pytest.fixture
async def db_session():
    """Sesión de aserción (la tool gestiona su propia sesión interna)."""
    await engine.dispose()
    async with AsyncSessionLocal() as session:
        yield session
        # No hacemos rollback general aquí porque la tool ya hizo commit de su propia sesión

async def _row_counts(db_session):
    """Conteo de las tres tablas mutables de la frontera (tasks/sessions/entities)."""
    tasks = (await db_session.execute(select(func.count(Task.id)))).scalar_one()
    sessions = (await db_session.execute(select(func.count(Session.id)))).scalar_one()
    entities = (await db_session.execute(select(func.count(Entity.id)))).scalar_one()
    return (tasks, sessions, entities)

@pytest.mark.asyncio
async def test_mcp_dispatch_task_tool_integration(db_session):
    """
    Valida el contrato de red: simula un payload RPC crudo desde un cliente MCP 
    y aserta el formato de salida estricto.
    
    Migración ADR-006-001 (#43): el tracer usaba el stub 'init_session', que ya no
    es despachable (sin contrato no hay despacho). Usa 'vectorize_event', un tipo
    con contrato registrado, con un payload conforme a su contrato.
    """
    # 1. Payload crudo simulando la entrada de un LLM (contrato de vectorize_event)
    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {
            "content": "Llamada desde cliente externo MCP.",
            "domain": "conversation",
            "visibility": "private"
        }
    }
    
    # 2. Ejecutar el manejador del Gateway
    response = await handle_task_tool("coreai_dispatch_task", arguments)
    
    # 3. Validar el contrato de respuesta (lo que leerá el LLM)
    assert response["isError"] is False, f"La herramienta devolvió error: {response}"
    assert len(response["content"]) == 1
    assert response["content"][0]["type"] == "text"
    
    # 4. Validar la des-serialización de la salida
    content = json.loads(response["content"][0]["text"])
    assert "task_id" in content
    assert "session_id" in content
    assert content["status"] == "PENDING"
    
    # 5. Comprobación final en BD para confirmar la integración real
    task_id = uuid.UUID(content["task_id"])
    result = await db_session.execute(select(Task).where(Task.id == task_id))
    task_in_db = result.scalar_one_or_none()
    
    assert task_in_db is not None, "El Gateway MCP no persistió la tarea."
    
    # Limpieza manual ya que handle_task_tool hace su propio commit
    await db_session.delete(task_in_db)
    await db_session.commit()

@pytest.mark.asyncio
async def test_mcp_dispatch_rejects_contract_violation_synchronously(db_session):
    """
    ADR-006-003 (#44): repro canónico del BUG-002 — claves inventadas
    (purpose/description, sin content) sobre un tipo CONTRATADO.

    Antes de #44: SUCCESS falso (PENDING) en la tool call y task FAILED minutos
    después en el worker. Ahora: isError síncrono en la frontera, error autónomo
    (URI + esquema inline) y cero efectos colaterales en BD.
    """
    before = await _row_counts(db_session)

    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {
            "purpose": "Clave inventada por el agente.",
            "description": "Segunda clave fantasma — sin 'content'."
        }
    }
    response = await handle_task_tool("coreai_dispatch_task", arguments)

    # 1. Rechazo síncrono en la tool call
    assert response["isError"] is True, f"BUG-002 vivo: la violación pasó la frontera: {response}"
    text = response["content"][0]["text"]

    # 2. El error es autónomo: cita la URI del contrato y la esquema inline
    assert contract_uri("vectorize_event") in text
    assert "purpose" in text  # la clave fantasma figura en las violaciones
    assert "content" in text  # la esquema inline declara la clave obligatoria

    # 3. Cero efectos colaterales: ni task encolada ni sesión/entidad auto-vivificada
    after = await _row_counts(db_session)
    assert after == before, f"La frontera dejó filas nuevas: antes {before}, después {after}"

@pytest.mark.asyncio
async def test_mcp_dispatch_persists_normalized_payload(db_session):
    """
    ADR-006-003 (#44): el payload entra al motor YA NORMALIZADO vía el loader
    del contrato — los defaults del contrato quedan materializados en BD.
    """
    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {"content": "Payload sin domain ni visibility explícitos."}
    }
    response = await handle_task_tool("coreai_dispatch_task", arguments)
    assert response["isError"] is False, f"La herramienta devolvió error: {response}"

    content = json.loads(response["content"][0]["text"])
    task_id = uuid.UUID(content["task_id"])
    result = await db_session.execute(select(Task).where(Task.id == task_id))
    task_in_db = result.scalar_one_or_none()

    assert task_in_db is not None, "El Gateway MCP no persistió la tarea."
    assert task_in_db.payload["domain"] == "system"
    assert task_in_db.payload["visibility"] == "private"

    # Limpieza manual ya que handle_task_tool hace su propio commit
    await db_session.delete(task_in_db)
    await db_session.commit()

# =====================================================================
# BUG-003 (#53): coreai_get_task — consulta read-only de estado
# =====================================================================

@pytest.mark.asyncio
async def test_mcp_get_task_returns_pending_after_dispatch(db_session):
    """
    BUG-003 (#53): dispatch → get_task cierra el bucle write-then-read.
    El estado consultado es el real de la fila, no una inferencia en memoria.
    """
    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {"content": "Tarea para validar la consulta de estado."}
    }
    dispatch_response = await handle_task_tool("coreai_dispatch_task", arguments)
    assert dispatch_response["isError"] is False, f"La herramienta devolvió error: {dispatch_response}"
    task_id = json.loads(dispatch_response["content"][0]["text"])["task_id"]

    response = await handle_task_tool("coreai_get_task", {"task_id": task_id})
    assert response["isError"] is False, f"La consulta de estado falló: {response}"
    assert len(response["content"]) == 1

    content = json.loads(response["content"][0]["text"])
    assert content["task_id"] == task_id
    assert content["task_type"] == "vectorize_event"
    assert content["status"] == "PENDING"
    assert content["error_trace"] is None
    assert "payload" not in content  # perfil mínimo: el payload crudo no viaja

    # Limpieza manual ya que handle_task_tool hace su propio commit
    result = await db_session.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
    await db_session.delete(result.scalar_one())
    await db_session.commit()

@pytest.mark.asyncio
async def test_mcp_get_task_exposes_error_trace_on_failed(db_session):
    """
    BUG-003 (#53): una FAILED legítima (fallo de ejecución, no de contrato)
    expone su error_trace. Simulamos lo que el worker deja tras fail_task:
    status FAILED + error_trace inyectado dentro del JSONB payload.
    """
    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {"content": "Tarea que simula un fallo del worker."}
    }
    dispatch_response = await handle_task_tool("coreai_dispatch_task", arguments)
    assert dispatch_response["isError"] is False, f"La herramienta devolvió error: {dispatch_response}"
    task_id = uuid.UUID(json.loads(dispatch_response["content"][0]["text"])["task_id"])

    await db_session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(
            status=TaskStatus.FAILED,
            payload={
                "task_type": "vectorize_event",
                "content": "Tarea que simula un fallo del worker.",
                "error_trace": "KeyError: 'session' en route_task",
            },
        )
    )
    await db_session.commit()

    response = await handle_task_tool("coreai_get_task", {"task_id": str(task_id)})
    assert response["isError"] is False, f"La consulta de estado falló: {response}"

    content = json.loads(response["content"][0]["text"])
    assert content["status"] == "FAILED"
    assert content["error_trace"] == "KeyError: 'session' en route_task"

    # Limpieza manual
    task_in_db = await db_session.get(Task, task_id)
    await db_session.delete(task_in_db)
    await db_session.commit()

@pytest.mark.asyncio
async def test_mcp_get_task_unknown_id_is_explicit_error(db_session):
    """
    BUG-003 (#53): un task_id inexistente es un error explícito con isError,
    no una respuesta vacía ni una fila inventada. Cero mutación colateral.
    """
    ghost_id = uuid.uuid4()
    before = await _row_counts(db_session)

    response = await handle_task_tool("coreai_get_task", {"task_id": str(ghost_id)})

    assert response["isError"] is True, "Un UUID fantasma debe ser error explícito"
    assert str(ghost_id) in response["content"][0]["text"]

    after = await _row_counts(db_session)
    assert after == before, f"La consulta mutó la BD: antes {before}, después {after}"

@pytest.mark.asyncio
async def test_mcp_get_task_does_not_mutate_the_row(db_session):
    """
    BUG-003 (#53): read-only estricto — dos consultas seguidas no tocan
    ni el estado ni el payload de la fila.
    """
    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {"content": "Tarea que no debe mutar por una consulta."}
    }
    dispatch_response = await handle_task_tool("coreai_dispatch_task", arguments)
    assert dispatch_response["isError"] is False, f"La herramienta devolvió error: {dispatch_response}"
    task_id = uuid.UUID(json.loads(dispatch_response["content"][0]["text"])["task_id"])

    result = await db_session.execute(select(Task).where(Task.id == task_id))
    task_before = result.scalar_one()
    payload_before = dict(task_before.payload)

    for _ in range(2):
        response = await handle_task_tool("coreai_get_task", {"task_id": str(task_id)})
        assert response["isError"] is False, f"La consulta de estado falló: {response}"

    # Releemos desde BD (refresh salta la caché de identidad de la sesión)
    await db_session.refresh(task_before)
    assert task_before.status == TaskStatus.PENDING
    assert task_before.payload == payload_before
    assert "error_trace" not in task_before.payload

    # Limpieza manual
    await db_session.delete(task_before)
    await db_session.commit()

# =====================================================================
# 4.4-003 (#105): superficie MCP de la DLQ — coreai_list_tasks +
# coreai_retry_failed_task (reinyección manual)
# =====================================================================

async def _dispatch_task(db_session, content):
    """Dispatcha una tarea válida y devuelve su task_id (sin worker)."""
    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {"content": content}
    }
    dispatch_response = await handle_task_tool("coreai_dispatch_task", arguments)
    assert dispatch_response["isError"] is False, f"El dispatch falló: {dispatch_response}"
    return uuid.UUID(json.loads(dispatch_response["content"][0]["text"])["task_id"])

async def _force_state(db_session, task_id, status, retry_count=None, payload=None):
    """Deja la fila como la dejaría el worker al resolver el desenlace."""
    values = {"status": status}
    if retry_count is not None:
        values["retry_count"] = retry_count
    if payload is not None:
        values["payload"] = payload
    await db_session.execute(update(Task).where(Task.id == task_id).values(**values))
    await db_session.commit()

def _failed_payload(content):
    """Payload como el que deja retry_task tras agotar los reintentos (4.4-002)."""
    traza = "ConnectionError: gateway LiteLLM inalcanzable"
    return {
        "task_type": "vectorize_event",
        "content": content,
        "error_trace": traza,
        "error_history": [
            {"attempt": 1, "at": "2026-11-17T09:00:00+00:00", "trace": traza},
            {"attempt": 2, "at": "2026-11-17T09:00:30+00:00", "trace": traza},
            {"attempt": 3, "at": "2026-11-17T09:01:10+00:00", "trace": traza},
        ],
    }

@pytest.mark.asyncio
async def test_mcp_list_tasks_exposes_dlq_profile_on_failed(db_session):
    """
    4.4-003 (#105): la DLQ (status=FAILED) expone por ítem retry_count,
    error_history y edad, en orden FIFO, sin mutar nada. El payload crudo
    no viaja (perfil mínimo).
    """
    task_id = await _dispatch_task(db_session, "Tarea que agota sus reintentos.")
    await _force_state(
        db_session, task_id, TaskStatus.FAILED,
        retry_count=3, payload=_failed_payload("Tarea que agota sus reintentos."),
    )

    response = await handle_task_tool("coreai_list_tasks", {"status": "FAILED", "limit": 10})
    assert response["isError"] is False, f"El listado falló: {response}"

    content = json.loads(response["content"][0]["text"])
    assert content["status"] == "FAILED"
    assert content["count"] >= 1

    item = next(t for t in content["tasks"] if t["task_id"] == str(task_id))
    assert item["task_type"] == "vectorize_event"
    assert item["status"] == "FAILED"
    assert item["retry_count"] == 3
    assert item["error_trace"] == "ConnectionError: gateway LiteLLM inalcanzable"
    assert [entrada["attempt"] for entrada in item["error_history"]] == [1, 2, 3]
    assert item["age_seconds"] >= 0
    assert "payload" not in item  # perfil mínimo: el payload crudo no viaja

    # FIFO: la respuesta viaja ordenada de la más antigua a la más nueva
    created = [t["created_at"] for t in content["tasks"]]
    assert created == sorted(created)

    # Read-only estricto: el listado no mutó la fila
    db_session.expire_all()
    task_in_db = await db_session.get(Task, task_id)
    assert task_in_db.status == TaskStatus.FAILED
    assert task_in_db.retry_count == 3

    # Limpieza manual
    await db_session.delete(task_in_db)
    await db_session.commit()

@pytest.mark.asyncio
async def test_mcp_list_tasks_filters_by_status(db_session):
    """4.4-003 (#105): el listado devuelve SOLO las tareas del estado pedido."""
    failed_id = await _dispatch_task(db_session, "Tarea fallida para el filtro por estado.")
    await _force_state(
        db_session, failed_id, TaskStatus.FAILED,
        retry_count=1, payload=_failed_payload("Tarea fallida para el filtro por estado."),
    )
    completed_id = await _dispatch_task(db_session, "Tarea completada para el filtro por estado.")
    await _force_state(db_session, completed_id, TaskStatus.COMPLETED)

    response = await handle_task_tool("coreai_list_tasks", {"status": "FAILED", "limit": 50})
    assert response["isError"] is False, f"El listado falló: {response}"
    ids = [t["task_id"] for t in json.loads(response["content"][0]["text"])["tasks"]]
    assert str(failed_id) in ids
    assert str(completed_id) not in ids

    response_completed = await handle_task_tool("coreai_list_tasks", {"status": "COMPLETED", "limit": 50})
    ids_completed = [t["task_id"] for t in json.loads(response_completed["content"][0]["text"])["tasks"]]
    assert str(completed_id) in ids_completed
    assert str(failed_id) not in ids_completed

    # Limpieza manual
    for task_id in (failed_id, completed_id):
        await db_session.delete(await db_session.get(Task, task_id))
    await db_session.commit()

@pytest.mark.asyncio
async def test_mcp_list_tasks_invalid_status_is_explicit_error(db_session):
    """4.4-003 (#105): un estado inventado rebota en la frontera, sin mutar la BD."""
    before = await _row_counts(db_session)

    response = await handle_task_tool("coreai_list_tasks", {"status": "garbage", "limit": 10})

    assert response["isError"] is True, "Un estado inventado debe ser error explícito"
    assert "garbage" in response["content"][0]["text"]

    after = await _row_counts(db_session)
    assert after == before, f"El rechazo mutó la BD: antes {before}, después {after}"

@pytest.mark.asyncio
async def test_mcp_list_tasks_limit_out_of_bounds_is_explicit_error(db_session):
    """4.4-003 (#105): el contrato del límite (1..200) se aplica en la frontera."""
    for limit in (0, 201):
        response = await handle_task_tool("coreai_list_tasks", {"status": "FAILED", "limit": limit})
        assert response["isError"] is True, f"limit={limit} debía rebotar en la frontera"

@pytest.mark.asyncio
async def test_mcp_retry_failed_task_reinjects_to_pending_from_scratch(db_session):
    """
    4.4-003 (#105): la reinyección manual deja la FAILED como recién nacida —
    PENDING, retry_count=0, available_at=NULL — con el error_history intacto
    (la mecánica se resetea, la auditoría no), y el listado PENDING la ve así.
    """
    task_id = await _dispatch_task(db_session, "Tarea fallida que se reinyecta manualmente.")
    history = _failed_payload("Tarea fallida que se reinyecta manualmente.")
    await _force_state(db_session, task_id, TaskStatus.FAILED, retry_count=3, payload=history)

    response = await handle_task_tool("coreai_retry_failed_task", {"task_id": str(task_id)})
    assert response["isError"] is False, f"La reinyección falló: {response}"

    content = json.loads(response["content"][0]["text"])
    assert content["task_id"] == str(task_id)
    assert content["status"] == "PENDING"
    assert content["retry_count"] == 0
    assert content["available_at"] is None

    db_session.expire_all()
    task_in_db = await db_session.get(Task, task_id)
    assert task_in_db.status == TaskStatus.PENDING
    assert task_in_db.retry_count == 0
    assert task_in_db.available_at is None
    # La auditoría queda intacta: la reinyección no reescribe el historial
    assert task_in_db.payload["error_history"] == history["error_history"]

    # Convergencia con la otra superficie: el listado PENDING la ve reseteada
    pending = await handle_task_tool("coreai_list_tasks", {"status": "PENDING", "limit": 50})
    assert pending["isError"] is False, f"El listado PENDING falló: {pending}"
    item = next(
        t for t in json.loads(pending["content"][0]["text"])["tasks"]
        if t["task_id"] == str(task_id)
    )
    assert item["retry_count"] == 0
    assert item["error_history"] == history["error_history"]

    # Limpieza manual
    await db_session.delete(task_in_db)
    await db_session.commit()

@pytest.mark.asyncio
async def test_mcp_retry_failed_task_rejects_non_failed_states(db_session):
    """
    4.4-003 (#105): solo FAILED es reinyectable — cualquier estado vivo
    rebota con error explícito que nombra el estado, sin mutar la fila.
    """
    task_id = await _dispatch_task(db_session, "Tarea viva que nunca debe reinyectarse.")

    for status in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.COMPLETED):
        await _force_state(db_session, task_id, status)
        response = await handle_task_tool("coreai_retry_failed_task", {"task_id": str(task_id)})

        assert response["isError"] is True, (
            f"Una {status.value} NO es reinyectable: el reintento debió rebotar"
        )
        assert status.value in response["content"][0]["text"]

        # El rechazo no mutó la fila
        db_session.expire_all()
        task_in_db = await db_session.get(Task, task_id)
        assert task_in_db.status == status
        assert task_in_db.retry_count == 0

    # Limpieza manual
    await db_session.delete(await db_session.get(Task, task_id))
    await db_session.commit()

@pytest.mark.asyncio
async def test_mcp_retry_failed_task_unknown_id_is_explicit_error(db_session):
    """4.4-003 (#105): un task_id inexistente es error explícito, no silencio."""
    ghost_id = uuid.uuid4()
    before = await _row_counts(db_session)

    response = await handle_task_tool("coreai_retry_failed_task", {"task_id": str(ghost_id)})

    assert response["isError"] is True, "Un UUID fantasma debe ser error explícito"
    assert str(ghost_id) in response["content"][0]["text"]

    after = await _row_counts(db_session)
    assert after == before, f"El rechazo mutó la BD: antes {before}, después {after}"

@pytest.mark.asyncio
async def test_mcp_retry_failed_task_malformed_args_is_explicit_error(db_session):
    """4.4-003 (#105): un task_id malformado rebota en la frontera, síncrono."""
    response = await handle_task_tool("coreai_retry_failed_task", {"task_id": "no-soy-un-uuid"})

    assert response["isError"] is True, "Un task_id malformado debe rebotar en la frontera"

@pytest.mark.asyncio
async def test_mcp_retry_failed_task_converges_without_double_application(db_session):
    """
    4.4-003 (#105): el desenlace converge — la segunda reinyección sobre la
    tarea ya reinyectada rebota con error explícito y NO aplica nada dos
    veces: el UPDATE condicional (WHERE status=FAILED) ES la idempotencia.
    """
    task_id = await _dispatch_task(db_session, "Tarea reinyectada que no debe mutar dos veces.")
    await _force_state(
        db_session, task_id, TaskStatus.FAILED,
        retry_count=3, payload=_failed_payload("Tarea reinyectada que no debe mutar dos veces."),
    )

    first = await handle_task_tool("coreai_retry_failed_task", {"task_id": str(task_id)})
    assert first["isError"] is False, f"La primera reinyección falló: {first}"

    second = await handle_task_tool("coreai_retry_failed_task", {"task_id": str(task_id)})
    assert second["isError"] is True, "La segunda llamada ya no es FAILED: debe rebotar explícito"
    assert "PENDING" in second["content"][0]["text"]

    # La fila sigue exactamente donde la dejó la primera reinyección
    db_session.expire_all()
    task_in_db = await db_session.get(Task, task_id)
    assert task_in_db.status == TaskStatus.PENDING
    assert task_in_db.retry_count == 0
    assert task_in_db.available_at is None

    # Limpieza manual
    await db_session.delete(task_in_db)
    await db_session.commit()

# =====================================================================
# ADR-012-002 (#113): frontera de identidad — los tres casos de resolución
# =====================================================================

async def _purge_entity_by_slug(db_session, slug):
    """
    Limpieza hermética e idempotente: retira la entidad de prueba con TODO lo
    que cuelga de ella, en orden FK-seguro. tasks.entity_id NO lleva ondelete
    (la titularidad es referencial, ADR-012-002): una task titulada bloquea
    el DELETE de su entidad, así que se retira primero; las sesiones caerían
    solas por el CASCADE de sessions.entity_id, pero se retiran a mano para
    no depender del orden en que Postgres evalúa las restricciones.
    Arranque y cierre del caso, para que runs sucesivos no pisen slugs ya
    registrados (idx_entities_slug es unique).
    """
    entity_id = (await db_session.execute(
        select(Entity.id).where(Entity.slug == slug)
    )).scalar_one_or_none()
    if entity_id is None:
        return
    await db_session.execute(
        delete(Task)
        .where(Task.entity_id == entity_id)
        .execution_options(synchronize_session=False)
    )
    await db_session.execute(
        delete(Session)
        .where(Session.entity_id == entity_id)
        .execution_options(synchronize_session=False)
    )
    await db_session.execute(
        delete(Entity)
        .where(Entity.id == entity_id)
        .execution_options(synchronize_session=False)
    )
    await db_session.commit()

@pytest.mark.asyncio
async def test_mcp_dispatch_identidad_declarada_registrada(db_session):
    """
    Caso 1 (ADR-012-002): declarada y registrada → la respuesta devuelve la
    entity_id resuelta, la fila nace titulada y la sesión huérfana se liga
    a la entidad declarada.
    """
    await _purge_entity_by_slug(db_session, "frontier-david")
    declared = Entity(role="user", slug="frontier-david")
    db_session.add(declared)
    await db_session.commit()

    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {"content": "Despacho con identidad declarada y registrada."},
        "entity": "frontier-david",
    }
    response = await handle_task_tool("coreai_dispatch_task", arguments)

    assert response["isError"] is False, f"El dispatch falló: {response}"
    content = json.loads(response["content"][0]["text"])
    assert content["entity_id"] == str(declared.id)

    task_id = uuid.UUID(content["task_id"])
    task_in_db = (await db_session.execute(
        select(Task).where(Task.id == task_id)
    )).scalar_one()
    assert task_in_db.entity_id == declared.id, "La task no nació titulada a la declarada."

    session_in_db = (await db_session.execute(
        select(Session).where(Session.id == task_in_db.session_id)
    )).scalar_one()
    assert session_in_db.entity_id == declared.id, "La sesión huérfana no quedó ligada a la declarada."

    await _purge_entity_by_slug(db_session, "frontier-david")

@pytest.mark.asyncio
async def test_mcp_dispatch_identidad_no_registrada_es_veneno(db_session):
    """
    Caso 2 / D4 (ADR-012-002): declarada y NO registrada → isError
    DETERMINISTA con catálogo inline; read-only total: ni task en PENDING ni
    RUNNING, ni sesión o entidad auto-vivificada. Y aplica a TODAS las task
    types: la frontera es una sola.
    """
    before = await _row_counts(db_session)

    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {"content": "Despacho con entidad fantasma."},
        "entity": "frontier-fantasma",
    }
    response = await handle_task_tool("coreai_dispatch_task", arguments)

    assert response["isError"] is True, "El slug no registrado debe rebotar en la frontera"
    text = response["content"][0]["text"]
    assert "frontier-fantasma" in text
    assert "no está registrada" in text
    assert "determinista" in text, "El error debe clasificarse como determinista"
    assert "NO se encoló" in text
    assert "system_default" in text, "El catálogo inline incluye al implícito"

    after = await _row_counts(db_session)
    assert after == before, f"El veneno dejó filas: antes {before}, después {after}"

@pytest.mark.asyncio
async def test_mcp_dispatch_identidad_ausente_resuelve_system_default(db_session):
    """
    Caso 3 (ADR-012-002): identidad ausente → system_default EXPLÍCITO en la
    respuesta y en la titularidad de la fila. La resolución deja de ser
    silenciosa sin exigir provisión previa al despachante (arranque autónomo)
    y NINGÚN despacho crea entidades: implícito ≠ auto-vivificado.
    """
    before = await _row_counts(db_session)

    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {"content": "Despacho sin identidad declarada."},
    }
    response = await handle_task_tool("coreai_dispatch_task", arguments)

    assert response["isError"] is False, f"El dispatch falló: {response}"
    content = json.loads(response["content"][0]["text"])

    implicit = (await db_session.execute(
        select(Entity).where(Entity.slug == "system_default")
    )).scalar_one()  # garantizado por el bootstrap (ADR-012-001, conftest)
    assert content["entity_id"] == str(implicit.id)

    task_in_db = (await db_session.execute(
        select(Task).where(Task.id == uuid.UUID(content["task_id"]))
    )).scalar_one()
    assert task_in_db.entity_id == implicit.id, "La task no quedó titulada al implícito."

    after = await _row_counts(db_session)
    assert after[0] == before[0] + 1, "la task"
    assert after[1] == before[1] + 1, "la sesión huérfana vivificada"
    assert after[2] == before[2], "NINGUNA entidad auto-vivificada: fin del patrón"

    # Limpieza manual: solo lo creado por el caso (la fila del implícito es del
    # bootstrap). Baja por bulk y en orden FK (task → sesión): el DELETE ORM en
    # unidad-de-trabajo puede disparar la sesión primero y el CASCADE de
    # tasks.session_id se llevaría la fila antes de que corra el DELETE de la
    # task (SAWarning: 0 filas matcheadas). La bulk deletion ni siquiera hace
    # planning en ORM: lo que se pide es lo que corre.
    session_in_db = (await db_session.execute(
        select(Session).where(Session.id == task_in_db.session_id)
    )).scalar_one()
    await db_session.execute(
        delete(Task)
        .where(Task.id == task_in_db.id)
        .execution_options(synchronize_session=False)
    )
    await db_session.execute(
        delete(Session)
        .where(Session.id == session_in_db.id)
        .execution_options(synchronize_session=False)
    )
    await db_session.commit()

@pytest.mark.asyncio
async def test_mcp_dispatch_identidad_titula_con_sesion_explicita(db_session):
    """
    La identidad es del DESPACHO, no de la sesión: con session_id explícito
    la task nace titulada a la entidad declarada aunque la sesión pertenezca
    a otra entidad — la coherencia sesión↔despachante no es ley de la
    frontera (ADR-012-002).
    """
    for slug in ("frontier-david", "frontier-owner"):
        await _purge_entity_by_slug(db_session, slug)
    declared = Entity(role="user", slug="frontier-david")
    owner = Entity(role="user", slug="frontier-owner")
    db_session.add_all([declared, owner])
    await db_session.flush()
    # Los modelos no definen relationships: la sesión se liga por entity_id
    sesion_ajena = Session(entity_id=owner.id)
    db_session.add(sesion_ajena)
    await db_session.commit()

    arguments = {
        "task_type": "vectorize_event",
        "task_payload": {"content": "Despacho titulado al declarante con sesión ajena."},
        "session_id": str(sesion_ajena.id),
        "entity": "frontier-david",
    }
    response = await handle_task_tool("coreai_dispatch_task", arguments)

    assert response["isError"] is False, f"El dispatch falló: {response}"
    content = json.loads(response["content"][0]["text"])
    assert content["session_id"] == str(sesion_ajena.id)
    assert content["entity_id"] == str(declared.id)

    task_in_db = (await db_session.execute(
        select(Task).where(Task.id == uuid.UUID(content["task_id"]))
    )).scalar_one()
    assert task_in_db.entity_id == declared.id

    for slug in ("frontier-david", "frontier-owner"):
        await _purge_entity_by_slug(db_session, slug)

@pytest.mark.asyncio
async def test_mcp_dispatch_identidad_charset_invalido_rebota(db_session):
    """
    El charset del slug se valida en el BORDE (misma familia que la forma del
    payload): mayúsculas o espacios rebota ANTES de abrir el registro, sin
    tocar la BD.
    """
    before = await _row_counts(db_session)

    response = await handle_task_tool("coreai_dispatch_task", {
        "task_type": "vectorize_event",
        "task_payload": {"content": "Despacho con slug fuera de charset."},
        "entity": "Slug-Invalido",
    })

    assert response["isError"] is True, "El slug fuera de charset debe rebotar en el borde"

    after = await _row_counts(db_session)
    assert after == before, f"El rechazo mutó la BD: antes {before}, después {after}"
