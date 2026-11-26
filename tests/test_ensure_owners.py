"""
Tests unitarios del backfill idempotente de titularidad (D1 del ADR-012,
ejecutado en el [ADR-012-005] #116).

Sin Qdrant real: la política del ensure_owners se defiende contra un
cliente mockeado. Lo que estos tests fijan:

- Cobertura: el backfill recorre TODAS las colecciones registradas (texto
  por dominio + images) — decisión de David: también la colección de
  imágenes, cuyos puntos llevan owner desde el #115.
- Regla D1 (DoD): el payload estampado es SIEMPRE el slug system_default,
  la misma identidad implícita del bootstrap relacional (ADR-012-001).
- Idempotencia por construcción: el selector de la mutación es
  is_empty/is_null sobre 'owner' — un punto con titularidad ya estampada
  queda FUERA del selector y N arranques convergen al mismo estado; jamás
  se sobrescribe un owner existente.
- Fallo estructural: un error de Qdrant en cualquier colección sube —
  el arranque no acepta tráfico con históricos sin cerrar.
"""

import pytest
from unittest.mock import AsyncMock, patch

from qdrant_client.http import models as qmodels

from src.db.vector_db import ensure_owners
from src.db.entities_config import SYSTEM_DEFAULT_SLUG
from src.repositories.vector import REGISTERED_COLLECTIONS


def _selector_claves(selector) -> list[str]:
    """Claves que cubren las condiciones should del selector de puntos."""
    assert isinstance(selector, qmodels.FilterSelector), (
        "El selector de la mutación es un FilterSelector: set_payload "
        "dirigido por filtro, no un listado de IDs."
    )
    condiciones = selector.filter.should
    assert len(condiciones) == 2
    claves = set()
    for condicion in condiciones:
        # IsEmptyCondition / IsNullCondition: la clave es 'owner' en ambas
        claves.add(condicion.is_empty.key if hasattr(condicion, "is_empty") else condicion.is_null.key)
    return sorted(claves)


@pytest.mark.asyncio
@patch("src.db.vector_db.get_qdrant_client")
async def test_backfill_recorre_todas_las_colecciones_registradas(mock_get_client):
    """DoD: el cierre de históricos cubre TODO el universo de puntos —
    texto por dominio y la colección de imágenes (decisión de David)."""
    client = AsyncMock()
    mock_get_client.return_value = client

    await ensure_owners()

    tocadas = [call.kwargs["collection_name"] for call in client.set_payload.call_args_list]
    assert tocadas == REGISTERED_COLLECTIONS


@pytest.mark.asyncio
@patch("src.db.vector_db.get_qdrant_client")
async def test_backfill_sella_system_default(mock_get_client):
    """Regla D1 literal: el slug estampado es SIEMPRE system_default — el
    histórico queda a nombre del inquilino por defecto del sustrato."""
    client = AsyncMock()
    mock_get_client.return_value = client

    await ensure_owners()

    assert client.set_payload.await_count == len(REGISTERED_COLLECTIONS)
    for call in client.set_payload.call_args_list:
        assert call.kwargs["payload"] == {"owner": SYSTEM_DEFAULT_SLUG}
        assert call.kwargs["wait"] is True


@pytest.mark.asyncio
@patch("src.db.vector_db.get_qdrant_client")
async def test_backfill_selector_solo_puntos_sin_owner(mock_get_client):
    """DoD de idempotencia: el selector de la mutación toca SOLO puntos
    con owner vacío o ausente — una titularidad existente JAMÁS se
    sobrescribe, y N arranques hacen exactamente lo mismo (convergencia
    al mismo estado, no 'segunda pasada distinta')."""
    client = AsyncMock()
    mock_get_client.return_value = client

    await ensure_owners()

    for call in client.set_payload.call_args_list:
        assert _selector_claves(call.kwargs["points"]) == ["owner"]


@pytest.mark.asyncio
@patch("src.db.vector_db.get_qdrant_client")
async def test_backfill_error_estructural_sube(mock_get_client):
    """Un fallo de Qdrant en cualquier colección aborta el arranque: el
    backfill es garantía de arranque (gemelo ensure_* del ADR-011), no
    un best-effort silencioso."""
    client = AsyncMock()
    client.set_payload.side_effect = RuntimeError("connection refused")
    mock_get_client.return_value = client

    with pytest.raises(RuntimeError, match="connection refused"):
        await ensure_owners()
