"""Constellation logging configuration.

Dual output: JSON lines to rotating file + human-readable to stderr.

Usage:
    from core.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Search query", extra={'query': 'test', 'results': 5})
"""

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone

from core.config import DATA_DIR

LOG_DIR = os.path.join(DATA_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'constellation.log')


class JSONFormatter(logging.Formatter):
    """Formats log records as JSON lines for file output."""

    def format(self, record):
        entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        # Include any extra fields passed via extra={}
        for key in ('query', 'results', 'conversation_id', 'note_id',
                     'duration_ms', 'top_k', 'provider', 'error',
                     'endpoint', 'method', 'status_code', 'port',
                     'conversations', 'messages'):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        if record.exc_info and record.exc_info[0]:
            entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(entry)


class StderrFormatter(logging.Formatter):
    """Human-readable format for stderr output."""

    def format(self, record):
        ts = datetime.now().strftime('%H:%M:%S')
        return f"[{ts}] {record.levelname:<7} {record.name}: {record.getMessage()}"


_configured = False


def setup_logging(level=None):
    """Configure root logging with file and stderr handlers."""
    global _configured
    if _configured:
        return
    _configured = True

    if level is None:
        level_name = os.environ.get('CONSTELLATION_LOG_LEVEL', 'INFO')
        level = getattr(logging, level_name.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Stderr handler (human-readable)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(StderrFormatter())
    stderr_handler.setLevel(level)
    root.addHandler(stderr_handler)

    # File handler (JSON lines, rotating)
    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3,
        encoding='utf-8',
    )
    file_handler.setFormatter(JSONFormatter())
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    # Silence noisy third-party loggers
    for name in ('sentence_transformers', 'transformers', 'torch',
                 'httpx', 'uvicorn.access', 'httpcore', 'urllib3'):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name):
    """Get a named logger, ensuring logging is configured."""
    setup_logging()
    return logging.getLogger(name)
