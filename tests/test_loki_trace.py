"""
Tests del filtro de traza Loki por UUID (Issue 4.3, ADR-003).

La correlación por tarea viaja en el formato de logging vía contextvar: el
filtro inyecta task_id en cada record. No se prueba infraestructura — solo
stdout plano llega a Loki vía el driver de Docker del compose.

Traza de actor (ADR-012-003, #114): el mismo filtro inyecta también el actor
de la tarea en ejecución — free-string estampado por el dispatch o el slug
de la entidad resuelta por omisión.
"""

import logging
import uuid

from src.logging_config import (
    TaskIdLogFilter,
    DEFAULT_LOG_FORMAT,
    set_current_task_id,
    clear_current_task_id,
    set_current_actor,
    clear_current_actor,
)


def _make_record(mensaje: str = "prueba") -> logging.LogRecord:
    return logging.LogRecord(
        name="coreai-worker",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=mensaje,
        args=(),
        exc_info=None,
    )


def test_filtro_inyecta_uuid_de_tarea():
    """Con tarea en contexto, cada record lleva su UUID."""
    task_id = str(uuid.uuid4())
    set_current_task_id(task_id)
    try:
        record = _make_record()
        assert TaskIdLogFilter().filter(record) is True
        assert record.task_id == task_id
    finally:
        clear_current_task_id()


def test_filtro_placeholder_sin_tarea():
    """Sin tarea en contexto (core-mcp, arranque), el campo es el placeholder."""
    clear_current_task_id()
    record = _make_record()
    assert TaskIdLogFilter().filter(record) is True
    assert record.task_id == "-"


def test_formato_renderiza_campo_task():
    """El formato por defecto renderiza task=<valor> en la línea final."""
    record = _make_record("Vectorizando evento")
    TaskIdLogFilter().filter(record)
    line = logging.Formatter(DEFAULT_LOG_FORMAT).format(record)
    assert "task=-" in line
    assert "Vectorizando evento" in line


def test_filtro_inyecta_actor_de_tarea():
    """[ADR-012-003] #114: con actor en contexto, cada record lo lleva."""
    set_current_actor("david-gomez")
    try:
        record = _make_record()
        assert TaskIdLogFilter().filter(record) is True
        assert record.actor == "david-gomez"
    finally:
        clear_current_actor()


def test_filtro_placeholder_actor_sin_contexto():
    """[ADR-012-003] #114: sin actor (tareas históricas, arranque), '-'."""
    clear_current_actor()
    record = _make_record()
    assert TaskIdLogFilter().filter(record) is True
    assert record.actor == "-"


def test_filtro_ignora_actor_de_tipo_basura():
    """set_current_actor solo acepta str: cualquier otra cosa limpia a None."""
    set_current_actor(None)
    record = _make_record()
    assert TaskIdLogFilter().filter(record) is True
    assert record.actor == "-"


def test_formato_renderiza_campo_actor():
    """[ADR-012-003] #114: la línea final renderiza actor=<valor>."""
    set_current_task_id(str(uuid.uuid4()))
    set_current_actor("coreai-cron")
    try:
        record = _make_record("Claim de tarea")
        TaskIdLogFilter().filter(record)
        line = logging.Formatter(DEFAULT_LOG_FORMAT).format(record)
        assert "actor=coreai-cron" in line
        assert "task=" in line
        assert "Claim de tarea" in line
    finally:
        clear_current_task_id()
        clear_current_actor()
