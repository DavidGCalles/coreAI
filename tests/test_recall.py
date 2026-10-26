"""
Tests unitarios del recall síncrono read-only (ADR-005-002, #37).

Dos capas, sin BD real: la política (HybridMemoryManager.recall_text /
recall_image, con gateway y Qdrant mockeados) y la capa fina MCP
(handle_recall_tool, con el manager mockeado).

Decisiones que estos tests defienden (26 oct):
- Sweep por el catálogo DomainType + merge global por score + top_k tras
  el merge: el ranking es de la consulta, no de la colección.
- Colección inexistente = MissingCollectionError EXPLÍCITO y ANTES de
  embeber: un fallo de inicialización no se disfraza de no-match honesto.
- Dif-cero: el recall no escribe NADA — ni factura en llm_audit, ni
  commit, ni upsert. La consulta (texto o imagen) es embed efímero.
- Simetría de superficie: project, top_k y umbral viajan igual en texto e
  imagen; sin filtro de tenant (identidad multi-tenant diferida).
- La tool valida la forma en el borde, ANTES de abrir sesión.
- DoD: tools/list son EXACTAMENTE las dos de tasks + las dos de recall.
"""

import base64
import io
import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image

from src.schemas.llm import (
    EmbeddingResponse,
    IMAGE_EMBEDDING_MODEL,
    TEXT_EMBEDDING_MODEL,
)
from src.schemas.memory import DomainType
from src.schemas.recall import MemoryHit, RecallResponse
from src.repositories.vector import IMAGE_COLLECTION
from src.managers.memory_manager import HybridMemoryManager, MissingCollectionError
from src.mcp.tools.recall_tools import get_recall_tools, handle_recall_tool
from src.mcp.tools.task_tools import get_task_tools

def _data_uri_jpeg_real() -> str:
    """JPEG real generado con PIL — el mismo mínimo viable del contrato de
    imagen (patrón de test_contract_registry): la frontera exige imagen
    decodificable y un base64 artesanal ya no pasa. El fixture original de
    este archivo tenía el cuerpo base64 corrupto (403 caracteres, padding
    inválido) y rebotaba en la validación de RecallImageRequest."""
    imagen = Image.new("RGB", (2, 2), (255, 0, 0))
    buffer = io.BytesIO()
    imagen.save(buffer, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


VALID_IMAGE_DATA_URI = _data_uri_jpeg_real()
DOC_POINT_ID = "11111111-1111-1111-1111-111111111111"
CONV_POINT_ID = "22222222-2222-2222-2222-222222222222"
SYS_POINT_ID = "33333333-3333-3333-3333-333333333333"
IMG_POINT_ID = "44444444-4444-4444-4444-444444444444"


# =====================================================================
# HELPERS (Datos Sintéticos)
# =====================================================================

def _scored_point(point_id, score, payload):
    """ScoredPoint crudo de Qdrant: id, score y payload plano."""
    point = MagicMock()
    point.id = point_id
    point.score = score
    point.payload = payload
    return point


def _embedding_response(embedding=(0.115, 0.224, -0.332), model="text-embedding"):
    return EmbeddingResponse.model_validate({
        "model": model,
        "data": [{"embedding": list(embedding), "index": 0}],
        "usage": {"prompt_tokens": 8, "total_tokens": 8},
    })


def _manager(vector_repo, llm_response):
    """Manager con todos los collaborators mockeados: política pura, sin BD."""
    session = AsyncMock()
    session.add = MagicMock()
    audit_repo = AsyncMock()
    llm = AsyncMock()
    llm.generate_embedding.return_value = llm_response
    manager = HybridMemoryManager(
        session=session,
        relational_repo=MagicMock(),
        vector_repo=vector_repo,
        audit_repo=audit_repo,
        llm_client=llm,
    )
    return manager, session, audit_repo, llm


def _assert_dif_cero(session, audit_repo, vector_repo):
    """DoD del recall read-only: NI UNA escritura cruza la frontera."""
    audit_repo.create.assert_not_awaited()
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.flush.assert_not_awaited()
    vector_repo.upsert.assert_not_awaited()


class _SessionCM:
    """Stand-in del async context manager de AsyncSessionLocal."""
    def __init__(self):
        self.session = AsyncMock()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc_info):
        return False


# =====================================================================
# CAPA DE POLÍTICA: recall_text (sweep + merge + top_k)
# =====================================================================

