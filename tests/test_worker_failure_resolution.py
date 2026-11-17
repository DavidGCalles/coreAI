"""
Suite de _resolver_fallo (4.4-002 #104): el punto donde la clasificación
decide el desenlace de una tarea que revienta.

Sin base de datos: un fake repo registra las llamadas que recibe y un
fake logger las líneas que emite, y se aserta la DECISIÓN (fail_task vs
retry_task y con qué traza). El comportamiento de repositorio real ya
tiene sus suites (fail_task en tests de repositorio, retry_task en
test_retry_task.py) — aquí lo que se prueba es el cableado del worker
y el trace honesto (#61).
"""

from uuid import uuid4

import httpx
import pytest

from src import worker as worker_module
from src.worker import _resolver_fallo


class FakeRepo:
    """TaskRepository de mentira: apunta las llamadas, no toca Postgres."""

    def __init__(self, desenlace=None):
        self.fails = []
        self.retries = []
        self.desenlace = desenlace  # lo que devolverá retry_task

    async def fail_task(self, task_id, error_msg):
        self.fails.append((task_id, error_msg))

    async def retry_task(self, task_id, error_msg):
        self.retries.append((task_id, error_msg))
        return self.desenlace


class FakeLogger:
    """Logger de mentira: resuelve el %s perezoso y apunta la línea."""

    def __init__(self):
        self.lines = []

    def _add(self, level, msg, args):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *args):
        self._add("info", msg, args)

    def warning(self, msg, *args):
        self._add("warning", msg, args)

    def error(self, msg, *args):
        self._add("error", msg, args)


@pytest.fixture
def fake_logger(monkeypatch):
    """Sustituye el logger del worker: asertas sin depender de la config de logging."""
    fake = FakeLogger()
    monkeypatch.setattr(worker_module, "logger", fake)
    return fake


async def _resolver(repo, error):
    """Ejecuta la resolución con un UUID cualquiera y devuelve el repo cargado."""
    await _resolver_fallo(repo, uuid4(), error)
    return repo


def _lineas(fake_logger):
    return " | ".join(linea for _, linea in fake_logger.lines)


@pytest.mark.asyncio
async def test_fallo_determinista_muere_en_failed(fake_logger):
    """ValueError (catálogo/corrupción de cola): fail_task a la primera, cero reintentos."""
    repo = await _resolver(FakeRepo(), ValueError("tipo fuera de catálogo"))

    assert len(repo.fails) == 1, "El fallo determinista debe llamar fail_task exactamente una vez."
    assert repo.retries == [], "Un fallo determinista JAMÁS consume reintento."
    _, trace = repo.fails[0]
    assert trace.startswith("ValueError"), "Trace honesto: la clase de la excepción viaja."
    assert "tipo fuera de catálogo" in trace
    assert "FAILED" in _lineas(fake_logger)


@pytest.mark.asyncio
async def test_not_implemented_es_determinista():
    """[DAVID 17 nov] NotImplementedError: nadie implementa «por amor al arte tras un reintento»."""
    repo = await _resolver(FakeRepo(), NotImplementedError("handler ausente para image.process"))

    assert len(repo.fails) == 1 and repo.retries == []


@pytest.mark.asyncio
async def test_fallo_transitorio_va_a_retry(fake_logger):
    """Un error de conexión del gateway: retry_task con desenlace RETRY."""
    desenlace = {"outcome": "RETRY", "retry_count": 1, "available_at": "2026-11-17T12:00:00+00:00"}
    repo = await _resolver(FakeRepo(desenlace), httpx.ConnectError("Connection refused"))

    assert repo.fails == [], "Un fallo transitorio no debe matar la tarea."
    assert len(repo.retries) == 1
    _, trace = repo.retries[0]
    assert "ConnectError" in trace, "El trace honesto lleva la clase de la excepción real."
    assert "Connection refused" in trace
    assert "difiere" in _lineas(fake_logger)


@pytest.mark.asyncio
async def test_reintentos_agotados_sin_excepcion(fake_logger):
    """retry_task devolviendo FAILED (agotado): se registra y no revienta."""
    desenlace = {"outcome": "FAILED", "retry_count": 3}
    repo = await _resolver(FakeRepo(desenlace), httpx.ReadTimeout("Read timed out"))

    assert len(repo.retries) == 1 and repo.fails == []
    assert "agotados" in _lineas(fake_logger)


@pytest.mark.asyncio
async def test_tarea_desaparecida_no_revienta(fake_logger):
    """retry_task devolviendo None (fila borrada): warning y a otra cosa."""
    repo = await _resolver(FakeRepo(desenlace=None), RuntimeError("cualquier cosa transitoria"))

    assert len(repo.retries) == 1 and repo.fails == []
    assert "desaparecida" in _lineas(fake_logger)


@pytest.mark.asyncio
async def test_runtimeerror_es_transitorio():
    """Excepción genérica de ejecución: no está en el catálogo determinista → reintenta."""
    repo = await _resolver(
        FakeRepo(desenlace={
            "outcome": "RETRY",
            "retry_count": 1,
            "available_at": "2026-11-17T12:00:00+00:00",  # contrato completo: el worker la loguea
        }),
        RuntimeError("boom"),
    )

    assert repo.retries and not repo.fails
