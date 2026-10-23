"""
Tests unitarios de la consolidación de memoria (Epic 4 - Issue 4.3, #29).

Aísla exclusivamente la lógica algorítmica con mocks: sin cola, sin Qdrant
real y sin gateway real. Valida:
- Parseo de ventanas temporales del contrato ('7d' / '12h' / '2w').
- No-op limpio cuando la ventana no trae candidatos.
- Happy path: resumen LLM → inyección dual → marca de orígenes → UN commit.
- Visibilidad del resumen = la más restrictiva de sus orígenes.
- Cortafuegos: fallo en el upsert vectorial → rollback, nada a medias.
- Resumen vacío del gateway → ValueError honesto.
"""

import uuid
import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from src.db.models import Event
from src.schemas.memory import DomainType, Visibility
from src.schemas.llm import CompletionResponse
from src.managers.memory_manager import (
    HybridMemoryManager,
    parsear_ventana_temporal,
    CONSOLIDATION_CHAT_MODEL,
)


# =====================================================================
# FIXTURES (Datos Sintéticos)
# =====================================================================

def _evento(
    visibility: Visibility = Visibility.PRIVATE,
    content: str = "Registro episódico de prueba."
) -> Event:
    return Event(
        id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        content=content,
        domain=DomainType.CONVERSATION,
        visibility=visibility,
        consolidated=False,
    )


def _respuesta_resumen(texto: str = "Resumen denso de hechos de los registros."):
    """Respuesta simulada del gateway a la chat completion del resumen."""
    return CompletionResponse.model_validate({
        "choices": [
            {"message": {"role": "assistant", "content": texto}, "finish_reason": "stop"}
        ],
        "model": "local-fallback",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    })


def _respuesta_embedding():
    """Respuesta simulada del gateway para el embedding del resumen."""
    data = MagicMock()
    data.embedding = [0.1, 0.2, 0.3]
    response = MagicMock()
    response.data = [data]
    response.model = "text-embedding"
    response.usage.prompt_tokens = 10
    response.usage.total_tokens = 10
    return response


@pytest.fixture
def manager():
    """HybridMemoryManager con todas las dependencias moqueadas."""
    session = AsyncMock()
    relational_repo = MagicMock()
    relational_repo.list_pending_consolidation = AsyncMock(return_value=[])
    relational_repo.mark_consolidated = AsyncMock(return_value=2)
    relational_repo.create = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    vector_repo = AsyncMock()
    audit_repo = AsyncMock()
    llm_client = AsyncMock()
    llm_client.generate_embedding = AsyncMock(return_value=_respuesta_embedding())
    llm_client.generate_completion = AsyncMock(return_value=_respuesta_resumen())

    manager = HybridMemoryManager(
        session=session,
        relational_repo=relational_repo,
        vector_repo=vector_repo,
        audit_repo=audit_repo,
        llm_client=llm_client,
    )
    return manager, session, relational_repo, vector_repo, audit_repo, llm_client


# =====================================================================
# TESTS: parseo de ventanas del contrato
# =====================================================================

@pytest.mark.parametrize(
    "ventana,esperada",
    [
        ("7d", timedelta(days=7)),
        ("12h", timedelta(hours=12)),
        ("2w", timedelta(weeks=2)),
        (" 7D ", timedelta(days=7)),
    ],
)
def test_parsear_ventana_valida(ventana, esperada):
    assert parsear_ventana_temporal(ventana) == esperada


@pytest.mark.parametrize("ventana", ["", "abc", "7x", "d7", "-3d"])
def test_parsear_ventana_invalida(ventana):
    with pytest.raises(ValueError) as exc_info:
        parsear_ventana_temporal(ventana)
    assert "Ventana temporal no válida" in str(exc_info.value)


# =====================================================================
# TESTS: consolidación
# =====================================================================