@pytest.mark.asyncio
async def test_recall_text_sweep_merge_y_top_k():
    """
    Sweep completo por el catálogo DomainType, merge global por score y
    top_k DESPUÉS del merge: el tercer hit (0.81) cae aunque su colección
    devolviera su propio límite.
    """
    vector_repo = AsyncMock()
    vector_repo.collection_names.return_value = {"document", "conversation", "system"}
    vector_repo.search.side_effect = [
        [_scored_point(DOC_POINT_ID, 0.93, {"content": "hit de documento", "project": "elios-workshop"})],
        [_scored_point(CONV_POINT_ID, 0.88, {"content": "hit de conversación", "session_id": "sesion-1"})],
        [_scored_point(SYS_POINT_ID, 0.81, {"content": "hit de sistema"})],
    ]
    manager, session, audit_repo, llm = _manager(vector_repo, _embedding_response())

    hits = await manager.recall_text(
        query="¿qué piezas se registraron?",
        project="elios-workshop",
        top_k=2,
        score_threshold=0.7,
    )

    # 1. Sweep: una búsqueda por cada colección del catálogo, en orden
    assert vector_repo.search.await_count == 3
    dominios = [call.kwargs["domain"] for call in vector_repo.search.call_args_list]
    assert dominios == [DomainType.DOCUMENT, DomainType.CONVERSATION, DomainType.SYSTEM]

    # 2. Filtros planos: project opcional + el top_k como limit de cada
    #    búsqueda parcial
    filters = vector_repo.search.call_args_list[0].kwargs["filters"]
    assert filters.tenant_id is None  # diferimiento defendido: sin filtro de tenant
    assert filters.project == "elios-workshop"
    assert filters.limit == 2
    assert filters.score_threshold == 0.7

    # 3. Merge global por score, top_k tras el merge; cada hit lleva su dominio
    assert len(hits) == 2
    assert [hit.score for hit in hits] == [0.93, 0.88]
    assert [hit.domain for hit in hits] == ["document", "conversation"]
    assert hits[0].point_id == DOC_POINT_ID
    assert hits[0].content == "hit de documento"
    assert hits[0].project == "elios-workshop"
    assert hits[1].session_id == "sesion-1"

    # 4. Embed EFÍMERO de consulta: grupo de texto, input = la query
    llm.generate_embedding.assert_awaited_once()
    request = llm.generate_embedding.call_args[0][0]
    assert request.model == TEXT_EMBEDDING_MODEL == "text-embedding"
    assert request.input == "¿qué piezas se registraron?"
    assert request.modality is None

    _assert_dif_cero(session, audit_repo, vector_repo)


@pytest.mark.asyncio
async def test_recall_text_no_match_honesto():
    """Tres colecciones vacías a este umbral = lista vacía, sin drama."""
    vector_repo = AsyncMock()
    vector_repo.collection_names.return_value = {"document", "conversation", "system"}
    vector_repo.search.return_value = []
    manager, session, audit_repo, llm = _manager(vector_repo, _embedding_response())

    hits = await manager.recall_text(query="nada que ver")

    assert hits == []
    assert vector_repo.search.await_count == 3
    _assert_dif_cero(session, audit_repo, vector_repo)


@pytest.mark.asyncio
async def test_recall_text_coleccion_inexistente_suena_antes_de_embeber():
    """
    Colección de texto ausente = MissingCollectionError ANTES del embed:
    un fallo de inicialización del sustrato NO es un no-match honesto
    (decisión de David, 26 oct — los errores suenan).
    """
    vector_repo = AsyncMock()
    vector_repo.collection_names.return_value = {"document", "conversation"}  # sin system
    manager, session, audit_repo, llm = _manager(vector_repo, _embedding_response())

    with pytest.raises(MissingCollectionError) as exc_info:
        await manager.recall_text(query="memorias")

    assert "system" in str(exc_info.value)
    # El gateway ni se toca: la denegación es ANTES de pagar el embed
    llm.generate_embedding.assert_not_awaited()
    vector_repo.search.assert_not_awaited()


# =====================================================================
# CAPA DE POLÍTICA: recall_image (identify, superficie simétrica)
# =====================================================================

