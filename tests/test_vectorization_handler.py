"""
Tests unitarios para handle_vectorize_event (Epic 4 - Issue 4.3)

Aísla exclusivamente la lógica algorítmica del handler sin interactuar con la cola.
Valida:
- Extracción de jerarquía relacional (Task -> Session -> Entity) con mocks síncronos/asíncronos.
- Búsqueda resiliente de claves de contenido.
- Inyección de dependencias limpia (Qdrant global).
- Delegación exitosa a HybridMemoryManager.add_memory() mediante kwargs.
"""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from src.db.models import Task, TaskStatus
from src.schemas.tasks import TaskType
from src.schemas.memory import DomainType, Visibility
from src.workers.task_router import handle_vectorize_event


# =====================================================================
# FIXTURES (Datos Sintéticos)
# =====================================================================

@pytest.fixture
def pending_vectorize_task():
    """Crea una tarea PENDING simulando la entrada de un LLM (usando summary_content)."""
    return Task(
        id=uuid.UUID('f47ac10b-58cc-4372-a567-0e02b2c3d479'),
        session_id=uuid.UUID('11111111-1111-1111-1111-111111111111'),
        status=TaskStatus.PENDING,
        payload={
            "task_type": TaskType.VECTORIZE_EVENT.value,
            "summary_content": "Memoria determinista y resiliente.",
            "domain": "SYSTEM",
            "visibility": "PRIVATE"
        }
    )


@pytest.fixture
def missing_content_task():
    """Crea una tarea cuyo payload no tiene ningún campo de texto válido."""
    return Task(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        status=TaskStatus.PENDING,
        payload={
            "task_type": TaskType.VECTORIZE_EVENT.value,
            "domain": "SYSTEM",
            "una_clave_inventada": "Esto debería fallar"
        }
    )


# =====================================================================
# TESTS
# =====================================================================

@pytest.mark.asyncio
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
    Valida la extracción relacional superando la trampa asíncrona/síncrona de SQLAlchemy.
    """
    # 1. Setup de los Mocks
    session_mock = AsyncMock()
    
    # Simular la query a PostgreSQL: select(Session).where(...)
    mock_db_session = MagicMock()
    mock_db_session.entity_id = uuid.UUID('99999999-9999-9999-9999-999999999999')
    
    # scalar_one_or_none es síncrono, por lo que usamos MagicMock en lugar de AsyncMock
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_db_session
    session_mock.execute.return_value = mock_result

    mock_manager_instance = AsyncMock()
    mock_manager_class.return_value = mock_manager_instance
    
    # 2. Ejecución
    await handle_vectorize_event(pending_vectorize_task, session_mock)
    
    # 3. Validaciones
    mock_get_qdrant.assert_awaited_once()
    
    mock_manager_instance.add_memory.assert_awaited_once_with(
        entity_id=uuid.UUID('99999999-9999-9999-9999-999999999999'),
        domain=DomainType.SYSTEM,
        content="Memoria determinista y resiliente.",
        visibility=Visibility.PRIVATE
    )


@pytest.mark.asyncio
async def test_handle_vectorize_event_raises_on_missing_session(pending_vectorize_task):
    """
    Test relacional: Si la sesión no existe en la BD, aborta violentamente.
    """
    session_mock = AsyncMock()
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session_mock.execute.return_value = mock_result
    
    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_event(pending_vectorize_task, session_mock)
    
    assert "Inconsistencia relacional fatal" in str(exc_info.value)
    assert str(pending_vectorize_task.session_id) in str(exc_info.value)


@pytest.mark.asyncio
async def test_handle_vectorize_event_raises_on_missing_content(missing_content_task):
    """
    Test de resiliencia de payload: Si no hay texto, no hay vector.
    """
    session_mock = AsyncMock()
    
    mock_db_session = MagicMock()
    mock_db_session.entity_id = uuid.uuid4()
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_db_session
    session_mock.execute.return_value = mock_result
    
    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_event(missing_content_task, session_mock)
    
    assert "No se encontró texto para vectorizar" in str(exc_info.value)