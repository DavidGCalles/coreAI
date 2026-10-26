"""
Tests del arranque vectorial garantizado (ADR-007-002 #50; BUG-001 #34).

La colección de imágenes (jina-clip-v2, 1024 dims, coseno) se creaba A
MANO; las colecciones de TEXTO por dominio (multilingual-e5-small, 384)
directamente no tenían init al arranque — un despliegue limpio dejaba la
ingesta muriendo asíncrona, tarea a tarea, contra el upsert. El worker
garantiza TODA la topología ANTES de consumir la cola, y valida la de lo
preexistente: dims de otro modelo = error explícito al arranque, nunca
upserts tragados en silencio. Qdrant mockeado: cero red, pura lógica de
garantía.
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
    TEXT_VECTOR_SIZE,
    TEXT_VECTOR_DISTANCE,
)
from src.schemas.memory import DomainType
from src.worker import ensure_collections

TEXT_COLLECTIONS = [d.value for d in DomainType]  # document, conversation, system


def _collection_info(size, distance):
    """CollectionInfo de Qdrant con un vector único sin nombre (la topología de coreAI)."""
    return SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors=SimpleNamespace(size=size, distance=distance)
            )
        )
    )


def _client_with_collections(*names, size=None, distance=None, by_name=None):
    """
    Cliente Qdrant mockeado que reporta estas colecciones como existentes.

    get_collection describe la topología de lo preexistente: con by_name
    ({nombre: (size, distance)}) cada colección cuenta su configuración;
    sin él, todas reportan la topología de imagen por defecto.
    """
    client = AsyncMock()
    client.get_collections.return_value = SimpleNamespace(
        collections=[SimpleNamespace(name=n) for n in names]
    )
    if by_name is not None:

        async def _get(collection_name):
            s, d = by_name[collection_name]
            return _collection_info(s, d)

        client.get_collection.side_effect = _get
    else:
        client.get_collection.return_value = _collection_info(
            size if size is not None else IMAGE_VECTOR_SIZE,
            distance if distance is not None else IMAGE_VECTOR_DISTANCE,
        )
    return client


# =====================================================================
# PRIMITIVA: ensure_collection (creación + validación de topología)
# =====================================================================

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
async def test_existing_collection_with_matching_topology_is_not_touched():
    """
    Ya existe con la topología esperada (a mano o en un arranque previo) →
    no se toca: se CONSULTA (validación BUG-001) y no se crea nada.
    """
    client = _client_with_collections("system", "images")
    repo = VectorRepository(client=client)

    await repo.ensure_collection(IMAGE_COLLECTION, IMAGE_VECTOR_SIZE, IMAGE_VECTOR_DISTANCE)

    client.create_collection.assert_not_awaited()
    client.get_collection.assert_awaited_once_with(collection_name="images")


@pytest.mark.asyncio
async def test_ensure_is_idempotent_across_restarts():
    """Dos arranques seguidos → una sola creación. El arranque 100 es idéntico al arranque 1."""
    client = _client_with_collections()
    repo = VectorRepository(client=client)

    # Primer arranque: no existe → crea
    await repo.ensure_collection(IMAGE_COLLECTION, IMAGE_VECTOR_SIZE, IMAGE_VECTOR_DISTANCE)
    assert client.create_collection.await_count == 1

    # Segundo arranque: la lista ya incluye 'images' con topología correcta
    client.get_collections.return_value = SimpleNamespace(
        collections=[SimpleNamespace(name="images")]
    )
    await repo.ensure_collection(IMAGE_COLLECTION, IMAGE_VECTOR_SIZE, IMAGE_VECTOR_DISTANCE)
    assert client.create_collection.await_count == 1


@pytest.mark.asyncio
async def test_existing_collection_with_wrong_dims_raises():
    """
    La mitad que faltaba del BUG-001: colección preexistente con dims de
    otro modelo → ValueError explícito. NUNCA se recrea con datos dentro.
    """
    client = _client_with_collections("images", size=384)  # 'images' con dims de texto
    repo = VectorRepository(client=client)

    with pytest.raises(ValueError) as exc_info:
        await repo.ensure_collection(IMAGE_COLLECTION, IMAGE_VECTOR_SIZE, IMAGE_VECTOR_DISTANCE)

    # El diagnóstico suena: qué se encontró, qué se esperaba
    assert "384 dims" in str(exc_info.value)
    assert "1024 dims" in str(exc_info.value)
    assert "no casa" in str(exc_info.value)
    client.create_collection.assert_not_awaited()


@pytest.mark.asyncio
async def test_named_vectors_topology_raises():
    """Named vectors (dict) = otra topología: mismo error explícito, nunca recreación ciega."""
    client = _client_with_collections("images")
    client.get_collection.return_value = SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors={"clip": SimpleNamespace(size=1024, distance=qmodels.Distance.COSINE)}
            )
        )
    )
    repo = VectorRepository(client=client)

    with pytest.raises(ValueError) as exc_info:
        await repo.ensure_collection(IMAGE_COLLECTION, IMAGE_VECTOR_SIZE, IMAGE_VECTOR_DISTANCE)

    assert "named vectors" in str(exc_info.value)
    client.create_collection.assert_not_awaited()


def test_image_collection_params_match_clip_model():
    """Guarda de coherencia: 'images', 1024 dims (jina-clip-v2), coseno — lo validado en el E2E del #61."""
    assert IMAGE_COLLECTION == "images"
    assert IMAGE_VECTOR_SIZE == 1024
    assert IMAGE_VECTOR_DISTANCE == qmodels.Distance.COSINE