@pytest.mark.asyncio
async def test_recall_image_happy_path():
    """
    Simetría con el texto: mismos filtros planos, mismo hit plano — sobre
    el espacio de visión. El punto de imagen no lleva content: su payload
    ancla sesión y proyecto.
    """
    vector_repo = AsyncMock()
    vector_repo.collection_names.return_value = {"images"}
    vector_repo.search.return_value = [
        _scored_point(IMG_POINT_ID, 0.62, {"session_id": "sesion-1", "project": "elios-workshop"}),
    ]
    manager, session, audit_repo, llm = _manager(vector_repo, _embedding_response())

    hits = await manager.recall_image(
        image_data_uri=VALID_IMAGE_DATA_URI,
        project="elios-workshop",
        top_k=5,
        score_threshold=0.5,
    )

    assert len(hits) == 1
    hit = hits[0]
    assert hit.domain == IMAGE_COLLECTION == "images"
    assert hit.point_id == IMG_POINT_ID
    assert hit.score == 0.62
    assert hit.content is None  # el punto de imagen no lleva texto
    assert hit.session_id == "sesion-1"
    assert hit.project == "elios-workshop"

    # Embed efímero de imagen: grupo de visión + modality (BUG-004 #58)
    llm.generate_embedding.assert_awaited_once()
    request = llm.generate_embedding.call_args[0][0]
    assert request.model == IMAGE_EMBEDDING_MODEL == "image-embedding"
    assert request.input == VALID_IMAGE_DATA_URI
    assert request.modality == "image"

    # Búsqueda contra la colección de imágenes, filtros idénticos a los de texto
    call = vector_repo.search.call_args
    assert call.kwargs["domain"] == IMAGE_COLLECTION
    assert call.kwargs["filters"].tenant_id is None
    assert call.kwargs["filters"].project == "elios-workshop"
    assert call.kwargs["filters"].score_threshold == 0.5

    _assert_dif_cero(session, audit_repo, vector_repo)


@pytest.mark.asyncio
async def test_recall_image_no_match_honesto():
    """Nada sobre el umbral de visión = lista vacía. Sin inventar matches."""
    vector_repo = AsyncMock()
    vector_repo.collection_names.return_value = {"images"}
    vector_repo.search.return_value = []
    manager, session, audit_repo, llm = _manager(vector_repo, _embedding_response())

    hits = await manager.recall_image(
        image_data_uri=VALID_IMAGE_DATA_URI, score_threshold=0.5,
    )

    assert hits == []
    _assert_dif_cero(session, audit_repo, vector_repo)


@pytest.mark.asyncio
async def test_recall_image_coleccion_inexistente_suena():
    """Sin colección 'images' no hay recall visual: error explícito, no vacío."""
    vector_repo = AsyncMock()
    vector_repo.collection_names.return_value = {"document", "conversation", "system"}
    manager, session, audit_repo, llm = _manager(vector_repo, _embedding_response())

    with pytest.raises(MissingCollectionError) as exc_info:
        await manager.recall_image(image_data_uri=VALID_IMAGE_DATA_URI)

    assert IMAGE_COLLECTION in str(exc_info.value)
    llm.generate_embedding.assert_not_awaited()
    vector_repo.search.assert_not_awaited()


# =====================================================================
# CAPA FINA MCP: handle_recall_tool
# =====================================================================

@pytest.mark.asyncio
@patch("src.mcp.tools.recall_tools.LLMClient")
@patch("src.mcp.tools.recall_tools.HybridMemoryManager")
@patch("src.mcp.tools.recall_tools.get_qdrant_client", new_callable=AsyncMock)
@patch("src.mcp.tools.recall_tools.AsyncSessionLocal")
async def test_tool_search_context_happy_path(
    mock_session_local, mock_get_qdrant, mock_manager_class, mock_llm_class
):
    """Capa fina: valida, delega en el manager y serializa RecallResponse."""
    mock_session_local.return_value = _SessionCM()

    hit = MemoryHit(
        point_id=SYS_POINT_ID, domain="system", score=0.9,
        content="memoria del taller", session_id=None, project=None,
    )
    manager = AsyncMock()
    manager.recall_text.return_value = [hit]
    mock_manager_class.return_value = manager

    response = await handle_recall_tool("coreai_search_context", {
        "query": "memorias del taller",
        "top_k": 3,
    })

    assert response["isError"] is False
    parsed = RecallResponse.model_validate_json(response["content"][0]["text"])
    assert len(parsed.hits) == 1
    assert parsed.hits[0].domain == "system"

    # Delegación pura: los defaults del schema viajan explícitos al manager
    manager.recall_text.assert_awaited_once_with(
        query="memorias del taller",
        project=None,
        top_k=3,
        score_threshold=0.7,
    )


