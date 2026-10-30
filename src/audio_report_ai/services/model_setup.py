from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path

from huggingface_hub import snapshot_download

from ..errors import ModelError
from ..paths import AppPaths
from .model_locator import missing_model_files, model_directory, validate_model_name

MODEL_REPO_PREFIX = "Systran/faster-whisper-"
MODEL_FILE_PATTERNS = [
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
    "vocabulary.txt",
]


class ModelSetupService:
    def __init__(
        self,
        paths: AppPaths,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._logger = logger or logging.getLogger(__name__)

    def setup(self, model_name: str) -> Path:
        validate_model_name(model_name)
        target = model_directory(self._paths, model_name)
        if target.is_dir():
            missing = missing_model_files(target)
            if missing:
                raise ModelError(
                    "model_incomplete",
                    (
                        "The existing local model directory is incomplete. "
                        "Remove it and run the model setup command again."
                    ),
                )
            try:
                _secure_tree(target)
            except OSError as exc:
                raise ModelError(
                    "model_setup_failed",
                    "Could not secure the existing local model directory.",
                ) from exc
            return target

        try:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(target.parent, 0o700)
        except OSError as exc:
            raise ModelError(
                "model_setup_failed",
                "Could not prepare the local model directory.",
            ) from exc

        temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        self._logger.info("model_setup_started model=%s", model_name)
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        try:
            snapshot_download(
                repo_id=f"{MODEL_REPO_PREFIX}{model_name}",
                local_dir=temporary,
                allow_patterns=MODEL_FILE_PATTERNS,
                max_workers=4,
            )
            missing = missing_model_files(temporary)
            if missing:
                raise ModelError(
                    "model_download_incomplete",
                    "The downloaded model is incomplete. Run the model setup command again.",
                )
            _secure_tree(temporary)
            os.replace(temporary, target)
        except ModelError:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            raise ModelError(
                "model_download_failed",
                (
                    "Could not download the local transcription model. "
                    "Check the network connection and run the model setup command again."
                ),
            ) from exc

        self._logger.info("model_setup_complete model=%s", model_name)
        return target


def _secure_tree(root: Path) -> None:
    os.chmod(root, 0o700)
    for path in root.rglob("*"):
        if path.is_dir():
            os.chmod(path, 0o700)
        elif path.is_file():
            os.chmod(path, 0o600)
