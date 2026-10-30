from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from .errors import LoggingError
from .paths import AppPaths

LOGGER_NAME = "audio_report_ai"


def configure_logging(paths: AppPaths, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level))
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    try:
        paths.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(paths.log_dir, 0o700)
        paths.log_path.touch(mode=0o600, exist_ok=True)
        os.chmod(paths.log_path, 0o600)
        file_handler = RotatingFileHandler(
            paths.log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        os.chmod(paths.log_path, 0o600)
    except OSError as exc:
        raise LoggingError(
            "logging_unavailable",
            f"Could not prepare log file: {paths.log_path}",
        ) from exc

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler.setFormatter(formatter)
    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stderr_handler)
    return logger