@pytest.mark.asyncio
@patch("src.mcp.tools.recall_tools.LLMClient")
@patch("src.mcp.tools.recall_tools.HybridMemoryManager")
@patch("src.mcp.tools.recall_tools.get_qdrant_client", new_callable=AsyncMock)
@patch("src.mcp.tools.recall_tools.AsyncSessionLocal")
async def test_tool_search_image_happy_path(
    mock_session_local, mock_get_qdrant, mock_manager_class, mock_llm_class
):
    """Simetría de superficie: search_image delega con el umbral de visión."""
    mock_session_local.return_value = _SessionCM()

    hit = MemoryHit(
        point_id=IMG_POINT_ID, domain="images", score=0.55,
        content=None, session_id="sesion-1", project="elios-workshop",
    )
    manager = AsyncMock()
    manager.recall_image.return_value = [hit]
    mock_manager_class.return_value = manager

    response = await handle_recall_tool("coreai_search_image", {
        "image": VALID_IMAGE_DATA_URI,
    })

    assert response["isError"] is False
    manager.recall_image.assert_awaited_once_with(
        image_data_uri=VALID_IMAGE_DATA_URI,
        project=None,
        top_k=5,
        score_threshold=0.5,
    )


@pytest.mark.asyncio
@patch("src.mcp.tools.recall_tools.AsyncSessionLocal")
async def test_tool_args_malformados_no_abren_sesion(mock_session_local):
    """
    DoD de negativos: un argumento malformado jamás abre transacción —
    la validación de forma ocurre en el borde, ANTES de la sesión.
    """
    # top_k fuera de rango
    response = await handle_recall_tool("coreai_search_context", {
        "query": "x", "top_k": 0,
    })
    assert response["isError"] is True

    # SVG: el patrón raster lo rebota (misma regla que la ingesta)
    response = await handle_recall_tool("coreai_search_image", {
        "image": "data:image/svg+xml;base64,PHN2Zy8=",
    })
    assert response["isError"] is True

    # Clave desconocida (extra=forbid: schemas planos y cerrados)
    response = await handle_recall_tool("coreai_search_context", {
        "query": "x", "clave_inventada": 1,
    })
    assert response["isError"] is True

    mock_session_local.assert_not_called()


@pytest.mark.asyncio
@patch("src.mcp.tools.recall_tools.AsyncSessionLocal")
async def test_tool_imagen_corrupta_no_abre_sesion(mock_session_local):
    """Bytes que decodifican base64 pero no son imagen: el oráculo Pillow manda."""
    response = await handle_recall_tool("coreai_search_image", {
        "image": "data:image/png;base64,aGVsbG8=",  # "hello" no es un PNG
    })
    assert response["isError"] is True
    mock_session_local.assert_not_called()


@pytest.mark.asyncio
@patch("src.mcp.tools.recall_tools.LLMClient")
@patch("src.mcp.tools.recall_tools.HybridMemoryManager")
@patch("src.mcp.tools.recall_tools.get_qdrant_client", new_callable=AsyncMock)
@patch("src.mcp.tools.recall_tools.AsyncSessionLocal")
async def test_tool_missing_collection_error_es_iserror(
    mock_session_local, mock_get_qdrant, mock_manager_class, mock_llm_class
):
    """
    El error explícito del manager atraviesa la tool como isError=True:
    el cliente OYE que el sustrato no está inicializado.
    """
    mock_session_local.return_value = _SessionCM()
    manager = AsyncMock()
    manager.recall_text.side_effect = MissingCollectionError(
        "Recall bloqueado: colecciones inexistentes (system)."
    )
    mock_manager_class.return_value = manager

    response = await handle_recall_tool("coreai_search_context", {
        "query": "x",
    })

    assert response["isError"] is True
    assert "Recall bloqueado" in response["content"][0]["text"]


@pytest.mark.asyncio
async def test_tool_no_gestionada_revierta():
    """Router estricto: un nombre fuera de las dos tools de recall revienta."""
    with pytest.raises(ValueError):
        await handle_recall_tool("coreai_nonsense", {})


# =====================================================================
# DoD: SUPERFICIE EXACTA DEL SERVIDOR
# =====================================================================

def test_dod_tools_list_exacta():
    """
    DoD del bloque: tools/list son EXACTAMENTE las dos de tasks + las dos
    de recall. El feature creep de superficie se defiende por test, no se
    asume por revisión (ADR-005: 'Tentación permanente').
    """
    names = {tool.name for tool in get_task_tools()} | {tool.name for tool in get_recall_tools()}
    assert names == {
        "coreai_dispatch_task",
        "coreai_get_task",
        "coreai_search_context",
        "coreai_search_image",
    }
