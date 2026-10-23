"""
Tests del ensure_collection idempotente (ADR-007-002, Issue #50).

La colección de imágenes (jina-clip-v2, 1024 dims, coseno) hasta ahora se
creaba A MANO — nada garantizaba su existencia en un despliegue limpio.
El worker la garantiza al arranque, ANTES de consumir la cola. Qdrant
mockeado: cero red, pura lógica de garantía.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from qdrant_client.http import models as qmodels

from src.repositories.vector import (
    VectorRepository,
    IMAGE_COLLECTION,
    IMAGE_VECTOR_SIZE,
    IMAGE_VECTOR_DISTANCE,
)
from src.worker import ensure_collections


def _client_with_collections(*names):
    """Cliente Qdrant mockeado que reporta estas colecciones como existentes."""
    client = AsyncMock()
    client.get_collections.return_value = SimpleNamespace(
        collections=[SimpleNamespace(name=n) for n in names]
    )
    return client


@pytest.mark.asyncio
async def test_creates_image_collection_when_missing():
    """Despliegue limpio: existe todo MENOS 'images' → se crea con los parámetros del CLIP."""
    client = _client_with_collections("system", "personal")
    repo = VectorRepository(client=client)

    await repo.ensure_collection(IMAGE_COLLECTION, IMAGE_VECTOR_SIZE, IMAGE_VECTOR_DISTANCE)

    client.create_collection.assert_awaited_once()
    kwargs = client.create_collection.call_args.kwargs
    assert kwargs["collection_name"] == IMAGE_COLLECTION == "images"
    assert kwargs["vectors_config"].size == 1024
    assert kwargs["vectors_config"].distance == qmodels.Distance.COSINE


@pytest.mark.asyncio
async def test_does_not_touch_existing_collection():
    """La colección ya existe (creada a mano o en un arranque previo) → no se toca."""
    client = _client_with_collections("system", "images")
    repo = VectorRepository(client=client)

    await repo.ensure_collection(IMAGE_COLLECTION, IMAGE_VECTOR_SIZE, IMAGE_VECTOR_DISTANCE)

    client.create_collection.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_is_idempotent_across_restarts():
    """Dos arranques seguidos → una sola creación. El arranque 100 es idéntico al arranque 1."""
    client = _client_with_collections()
    repo = VectorRepository(client=client)

    # Primer arranque: no existe → crea
    await repo.ensure_collection(IMAGE_COLLECTION, IMAGE_VECTOR_SIZE, IMAGE_VECTOR_DISTANCE)
    assert client.create_collection.await_count == 1

    # Segundo arranque: la lista de colecciones ya incluye 'images'
    client.get_collections.return_value = SimpleNamespace(
        collections=[SimpleNamespace(name="images")]
    )
    await repo.ensure_collection(IMAGE_COLLECTION, IMAGE_VECTOR_SIZE, IMAGE_VECTOR_DISTANCE)
    assert client.create_collection.await_count == 1


def test_image_collection_params_match_clip_model():
    """Guarda de coherencia: 'images', 1024 dims (jina-clip-v2), coseno — lo validado en el E2E del #61."""
    assert IMAGE_COLLECTION == "images"
    assert IMAGE_VECTOR_SIZE == 1024
    assert IMAGE_VECTOR_DISTANCE == qmodels.Distance.COSINE


@pytest.mark.asyncio
async def test_worker_boot_ensures_the_image_collection(monkeypatch):
    """El arranque del worker garantiza la colección por su nombre propio — wiring real de ensure_collections()."""
    client = _client_with_collections()
    monkeypatch.setattr("src.worker.get_qdrant_client", AsyncMock(return_value=client))

    await ensure_collections()

    client.create_collection.assert_awaited_once()
    kwargs = client.create_collection.call_args.kwargs
    assert kwargs["collection_name"] == "images"
    assert kwargs["vectors_config"].size == 1024
    assert kwargs["vectors_config"].distance == qmodels.Distance.COSINE
