"""
Centralized logging configuration for coreAI.

Usage:
    from src.logging_config import configure_logging, get_logger
    configure_logging()  # idempotent by default
    logger = get_logger(__name__)

Environment variables (optional):
- LOG_LEVEL (e.g., INFO, DEBUG)
- LOG_FILE (path to log file, optional)
"""

import logging
import os
from typing import Optional
def _resolve_level(level: str | int) -> int:
    """Convert level string/int to logging integer level."""
    if isinstance(level, int):
        return level
    try:
        return getattr(logging, str(level).upper())
    except Exception:
        return logging.INFO


_configured = False

def configure_logging(
    level: Optional[str | int] = None,
    log_file: Optional[str] = None,
    fmt: Optional[str] = None,
    force: bool = False
) -> None:
    """Configure root logger once for the application.

    - level: logging level (numeric or string like 'INFO')
    - log_file: optional path to a rotating file handler
    - fmt: if not provided, defaults to '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    - force: if True reconfigures even if already configured

    Default format: '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
    fmt = fmt or '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Clear existing handlers to avoid duplicate logs
    for h in list(root.handlers):
        root.removeHandler(h)

    # Console handler (always enabled)
    stream_handler = logging.StreamHandler()
    formatter = logging.Formatter(fmt)
    stream_handler.setFormatter(formatter)
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

