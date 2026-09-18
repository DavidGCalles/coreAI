"""
Tests unitarios para handle_vectorize_event (Epic 4 - Issue 4.3)

Aísla exclusivamente la lógica algorítmica del handler sin interactuar con la cola.
Valida:
- Extracción estricta y validación de tipos del payload.
- Inyección de dependencias limpia (Qdrant global).
- Delegación exitosa a HybridMemoryManager.add_memory() mediante kwargs.
"""

import pytest
import uuid
from unittest.mock import AsyncMock, patch

from src.db.models import Task, TaskStatus
from src.schemas.tasks import TaskType
from src.schemas.memory import DomainType, Visibility
from src.workers.task_router import handle_vectorize_event


# =====================================================================
# FIXTURES (Datos Sintéticos)
# =====================================================================

@pytest.fixture
def pending_vectorize_task():
    """Crea una tarea PENDING de vectorización con payload completo."""
    task_id = uuid.UUID('f47ac10b-58cc-4372-a567-0e02b2c3d479')
    target_entity_id = uuid.UUID('00000000-0000-0000-0000-000000000001')
    
    payload = {
        "task_type": TaskType.VECTORIZE_EVENT.value,
        "entity_id": str(target_entity_id),
        "content": "Memoria determinista y sin bullshit.",
        "domain": "CONVERSATION",
        "visibility": "SHARED"
    }
    
    return Task(
        id=task_id,
        session_id=uuid.uuid4(),
        status=TaskStatus.PENDING,
        payload=payload
    )


@pytest.fixture
def missing_fields_task():
    """Crea una tarea sin los campos mínimos vitales."""
    return Task(
        id=uuid.uuid4(),
        status=TaskStatus.PENDING,
        payload={
            "task_type": TaskType.VECTORIZE_EVENT.value,
            "domain": "SYSTEM"
            # Falta entity_id y content
        }
    )


@pytest.fixture
def invalid_uuid_task():
    """Crea una tarea con un UUID corrupto."""
    return Task(
        id=uuid.uuid4(),
        status=TaskStatus.PENDING,
        payload={
            "task_type": TaskType.VECTORIZE_EVENT.value,
            "entity_id": "esto-no-es-un-uuid",
            "content": "Test de fallo"
        }
    )


# =====================================================================
# TESTS
# =====================================================================

@pytest.mark.asyncio
# Parcheamos las dependencias externas en la ruta exacta donde las importa el handler
@patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
@patch("src.workers.task_router.LLMClient")
@patch("src.workers.task_router.HybridMemoryManager")
async def test_handle_vectorize_event_happy_path(
    mock_manager_class, 
    mock_llm_client_class, 
    mock_get_qdrant, 
    pending_vectorize_task
):
    """
    Test de comportamiento (Happy Path): 
    Valida que las dependencias se inyectan correctamente y la tarea se delega al Manager.
    """
    # 1. Setup de los Mocks
    session_mock = AsyncMock()
    mock_manager_instance = AsyncMock()
    mock_manager_class.return_value = mock_manager_instance
    
    # 2. Ejecución
    await handle_vectorize_event(pending_vectorize_task, session_mock)
    
    # 3. Validaciones
    # a. Verificamos que se solicitó la conexión global de Qdrant
    mock_get_qdrant.assert_awaited_once()
    
    # b. Verificamos la llamada exacta al mánager transaccional (con KWARGS)
    mock_manager_instance.add_memory.assert_awaited_once_with(
        entity_id=uuid.UUID('00000000-0000-0000-0000-000000000001'),
        domain=DomainType.CONVERSATION,
        content="Memoria determinista y sin bullshit.",
        visibility=Visibility.SHARED
    )


@pytest.mark.asyncio
async def test_handle_vectorize_event_raises_on_missing_fields(missing_fields_task):
    """
    Test de validación temprana: 
    Falla antes de tocar la base de datos si el payload está incompleto.
    """
    session_mock = AsyncMock()
    
    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_event(missing_fields_task, session_mock)
    
    assert "Faltan campos esenciales" in str(exc_info.value)
    assert str(missing_fields_task.id) in str(exc_info.value)


@pytest.mark.asyncio
async def test_handle_vectorize_event_raises_on_invalid_uuid(invalid_uuid_task):
    """
    Test de validación de tipos: 
    Evita que un string corrupto reviente SQLAlchemy más adelante.
    """
    session_mock = AsyncMock()
    
    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_event(invalid_uuid_task, session_mock)
    
    assert "debe ser un UUID válido" in str(exc_info.value)
    assert str(invalid_uuid_task.id) in str(exc_info.value)