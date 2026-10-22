"""
Tests del init idempotente de la colección de imágenes (ADR-007-002 #50).

El ensure corre en el ARRANQUE del worker: un despliegue limpio garantiza la
colección 'images' (1024 dims jina-clip-v2, espacio propio separado de las
colecciones de texto) sin creación manual.
"""

import pytest
from unittest.mock import AsyncMock, patch

from qdrant_client import models as qmodels

from src.db.vector_db import (
    ensure_image_collection,
    IMAGE_COLLECTION,
    IMAGE_VECTOR_SIZE,
)
from src.worker import worker_loop


class _StopLoop(BaseException):
    """
    Centinela que atraviesa los 'except Exception' del bucle del worker —
    mismo mecanismo de escape que KeyboardInterrupt en producción. Permite
    abortar worker_loop de forma determinista en test.
    """


# ---------------------------------------------------------------------
# Unitarios del ensure
# ---------------------------------------------------------------------

@pytest.mark.asyncio
@patch("src.db.vector_db.get_qdrant_client", new_callable=AsyncMock)
async def test_ensure_no_op_si_la_coleccion_existe(mock_get_client):
    """Idempotencia pura: existe → no-op, NUNCA se toca create_collection."""
    client = AsyncMock()
    mock_get_client.return_value = client

    created = await ensure_image_collection()

    assert created is False
    client.get_collection.assert_awaited_once_with(collection_name=IMAGE_COLLECTION)
    client.create_collection.assert_not_awaited()


@pytest.mark.asyncio
@patch("src.db.vector_db.get_qdrant_client", new_callable=AsyncMock)
async def test_ensure_crea_con_dim_1024_cosine_si_falta(mock_get_client):
    """Despliegue limpio: la colección nace con la dim de jina-clip-v2 y COSINE."""
    client = AsyncMock()
    client.get_collection.side_effect = Exception("Collection not found")
    mock_get_client.return_value = client

    created = await ensure_image_collection()

    assert created is True
    client.create_collection.assert_awaited_once()
    kwargs = client.create_collection.call_args.kwargs
    assert kwargs["collection_name"] == IMAGE_COLLECTION == "images"
    vectors = kwargs["vectors_config"]
    assert isinstance(vectors, qmodels.VectorParams)
    assert vectors.size == IMAGE_VECTOR_SIZE == 1024
    assert vectors.distance == qmodels.Distance.COSINE


@pytest.mark.asyncio
@patch("src.db.vector_db.get_qdrant_client", new_callable=AsyncMock)
async def test_ensure_fail_fast_si_qdrant_no_responde(mock_get_client):
    """
    Qdrant caído al arranque → el error PROPAGA: el worker no levanta
    degradado — descubre la falta en plena tarea.
    """
    client = AsyncMock()
    client.get_collection.side_effect = ConnectionError("Qdrant caído")
    client.create_collection.side_effect = ConnectionError("Qdrant caído")
    mock_get_client.return_value = client

    with pytest.raises(ConnectionError):
        await ensure_image_collection()


# ---------------------------------------------------------------------
# Cableado del worker
# ---------------------------------------------------------------------

@pytest.mark.asyncio
@patch("src.worker.AsyncSessionLocal")
@patch("src.worker.ensure_image_collection", new_callable=AsyncMock)
async def test_worker_garantiza_coleccion_antes_del_bucle(mock_ensure, mock_session_cls):
    """
    El arranque del worker corre el ensure ANTES de tocar la cola: sin
    colección garantizada no hay bucle.
    """
    # Aborta la primera iteración del bucle (BaseException atraviesa los
    # except Exception del worker — igual que KeyboardInterrupt en vivo).
    mock_session_cls.side_effect = _StopLoop

    with pytest.raises(_StopLoop):
        await worker_loop()

    mock_ensure.assert_awaited_once()
