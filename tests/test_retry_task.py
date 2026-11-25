"""
Suite de TaskRepository.retry_task (4.4-002 #104): reintento con backoff,
agotamiento → FAILED con historial, y la promesa del issue — ninguna tarea
fallida bloquea la cola.

Hermetismo (lección 31 oct): la config de reintentos NO se lee del entorno
— fixture fija max_retries=3, base=1s, cap=30s, jitter=0 para que las
esperas del backoff sean deterministas y asertables.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, delete, update

from src.db.database import AsyncSessionLocal, engine
from src.db.models import Task
from src.managers.config_manager import config_manager
from src.repositories.relational import TaskRepository, SessionRepository, EntityRepository

CONFIG_FIJA = {
    "max_retries": 3,
    "backoff_base_seconds": 1.0,
    "backoff_cap_seconds": 30.0,
    "backoff_jitter": 0.0,
}


@pytest.fixture(autouse=True)
def config_fija(monkeypatch):
    """La suite decide la política de reintentos, no el entorno."""
    monkeypatch.setattr(config_manager, "get_retry_config", lambda: dict(CONFIG_FIJA))


@pytest.fixture
async def db_session():
    """Sesión limpia, blindada y que RECOGE SU BASURA."""
    await engine.dispose()
    session = AsyncSessionLocal()
    try:
        await session.execute(delete(Task))
        await session.commit()

        yield session
    finally:
        await session.execute(delete(Task))
        await session.commit()

        await session.close()
        await engine.dispose()


@pytest.fixture
async def setup_running_task(db_session):
    """Árbol relacional mínimo + tarea RUNNING (como la deja el claim del worker)."""
    entity_repo = EntityRepository(db_session)
    session_repo = SessionRepository(db_session)
    task_repo = TaskRepository(db_session)

    entity = await entity_repo.create(role="test_worker")
    session_db = await session_repo.create(entity_id=entity.id)

    task = await task_repo.create(
        session_id=session_db.id,
        payload={"task_type": "dummy_test_task", "extra": "dato-del-inquilino", "actor": "lane-de-test"},
    )
    await db_session.commit()

    # El claim ya se probó en la suite de adquisición (4.4-001): aquí se
    # simula su efecto directo — la tarea llega RUNNING a manos de su worker.
    stmt = update(Task).where(Task.id == task.id).values({Task.status: "RUNNING"})
    await db_session.execute(stmt)
    await db_session.commit()

    return task.id


async def _leer(db_session, task_id) -> Task:
    db_session.expunge_all()
    result = await db_session.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one()


async def _segundos_diferidos(db_session, repo, task_id) -> float:
    """Ejecuta un reintento y devuelve cuánto queda hasta su vencimiento."""
    desenlace = await repo.retry_task(task_id, "httpx.ReadTimeout: Read timed out")
    assert desenlace is not None and desenlace["outcome"] == "RETRY"
    tarea = await _leer(db_session, task_id)
    return (tarea.available_at - datetime.now(timezone.utc)).total_seconds()


def _cerca(segundos_obtenidos, segundos_esperados):
    """Tolerancia de ±1s: el reloj que decide es el de Postgres, no el del test."""
    assert abs(segundos_obtenidos - segundos_esperados) <= 1.0, (
        f"Esperados ~{segundos_esperados}s, obtenidos {segundos_obtenidos:.1f}s"
    )


@pytest.mark.asyncio
async def test_retry_transitorio_difiere_y_cuenta(db_session, setup_running_task):
    """Fallo transitorio: +1 en retry_count, PENDING diferida, historial con la traza."""
    repo = TaskRepository(db_session)

    desenlace = await repo.retry_task(setup_running_task, "httpx.ConnectError: Connection refused")

    assert desenlace is not None and desenlace["outcome"] == "RETRY"
    assert desenlace["retry_count"] == 1

    tarea = await _leer(db_session, setup_running_task)
    assert tarea.status.value == "PENDING", "La tarea transitoria debe volver a PENDING."
    assert tarea.retry_count == 1
    # Diferida ~1s (base·2^0, jitter 0): vence pronto pero NO ya — en el futuro.
    assert tarea.available_at is not None
    _cerca((tarea.available_at - datetime.now(timezone.utc)).total_seconds(), 1)
    # El historial nace con la primera entrada y el payload original intacto.
    assert tarea.payload["task_type"] == "dummy_test_task"
    assert tarea.payload["extra"] == "dato-del-inquilino"
    assert len(tarea.payload["error_history"]) == 1
    entrada = tarea.payload["error_history"][0]
    assert entrada["attempt"] == 1
    assert entrada["trace"] == "httpx.ConnectError: Connection refused"
    assert tarea.payload["error_trace"] == "httpx.ConnectError: Connection refused"


@pytest.mark.asyncio
async def test_error_history_enriquece_con_el_actor_del_contexto(db_session, setup_running_task):
    """
    [ADR-012-003] #114: cada entrada de error_history lleva el actor
    estampado en el contexto de la tarea — la DLQ dice QUIÉN despachó cada
    intento (apto Grafana). El actor lo leyó retry_task del propio JSONB.
    """
    repo = TaskRepository(db_session)

    await repo.retry_task(setup_running_task, "httpx.ConnectError: Connection refused")
    await repo.retry_task(setup_running_task, "httpx.ReadTimeout: Read timed out")

    tarea = await _leer(db_session, setup_running_task)
    historial = tarea.payload["error_history"]
    assert len(historial) == 2
    for entrada in historial:
        assert entrada["actor"] == "lane-de-test", (
            "Cada intento de la DLQ debe llevar el actor del dispatch"
        )
    # El actor de CONTEXTO del payload sigue intacto: solo se enriquece el
    # historial, el contexto no se reescribe.
    assert tarea.payload["actor"] == "lane-de-test"


@pytest.mark.asyncio
async def test_error_history_tarea_historica_sin_actor_queda_en_none(db_session, setup_running_task):
    """
    [ADR-012-003] #114: una tarea histórica (anterior a la familia, sin actor
    estampado) agrega entradas con actor None — la auditoría no se reescribe
    ni se inventa trazabilidad que nunca existió.
    """
    db_session.expunge_all()
    await db_session.execute(
        update(Task)
        .where(Task.id == setup_running_task)
        .values({Task.payload: {"task_type": "dummy_test_task", "extra": "legado-pre-familia"}})
    )
    await db_session.commit()

    repo = TaskRepository(db_session)
    desenlace = await repo.retry_task(setup_running_task, "RuntimeError: fallo del legado")
    assert desenlace is not None and desenlace["outcome"] == "RETRY"

    tarea = await _leer(db_session, setup_running_task)
    entrada = tarea.payload["error_history"][0]
    assert entrada["actor"] is None, "Sin actor en el contexto, la entrada queda en None."
    assert "actor" not in tarea.payload or tarea.payload["actor"] is None


@pytest.mark.asyncio
async def test_backoff_exponencial(db_session, setup_running_task):
    """Con base 1s y jitter 0 las esperas crecen 1s → 2s → 4s."""
    repo = TaskRepository(db_session)

    esperas = [
        await _segundos_diferidos(db_session, repo, setup_running_task)
        for _ in range(3)
    ]

    _cerca(esperas[0], 1)
    _cerca(esperas[1], 2)
    _cerca(esperas[2], 4)


@pytest.mark.asyncio
async def test_backoff_con_techo(db_session, setup_running_task, monkeypatch):
    """Con base 10s la tercera espera sería 40s: el cap de 30 la recorta."""
    monkeypatch.setattr(config_manager, "get_retry_config", lambda: {
        "max_retries": 3, "backoff_base_seconds": 10.0,
        "backoff_cap_seconds": 30.0, "backoff_jitter": 0.0,
    })
    repo = TaskRepository(db_session)

    esperas = [
        await _segundos_diferidos(db_session, repo, setup_running_task)
        for _ in range(3)
    ]

    _cerca(esperas[0], 10)
    _cerca(esperas[1], 20)
    # El cap recorta los 40s que la exponencial pediría en el tercer reintento.
    _cerca(esperas[2], 30)


@pytest.mark.asyncio
async def test_retry_agotado_deja_failed_con_historial_completo(db_session, setup_running_task):
    """Tres reintentos y al cuarto fallo: FAILED, retry_count clavado, available_at limpio."""
    repo = TaskRepository(db_session)

    desenlaces = []
    for intento in range(4):
        desenlaces.append(await repo.retry_task(setup_running_task, f"fallo {intento}"))

    assert [d["outcome"] for d in desenlaces] == ["RETRY", "RETRY", "RETRY", "FAILED"]

    tarea = await _leer(db_session, setup_running_task)
    assert tarea.status.value == "FAILED"
    assert tarea.retry_count == 3, "El contador refleja los reintentos hechos, no el fallo final."
    assert tarea.available_at is None, "Una FAILED no tiene vencimiento que cumplir."
    # Historial íntegro: 4 entradas (3 reintentos + fallo final), intentos 1..4.
    assert [e["attempt"] for e in tarea.payload["error_history"]] == [1, 2, 3, 4]
    assert tarea.payload["error_trace"] == "fallo 3", "La traza final manda en error_trace."
    # Y el payload original sigue intacto para auditoría o reinyección (4.4-003).
    assert tarea.payload["task_type"] == "dummy_test_task"
    assert tarea.payload["extra"] == "dato-del-inquilino"


@pytest.mark.asyncio
async def test_error_history_acumula_sin_machacar_payload(db_session, setup_running_task):
    """Cada intento fallido APPENDA su entrada; ninguna clave del payload se pierde."""
    repo = TaskRepository(db_session)

    await repo.retry_task(setup_running_task, "RuntimeError: primera")
    await repo.retry_task(setup_running_task, "RuntimeError: segunda")

    tarea = await _leer(db_session, setup_running_task)
    traces = [e["trace"] for e in tarea.payload["error_history"]]
    assert traces == ["RuntimeError: primera", "RuntimeError: segunda"]
    assert all("at" in e for e in tarea.payload["error_history"]), "Cada entrada timestamp su intento."
    assert tarea.payload["extra"] == "dato-del-inquilino"


@pytest.mark.asyncio
async def test_tarea_diferida_no_bloquea_la_cola(db_session, setup_running_task):
    """Criterio de aceptación del issue: ninguna tarea fallida detiene al resto."""
    repo = TaskRepository(db_session)

    # La tarea A falla de forma transitoria → queda PENDING diferida.
    await repo.retry_task(setup_running_task, "httpx.ConnectError: Connection refused")

    # Tarea B disponible en paralelo (misma sesión): el claim la atiende sin esperar a A.
    tarea_a = await _leer(db_session, setup_running_task)
    tarea_b = await repo.create(
        session_id=tarea_a.session_id,
        payload={"task_type": "dummy_test_task"},
    )
    await db_session.commit()

    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == tarea_b.id, \
        "El claim debe saltar la diferida y llevarse la disponible."


@pytest.mark.asyncio
async def test_retry_task_tarea_inexistente(db_session):
    """Fila desaparecida: None, sin reventar — el worker solo lo registra."""
    repo = TaskRepository(db_session)
    assert await repo.retry_task(uuid.uuid4(), "no existe") is None
