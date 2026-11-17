"""
Suite de la clasificación determinista/transitoria (4.4-002 #104).

Hermetismo total: cero DB, cero entorno — solo la taxonomía de excepciones
que cada capa puede emitir en el lane de ejecución (frontera de contratos,
router, handlers, gateway).
"""

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from src.contracts.registry import ContractNotRegisteredError, VectorizeEventPayload
from src.workers.failure_classification import es_fallo_determinista
from src.workers.task_router import MemoryPointNotFoundError


def _error_de_contrato() -> ValidationError:
    """Un payload que viola el contrato, real: la excepción que levanta Pydantic."""
    try:
        VectorizeEventPayload.model_validate({"project": "sin-content"})
    except ValidationError as error:
        return error
    raise AssertionError("el payload sin 'content' debía reventar la validación")


@pytest.mark.parametrize(
    "error",
    [
        # Fuera de catálogo / corrupción de cola (route_task).
        ValueError("Tipo de tarea desconocido, corrupto o fuera de catalogo"),
        # Brecha de implementación: contratado sin handler.
        NotImplementedError("El tipo de tarea está CONTRATADO pero su handler no está registrado"),
        # Sin contrato registrado.
        ContractNotRegisteredError("El tipo de tarea no tiene contrato registrado"),
        # Violación de contrato real de Pydantic.
        _error_de_contrato(),
        # ADR-009: point_id inexistente.
        MemoryPointNotFoundError("El point_id no existe en ninguna colección registrada"),
    ],
    ids=[
        "value-error-catalogo",
        "not-implemented-brecha",
        "sin-contrato",
        "validacion-contrato",
        "punto-inexistente",
    ],
)
def test_fallo_determinista_sin_reintento(error: Exception):
    """Los fallos deterministas mueren en FAILED a la primera: cero reintentos."""
    assert es_fallo_determinista(error) is True


@pytest.mark.parametrize(
    "error",
    [
        # Gateway caído: el clásico del E2E de LiteLLM (validación viva de David).
        httpx.ConnectError("Connection refused"),
        # Timeout de lectura contra el gateway o Infinity.
        httpx.ReadTimeout("Read timed out"),
        # Timeout del ciclo del handler.
        asyncio.TimeoutError(),
        # Caída genérica de red / infraestructura.
        OSError("Network is unreachable"),
        # Cualquier error de ejecución no clasificado: transitorio por defecto
        # (ante la duda se difiere — que reintente, no que se pierda la tarea).
        RuntimeError("explotó algo sin clasificar"),
    ],
    ids=[
        "connect-error",
        "read-timeout",
        "timeout",
        "oserror-red",
        "runtime-error",
    ],
)
def test_fallo_transitorio_recupera(error: Exception):
    """Los fallos transitorios van a retry_task: la infra puede recuperarse."""
    assert es_fallo_determinista(error) is False
