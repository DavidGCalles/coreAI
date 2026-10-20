import pytest
import json
import uuid
from sqlalchemy import select, func, update

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
