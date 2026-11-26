"""
Tests unitarios para handle_vectorize_event (Epic 4 - Issue 4.3)

Aísla exclusivamente la lógica algorítmica del handler sin interactuar con la cola.
Valida:
- Extracción de jerarquía relacional (Task -> Session -> Entity) con mocks síncronos/asíncronos.
- Extracción estricta por contrato: solo 'content' (ADR-006 — purga de la extracción tolerante).
- Inyección de dependencias limpia (Qdrant global).
- Delegación exitosa a HybridMemoryManager.add_memory() mediante kwargs.
- Propagación de 'project' (FR-3, #51): del payload del contrato al payload
  de Qdrant vía add_memory — y su default honesto (None) cuando falta.
- Titularidad por registro en la escritura ([ADR-012-004] #115): el owner del
  punto es el SLUG de la entidad titular (despachante desde el #113, sesión
  como fallback de legado), resuelto con join contra entities.
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
    """Crea una tarea PENDING con payload normalizado por la frontera (clave 'content')."""
    return Task(
        id=uuid.UUID('f47ac10b-58cc-4372-a567-0e02b2c3d479'),
        session_id=uuid.UUID('11111111-1111-1111-1111-111111111111'),
        status=TaskStatus.PENDING,
        payload={
            "task_type": TaskType.VECTORIZE_EVENT.value,
            "content": "Memoria determinista y resiliente.",
            "domain": "SYSTEM",
            "visibility": "PRIVATE",
            "project": "elios-workshop"
        }
    )


@pytest.fixture
def pending_vectorize_task_sin_project():
    """Crea una tarea PENDING sin 'project': clave opcional del contrato."""
    return Task(
        id=uuid.UUID('f47ac10b-58cc-4372-a567-0e02b2c3d479'),
        session_id=uuid.UUID('11111111-1111-1111-1111-111111111111'),
        status=TaskStatus.PENDING,
        payload={
            "task_type": TaskType.VECTORIZE_EVENT.value,
            "content": "Memoria determinista y resiliente.",
            "domain": "SYSTEM",
            "visibility": "PRIVATE"
        }
    )


@pytest.fixture
def pending_vectorize_task_titular():
    """
    Task post-familia ([ADR-012-002] #113): nace TITULADA al despachante,
    una entidad distinta de la dueña de la sesión — la titularidad es del
    despacho, no de la sesión.
    """
    return Task(
        id=uuid.UUID('f47ac10b-58cc-4372-a567-0e02b2c3d479'),
        entity_id=uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc'),
        session_id=uuid.UUID('11111111-1111-1111-1111-111111111111'),
        status=TaskStatus.PENDING,
        payload={
            "task_type": TaskType.VECTORIZE_EVENT.value,
            "content": "Memoria titulada al despachante.",
            "domain": "SYSTEM",
            "visibility": "PRIVATE",
            "project": "elios-workshop"
        }
    )


@pytest.fixture
def missing_content_task():
    """Crea una tarea cuyo payload no trae 'content': solo corrupción de cola."""
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
@patch("src.workers.task_router.EntityRepository")
@patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
@patch("src.workers.task_router.LLMClient")
@patch("src.workers.task_router.HybridMemoryManager")
async def test_handle_vectorize_event_happy_path(
    mock_manager_class, 
    mock_llm_client_class, 
    mock_get_qdrant, 
    mock_entity_repo_class,
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

    # [ADR-012-004] #115: el join contra entities devuelve el slug legible
    mock_entity_repo_class.return_value.get_slug = AsyncMock(
        return_value="lane-de-test"
    )
    
    # 2. Ejecución
    await handle_vectorize_event(pending_vectorize_task, session_mock)
    
    # 3. Validaciones
    mock_get_qdrant.assert_awaited_once()

    # Fallback de legado: la task PRE-familia no trae columna estampada →
    # titularidad por sesión; el slug se resuelve del titular así resuelto
    mock_entity_repo_class.return_value.get_slug.assert_awaited_once_with(
        uuid.UUID('99999999-9999-9999-9999-999999999999')
    )
    mock_manager_instance.add_memory.assert_awaited_once_with(
        entity_id=uuid.UUID('99999999-9999-9999-9999-999999999999'),
        domain=DomainType.SYSTEM,
        content="Memoria determinista y resiliente.",
        visibility=Visibility.PRIVATE,
        project="elios-workshop",
        owner="lane-de-test"
    )


@pytest.mark.asyncio
@patch("src.workers.task_router.EntityRepository")
@patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
@patch("src.workers.task_router.LLMClient")
@patch("src.workers.task_router.HybridMemoryManager")
async def test_handle_vectorize_event_titularidad_por_despacho_no_por_sesion(
    mock_manager_class,
    mock_llm_client_class,
    mock_get_qdrant,
    mock_entity_repo_class,
    pending_vectorize_task_titular
):
    """
    [ADR-012-004] #115: con columna estampada (#113), la titularidad de la
    escritura es el DESPACHANTE (task.entity_id), aunque la sesión viva a
    nombre de otra entidad — la titularidad es del despacho, no de la
    sesión. El slug se resuelve del titular, y a él se titula la memoria.
    """
    session_mock = AsyncMock()

    # La sesión pertenece a OTRA entidad: no manda en la titularidad
    mock_db_session = MagicMock()
    mock_db_session.entity_id = uuid.UUID('99999999-9999-9999-9999-999999999999')

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_db_session
    session_mock.execute.return_value = mock_result

    mock_manager_instance = AsyncMock()
    mock_manager_class.return_value = mock_manager_instance

    mock_entity_repo_class.return_value.get_slug = AsyncMock(
        return_value="frontier-david"
    )

    await handle_vectorize_event(pending_vectorize_task_titular, session_mock)

    # El slug sale del DESPACHANTE, no del dueño de la sesión
    mock_entity_repo_class.return_value.get_slug.assert_awaited_once_with(
        uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
    )
    mock_manager_instance.add_memory.assert_awaited_once_with(
        entity_id=uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc'),
        domain=DomainType.SYSTEM,
        content="Memoria titulada al despachante.",
        visibility=Visibility.PRIVATE,
        project="elios-workshop",
        owner="frontier-david"
    )


@pytest.mark.asyncio
@patch("src.workers.task_router.EntityRepository")
@patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
@patch("src.workers.task_router.LLMClient")
@patch("src.workers.task_router.HybridMemoryManager")
async def test_handle_vectorize_event_entidad_legada_sin_slug_owner_none(
    mock_manager_class,
    mock_llm_client_class,
    mock_get_qdrant,
    mock_entity_repo_class,
    pending_vectorize_task
):
    """
    [ADR-012-004] #115: entidad histórica sin slug (pre-ADR-012-001): el
    punto nace con owner=None — honesto, sin inventar trazabilidad — y el
    backfill (#116) lo rellena. Nada revienta por ello.
    """
    session_mock = AsyncMock()

    mock_db_session = MagicMock()
    mock_db_session.entity_id = uuid.UUID('99999999-9999-9999-9999-999999999999')

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_db_session
    session_mock.execute.return_value = mock_result

    mock_manager_instance = AsyncMock()
    mock_manager_class.return_value = mock_manager_instance

    mock_entity_repo_class.return_value.get_slug = AsyncMock(return_value=None)

    await handle_vectorize_event(pending_vectorize_task, session_mock)

    mock_manager_instance.add_memory.assert_awaited_once_with(
        entity_id=uuid.UUID('99999999-9999-9999-9999-999999999999'),
        domain=DomainType.SYSTEM,
        content="Memoria determinista y resiliente.",
        visibility=Visibility.PRIVATE,
        project="elios-workshop",
        owner=None
    )


@pytest.mark.asyncio
@patch("src.workers.task_router.EntityRepository")
@patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
@patch("src.workers.task_router.LLMClient")
@patch("src.workers.task_router.HybridMemoryManager")
async def test_handle_vectorize_event_project_default_none(
    mock_manager_class,
    mock_llm_client_class,
    mock_get_qdrant,
    mock_entity_repo_class,
    pending_vectorize_task_sin_project
):
    """
    Test de propagación de 'project' (FR-3, #51): el contrato lo declara
    OPCIONAL — un payload sin 'project' NO es corrupción de cola. El
    handler debe pasarlo como None explícito, no inventarlo: los puntos
    de texto sin proyecto son simplemente no-filtrables por él.
    """
    session_mock = AsyncMock()

    mock_db_session = MagicMock()
    mock_db_session.entity_id = uuid.UUID('99999999-9999-9999-9999-999999999999')

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_db_session
    session_mock.execute.return_value = mock_result

    mock_manager_instance = AsyncMock()
    mock_manager_class.return_value = mock_manager_instance

    mock_entity_repo_class.return_value.get_slug = AsyncMock(
        return_value="lane-de-test"
    )

    await handle_vectorize_event(pending_vectorize_task_sin_project, session_mock)

    mock_manager_instance.add_memory.assert_awaited_once_with(
        entity_id=uuid.UUID('99999999-9999-9999-9999-999999999999'),
        domain=DomainType.SYSTEM,
        content="Memoria determinista y resiliente.",
        visibility=Visibility.PRIVATE,
        project=None,
        owner="lane-de-test"
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
    Test de extracción estricta: si la clave 'content' del contrato no está,
    no hay vector — y el error lo dice sin buscar claves alternativas.
    """
    session_mock = AsyncMock()
    
    mock_db_session = MagicMock()
    mock_db_session.entity_id = uuid.uuid4()
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_db_session
    session_mock.execute.return_value = mock_result
    
    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_event(missing_content_task, session_mock)
    
    assert "no trae 'content'" in str(exc_info.value)
    assert "corrupción de cola" in str(exc_info.value)
