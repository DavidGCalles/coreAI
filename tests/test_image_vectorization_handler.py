"""
Tests unitarios para handle_vectorize_image_event (ADR-007-003, Issue #61).

Aísla exclusivamente la lógica algorítmica del handler sin interactuar con la
cola (criterio de aceptación del 4.3): gateway LiteLLM y Qdrant mockeados.
Valida:
- Coreografía de la regla 2 del bypass cercado: el registro textual
  referenciado debe existir, ser vectorize_event y estar COMPLETED
  (write-then-read) — tres fallos distintos con error_trace legible.
- El embedding se pide al GATEWAY (entrada image-embedding + modality=image),
  nunca a Infinity directo (BUG-004 #58).
- El vector aterriza en la colección de imágenes: el punto ES la tarea de
  imagen; la imagen NO se persiste; el payload referencia text_event_id.
- Telemetría simétrica con el flujo textual: factura de tokens en llm_audit.
- Fallos legítimos: gateway caído (rollback + re-raise), embedding vacío,
  corrupción de cola (sin extracción tolerante — ADR-006).
"""

import uuid

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.db.models import Task, TaskStatus, LLMAudit
from src.schemas.tasks import TaskType
from src.schemas.llm import EmbeddingResponse
from src.workers.task_router import (
    handle_vectorize_image_event,
    IMAGE_EMBEDDING_MODEL,
)
from src.repositories.vector import VectorRepository, IMAGE_COLLECTION

# 1x1 JPEG real en base64: mínimo viable que satisface el patrón data URI del contrato.
VALID_IMAGE_DATA_URI = (
    "data:image/jpeg;base64,"
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjI"
    "yMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/"
    "8QAFQABAQAAAAAAAAAAAAAAAAAAAAP/xAAUEAEAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAA"
    "AAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k="
)

TASK_ID = uuid.UUID("a47ac10b-58cc-4372-a567-0e02b2c3d479")
SESSION_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
ENTITY_ID = uuid.UUID("99999999-9999-9999-9999-999999999999")
TEXT_EVENT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


# =====================================================================
# HELPERS (Datos Sintéticos)
# =====================================================================

