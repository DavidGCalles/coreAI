"""
Centralized logging configuration for coreAI.

Usage:
    from src.logging_config import configure_logging, get_logger
    configure_logging()  # idempotent by default
    logger = get_logger(__name__)

Environment variables (optional):
- LOG_LEVEL (e.g., INFO, DEBUG)
- LOG_FILE (path to log file, optional)

Traza Loki por UUID (Issue 4.3, ADR-003): las líneas de log llevan el UUID
de la tarea en ejecución como texto plano (`task=<uuid>`). El ADR-003 manda
stdout crudo sin conocimiento de telemetría en el código base, así que la
correlación viaja en el formato de logging (contextvar + filtro), no en
instrumentación nueva. En Grafana/LogQL, la consulta
`{compose_service="worker-node"} |= "task=<uuid>"` devuelve la traza
completa del ciclo de vida de esa tarea (claim -> router -> handler ->
manager -> COMPLETED/FAILED).

Traza de actores (ADR-012-003, #114): mismo patrón, mismo filtro — el worker
fija también el actor estampado en el contexto de la tarea (`actor=<slug o
free-string>`) al reclamarla y lo suelta al cerrar su ciclo. En Loki/Grafana,
`|= "actor=<valor>"` recorta la traza de una tarea a las líneas de QUIÉN
despachó: la trazabilidad que la DLQ y su auditoría necesitan.
"""

import contextvars
import logging
import os
from typing import Optional

# Contexto de ejecución de tarea: el worker lo fija al reclamar una tarea y
# lo suelta al cerrar su ciclo. Fuera de una tarea (core-mcp, arranque del
# worker), el filtro renderiza el placeholder "task=-".
_task_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "coreai_task_id", default=None
)

# Actor de la tarea en ejecución (ADR-012-003, #114): free-string de
# trazabilidad estampado por el dispatch (o el slug de la entidad resuelta
# por omisión). Misma vida que el task_id: lo fija el claim, lo suelta el
# finally del ciclo.
_actor_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "coreai_actor", default=None
)


def set_current_task_id(task_id: str) -> None:
    """Fija el UUID de la tarea en ejecución (lo llama el bucle del worker)."""
    _task_id_context.set(str(task_id))


def clear_current_task_id() -> None:
    """Suelta el UUID de la tarea al cerrar su ciclo (worker)."""
    _task_id_context.set(None)


def set_current_actor(actor: Optional[str]) -> None:
    """
    Fija el actor de la tarea en ejecución (ADR-012-003, #114). Lo llama el
    bucle del worker tras el claim, leyendo el actor del propio payload de
    la tarea: lo estampó el dispatch (TaskManager) o no existe (tareas
    históricas anteriores a la familia) y el filtro renderiza el "-".
    """
    _actor_context.set(actor if isinstance(actor, str) else None)


def clear_current_actor() -> None:
    """Suelta el actor al cerrar el ciclo de la tarea (worker)."""
    _actor_context.set(None)


class TaskIdLogFilter(logging.Filter):
    """
    Inyecta el UUID de la tarea en ejecución y su actor en cada registro
    de log.

    Añade los atributos `task_id` (UUID o "-") y `actor` (free-string o
    "-") a cada record; el formato de logging los renderiza como
    `task=<valor>` y `actor=<valor>`. Cero cambios en las llamadas de log
    del código de negocio: router, handlers y managers quedan correlacionados
    por la tarea sin saberlo.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.task_id = _task_id_context.get() or "-"
        record.actor = _actor_context.get() or "-"
        return True


def _resolve_level(level: str | int) -> int:
    """Convert level string/int to logging integer level."""
    if isinstance(level, int):
        return level
    try:
        return getattr(logging, str(level).upper())
    except Exception:
        return logging.INFO


_configured = False

# El campo task=<uuid> es la traza Loki por UUID (Issue 4.3, ADR-003); el
# actor=<valor> es la trazabilidad del dispatch (ADR-012-003, #114).
DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - task=%(task_id)s - actor=%(actor)s - %(message)s"


def configure_logging(
    level: Optional[str | int] = None,
    log_file: Optional[str] = None,
    fmt: Optional[str] = None,
    force: bool = False
) -> None:
    """Configure root logger once for the application.

    - level: logging level (numeric or string like 'INFO')
    - log_file: optional path to a log file
    - fmt: if not provided, defaults to DEFAULT_LOG_FORMAT (incluye task=<uuid>)
    - force: if True reconfigures even if already configured
    """
    root = logging.getLogger()
    global _configured

    if _configured and not force:
        # Only update level if explicitly requested
        if level is not None:
            resolved = _resolve_level(level)
            root.setLevel(resolved)
        return

    # Determine level from env / param
    env_level = os.getenv('LOG_LEVEL') or os.getenv('LOGGING_LEVEL')
    level = level or env_level or 'INFO'
    resolved_level = _resolve_level(level)

    # Default format string
    fmt = fmt or DEFAULT_LOG_FORMAT

    # Clear existing handlers to avoid duplicate logs
    for h in list(root.handlers):
        root.removeHandler(h)

    # Console handler (always enabled)
    stream_handler = logging.StreamHandler()
    formatter = logging.Formatter(fmt)
    stream_handler.setFormatter(formatter)
    # Traza por UUID (Issue 4.3): el filtro garantiza el campo task_id en
    # cada línea, venga de donde venga el log.
    stream_handler.addFilter(TaskIdLogFilter())
    root.addHandler(stream_handler)

    # Optional file handler with rotation (10MB, 3 backups)
    file_path = log_file or os.getenv('LOG_FILE')
    if file_path:
        fh = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=3
        )
        fh.setFormatter(formatter)
        fh.addFilter(TaskIdLogFilter())
        root.addHandler(fh)

    root.setLevel(resolved_level)

    # Suppress noisy third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("grpc").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str | None = None) -> logging.Logger:
    """Convenience wrapper for logging.getLogger."""
    return logging.getLogger(name or 'lifeos')

