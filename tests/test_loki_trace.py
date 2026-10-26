"""
Tests del filtro de traza Loki por UUID (Issue 4.3, ADR-003).

La correlación por tarea viaja en el formato de logging vía contextvar: el
filtro inyecta task_id en cada record. No se prueba infraestructura — solo
stdout plano llega a Loki vía el driver de Docker del compose.
"""

import logging
import uuid

from src.logging_config import (
    TaskIdLogFilter,
    DEFAULT_LOG_FORMAT,
    set_current_task_id,
    clear_current_task_id,
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