def test_text_collection_params_match_e5():
    """Guarda de coherencia: 384 dims (multilingual-e5-small), coseno — lo que emite el grupo 'text-embedding'."""
    assert TEXT_COLLECTIONS == ["document", "conversation", "system"]
    assert TEXT_VECTOR_SIZE == 384
    assert TEXT_VECTOR_DISTANCE == qmodels.Distance.COSINE


# =====================================================================
# WIRING DEL WORKER: ensure_collections() garantiza TODA la topología
# =====================================================================

@pytest.mark.asyncio
async def test_worker_boot_creates_all_collections_on_clean_deploy(monkeypatch):
    """
    DoD del BUG-001: tras un `docker-compose down -v` y arranque limpio, las
    4 colecciones (3 de texto por dominio + images) se auto-generan con sus
    dims correctas — cero intervención manual.
    """
    client = _client_with_collections()
    monkeypatch.setattr("src.worker.get_qdrant_client", AsyncMock(return_value=client))

    await ensure_collections()

    created = [c.kwargs["collection_name"] for c in client.create_collection.await_args_list]
    assert set(created) == set(TEXT_COLLECTIONS) | {IMAGE_COLLECTION}
    assert len(created) == 4

    for call in client.create_collection.await_args_list:
        if call.kwargs["collection_name"] in TEXT_COLLECTIONS:
            assert call.kwargs["vectors_config"].size == 384
            assert call.kwargs["vectors_config"].distance == qmodels.Distance.COSINE
        else:
            assert call.kwargs["vectors_config"].size == 1024
            assert call.kwargs["vectors_config"].distance == qmodels.Distance.COSINE


@pytest.mark.asyncio
async def test_worker_boot_idempotent_with_full_valid_topology(monkeypatch):
    """Arranque número cien = arranque número uno: todo existe y casa → cero creaciones, cero errores."""
    topology = {name: (384, qmodels.Distance.COSINE) for name in TEXT_COLLECTIONS}
    topology[IMAGE_COLLECTION] = (1024, qmodels.Distance.COSINE)
    client = _client_with_collections(*topology.keys(), by_name=topology)
    monkeypatch.setattr("src.worker.get_qdrant_client", AsyncMock(return_value=client))

    await ensure_collections()

    client.create_collection.assert_not_awaited()
    assert client.get_collection.await_count == 4  # cada preexistente se VALIDA


@pytest.mark.asyncio
async def test_worker_boot_fails_loud_on_wrong_text_dims(monkeypatch):
    """
    Topología corrupta preexistente (dims de otro modelo): el arranque
    revienta — el boot loop de worker_loop suena y reintenta con backoff,
    y NO se recrea nada. Antes del BUG-001 esto se descubría tarea a
    tarea, en el upsert, con el error tragándose los seeds.
    """
    client = _client_with_collections("document", "conversation", size=768)
    monkeypatch.setattr("src.worker.get_qdrant_client", AsyncMock(return_value=client))

    with pytest.raises(ValueError) as exc_info:
        await ensure_collections()

    assert "document" in str(exc_info.value)
    assert "768 dims" in str(exc_info.value)
    client.create_collection.assert_not_awaited()
