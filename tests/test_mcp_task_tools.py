import pytest
import json
import uuid
from sqlalchemy import select

from src.db.database import AsyncSessionLocal, engine
from src.db.models import Task
from src.mcp.tools.task_tools import handle_task_tool

@pytest.fixture
async def db_session():
    """Sesión de aserción (la tool gestiona su propia sesión interna)."""
    await engine.dispose()
    async with AsyncSessionLocal() as session:
        yield session
        # No hacemos rollback general aquí porque la tool ya hizo commit de su propia sesión

@pytest.mark.asyncio
async def test_mcp_dispatch_task_tool_integration(db_session):
    """
    Valida el contrato de red: simula un payload RPC crudo desde un cliente MCP 
    y aserta el formato de salida estricto.
    """
    # 1. Payload crudo simulando la entrada de un LLM
    arguments = {
        "task_type": "init_session",
        "task_payload": {"context": "Llamada desde cliente externo MCP."}
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