def _session_result(value):
    """Resultado síncrono de SQLAlchemy (scalar_one_or_none es síncrono)."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _db_session():
    db_session = MagicMock()
    db_session.entity_id = ENTITY_ID
    return db_session


def _text_task(status=TaskStatus.COMPLETED, task_type=TaskType.VECTORIZE_EVENT.value):
    return Task(
        id=TEXT_EVENT_ID,
        session_id=SESSION_ID,
        status=status,
        payload={"task_type": task_type, "content": "Pieza del catálogo: tornillo M6."},
    )


def _image_task():
    return Task(
        id=TASK_ID,
        session_id=SESSION_ID,
        status=TaskStatus.RUNNING,
        payload={
            "task_type": TaskType.VECTORIZE_IMAGE_EVENT.value,
            "image": VALID_IMAGE_DATA_URI,
            "text_event_id": str(TEXT_EVENT_ID),
            "domain": "system",
            "visibility": "private",
        },
    )


def _gateway_response(embedding=(0.115, 0.224, -0.332)):
    return EmbeddingResponse.model_validate({
        "model": "infinity/data/clip_model",
        "data": [{"embedding": list(embedding), "index": 0}],
        "usage": {"prompt_tokens": 2429, "total_tokens": 2429},
    })


def _session_mock(*execute_results):
    session_mock = AsyncMock()
    session_mock.add = MagicMock()
    session_mock.flush = AsyncMock()
    session_mock.rollback = AsyncMock()
    session_mock.execute.side_effect = list(execute_results)
    return session_mock


# =====================================================================
# TESTS: HAPPY PATH
# =====================================================================

@pytest.mark.asyncio
@patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
@patch("src.workers.task_router.LLMClient")
async def test_happy_path(mock_llm_client_class, mock_get_qdrant):
    """
    Texto COMPLETED → imagen vectorizada vía gateway → punto en la colección
    de imágenes referenciando el registro textual. Nada más, nada menos.
    """
    session_mock = _session_mock(
        _session_result(_db_session()),
        _session_result(_text_task()),
    )
    task = _image_task()

    qdrant_mock = AsyncMock()
    mock_get_qdrant.return_value = qdrant_mock

    llm_instance = AsyncMock()
    llm_instance.generate_embedding.return_value = _gateway_response()
    mock_llm_client_class.return_value = llm_instance

    await handle_vectorize_image_event(task, session_mock)

    # 1. Resolución relacional: sesión + registro textual (dos SELECT)
    assert session_mock.execute.await_count == 2

    # 2. El embedding se pide al GATEWAY: entrada image-embedding + modality (BUG-004 #58)
    llm_instance.generate_embedding.assert_awaited_once()
    request = llm_instance.generate_embedding.call_args[0][0]
    assert request.model == IMAGE_EMBEDDING_MODEL == "image-embedding"
    assert request.input == VALID_IMAGE_DATA_URI
    assert request.modality == "image"

    # 3. El vector aterriza en la colección de imágenes; el punto ES la tarea
    qdrant_mock.upsert.assert_awaited_once()
    kwargs = qdrant_mock.upsert.call_args.kwargs
    assert kwargs["collection_name"] == IMAGE_COLLECTION
    point = kwargs["points"][0]
    assert point.id == str(TASK_ID)
    assert point.vector == [0.115, 0.224, -0.332]
    # La imagen NO se persiste: el vector es dato derivado del registro textual
    assert "image" not in point.payload
    assert point.payload["tenant_id"] == str(ENTITY_ID)
    assert point.payload["text_event_id"] == str(TEXT_EVENT_ID)
    assert point.payload["domain"] == "system"
    assert point.payload["visibility"] == "private"

    # 4. Telemetría simétrica con el flujo textual: factura en llm_audit
    session_mock.add.assert_called_once()
    audit_row = session_mock.add.call_args[0][0]
    assert isinstance(audit_row, LLMAudit)
    assert audit_row.model_used == "infinity/data/clip_model"
    assert audit_row.prompt_tokens == 2429
    assert audit_row.total_tokens == 2429

    # 5. Sin rollback: transacción limpia
    session_mock.rollback.assert_not_awaited()


# =====================================================================
# TESTS: COREOGRAFÍA (regla 2 del bypass cercado)
# =====================================================================

@pytest.mark.asyncio
async def test_missing_session_aborts():
    session_mock = _session_mock(_session_result(None))
    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_image_event(_image_task(), session_mock)
    assert "Inconsistencia relacional fatal" in str(exc_info.value)
    assert str(SESSION_ID) in str(exc_info.value)


@pytest.mark.asyncio
async def test_orphan_text_reference_aborts():
    """Sin registro textual no hay vector: sería huérfano (regla 2 del bypass)."""
    session_mock = _session_mock(
        _session_result(_db_session()),
        _session_result(None),
    )
    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_image_event(_image_task(), session_mock)
    assert "huérfano" in str(exc_info.value)
    assert str(TEXT_EVENT_ID) in str(exc_info.value)


@pytest.mark.asyncio
async def test_reference_of_wrong_type_aborts():
    """text_event_id que apunta a otra clase de tarea no ancla nada."""
    session_mock = _session_mock(
        _session_result(_db_session()),
        _session_result(_text_task(task_type=TaskType.CONSOLIDATE_MEMORY.value)),
    )
    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_image_event(_image_task(), session_mock)
    assert "NO es vectorize_event" in str(exc_info.value)


@pytest.mark.asyncio
async def test_uncompleted_text_reference_aborts():
    """Write-then-read: la imagen llega antes de que el texto esté COMPLETED."""
    session_mock = _session_mock(
        _session_result(_db_session()),
        _session_result(_text_task(status=TaskStatus.PENDING)),
    )
    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_image_event(_image_task(), session_mock)
    assert "COMPLETED" in str(exc_info.value)
    assert "write-then-read" in str(exc_info.value)


# =====================================================================
# TESTS: FALLOS LEGÍTIMOS Y CORRUPCIÓN DE COLA
# =====================================================================

@pytest.mark.asyncio
async def test_corrupt_payload_aborts_without_tolerant_extraction():
    """La frontera ya validó: una clave ausente solo puede ser corrupción de cola."""
    task = _image_task()
    task.payload.pop("image")
    session_mock = _session_mock(_session_result(_db_session()))

    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_image_event(task, session_mock)
    assert "no cumple el contrato" in str(exc_info.value)


@pytest.mark.asyncio
@patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
@patch("src.workers.task_router.LLMClient")
async def test_gateway_down_rolls_back_and_reraises(mock_llm_client_class, mock_get_qdrant):
    """Cortafuegos del flujo textual: nada a medias queda en la transacción."""
    session_mock = _session_mock(
        _session_result(_db_session()),
        _session_result(_text_task()),
    )
    llm_instance = AsyncMock()
    llm_instance.generate_embedding.side_effect = httpx.RequestError("gateway caído")
    mock_llm_client_class.return_value = llm_instance

    with pytest.raises(httpx.RequestError):
        await handle_vectorize_image_event(_image_task(), session_mock)

    session_mock.rollback.assert_awaited_once()
    session_mock.add.assert_not_called()
    mock_get_qdrant.return_value.upsert.assert_not_awaited()


@pytest.mark.asyncio
@patch("src.workers.task_router.get_qdrant_client", new_callable=AsyncMock)
@patch("src.workers.task_router.LLMClient")
async def test_empty_embedding_is_legible_failure(mock_llm_client_class, mock_get_qdrant):
    """Imagen ilegible/corrupta para el modelo de visión: error legible, sin factura."""
    session_mock = _session_mock(
        _session_result(_db_session()),
        _session_result(_text_task()),
    )
    llm_instance = AsyncMock()
    llm_instance.generate_embedding.return_value = _gateway_response(embedding=())
    mock_llm_client_class.return_value = llm_instance

    with pytest.raises(ValueError) as exc_info:
        await handle_vectorize_image_event(_image_task(), session_mock)
    assert "embedding vacío" in str(exc_info.value)
    # No se facturan tokens de un embed que no existe
    session_mock.add.assert_not_called()
    mock_get_qdrant.return_value.upsert.assert_not_awaited()


# =====================================================================
# TESTS: SEAM DE PERSISTENCIA (repositorio vectorial mudo)
# =====================================================================

@pytest.mark.asyncio
async def test_upsert_image_targets_the_image_collection():
    """La colección de imágenes es espacio propio (#50): nombre único, punto por UUID."""
    client = AsyncMock()
    repo = VectorRepository(client=client)
    point_id = uuid.uuid4()

    returned = await repo.upsert_image(
        point_id=point_id,
        vector=[0.1, 0.2],
        payload={"tenant_id": "t", "text_event_id": str(TEXT_EVENT_ID)},
    )

    assert returned == str(point_id)
    client.upsert.assert_awaited_once()
    kwargs = client.upsert.call_args.kwargs
    assert kwargs["collection_name"] == IMAGE_COLLECTION == "images"
    assert kwargs["points"][0].id == str(point_id)