@pytest.mark.asyncio
async def test_consolidacion_sin_candidatos_es_noop(manager):
    """Ventana vacía: no-op limpio — sin LLM, sin escrituras, sin commit."""
    mg, session, _, _, _, llm_client = manager
    mg.relational_repo.list_pending_consolidation = AsyncMock(return_value=[])

    resultado = await mg.consolidate_memory(
        entity_id=uuid.uuid4(),
        domain=DomainType.CONVERSATION,
        time_window="7d",
    )

    assert resultado is None
    llm_client.generate_completion.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_consolidacion_happy_path(manager):
    """Resumen → inyección dual → marcas → UN commit. Visibilidad restrictiva."""
    mg, session, relational_repo, vector_repo, audit_repo, llm_client = manager
    privado = _evento(visibility=Visibility.PRIVATE, content="Hecho privado A.")
    compartido = _evento(visibility=Visibility.SHARED, content="Hecho compartido B.")
    mg.relational_repo.list_pending_consolidation = AsyncMock(
        return_value=[privado, compartido]
    )

    resultado = await mg.consolidate_memory(
        entity_id=privado.entity_id,
        domain=DomainType.CONVERSATION,
        time_window="7d",
    )

    # Resumen pedido al gateway con el modelo de consolidación...
    llm_client.generate_completion.assert_awaited_once()
    peticion = llm_client.generate_completion.await_args.args[0]
    assert peticion.model == CONSOLIDATION_CHAT_MODEL
    corpus = peticion.messages[1]["content"]
    assert "Hecho privado A." in corpus
    assert "Hecho compartido B." in corpus

    # ...inyección dual del resumen (la visibilidad es la más restrictiva)...
    create_kwargs = relational_repo.create.await_args.kwargs
    assert create_kwargs["visibility"] == Visibility.PRIVATE
    assert create_kwargs["entity_id"] == privado.entity_id
    upsert_kwargs = vector_repo.upsert.await_args.kwargs
    assert upsert_kwargs["payload"]["metadata"]["origin"] == "consolidation"
    assert upsert_kwargs["payload"]["metadata"]["time_window"] == "7d"
    assert set(upsert_kwargs["payload"]["metadata"]["source_event_ids"]) == {
        str(privado.id), str(compartido.id)
    }

    # ...dos facturas en llm_audit: embedding del resumen + completion del LLM.
    assert audit_repo.create.await_count == 2
    factura_completion = audit_repo.create.await_args_list[1].kwargs
    assert factura_completion["model_used"] == "local-fallback"
    assert factura_completion["completion_tokens"] == 20

    # ...marca de orígenes y UN solo commit que asienta todo junto.
    relational_repo.mark_consolidated.assert_awaited_once_with([privado.id, compartido.id])
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()

    assert resultado == relational_repo.create.return_value.id


@pytest.mark.asyncio
async def test_consolidacion_resumen_vacio_del_gateway(manager):
    """El modelo no respondió: ValueError honesto, rollback, nada persistido."""
    mg, session, _, _, _, llm_client = manager
    llm_client.generate_completion = AsyncMock(return_value=_respuesta_resumen("   "))
    mg.relational_repo.list_pending_consolidation = AsyncMock(return_value=[_evento()])

    with pytest.raises(ValueError) as exc_info:
        await mg.consolidate_memory(
            entity_id=uuid.uuid4(),
            domain=DomainType.CONVERSATION,
            time_window="7d",
        )

    assert "resumen vacío" in str(exc_info.value)
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_consolidacion_fallo_vectorial_rollback(manager):
    """Fallo en el upsert de Qdrant: rollback, sin marcas, sin commit a medias."""
    mg, session, relational_repo, vector_repo, _, _llm = manager
    vector_repo.upsert = AsyncMock(side_effect=RuntimeError("Qdrant caído"))
    mg.relational_repo.list_pending_consolidation = AsyncMock(return_value=[_evento()])

    with pytest.raises(RuntimeError):
        await mg.consolidate_memory(
            entity_id=uuid.uuid4(),
            domain=DomainType.CONVERSATION,
            time_window="7d",
        )

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    relational_repo.mark_consolidated.assert_not_awaited()
