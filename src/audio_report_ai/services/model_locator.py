from __future__ import annotations

import logging
import re
from pathlib import Path

from ..errors import ModelError
from ..paths import AppPaths

DEFAULT_MODEL_NAME = "large-v3"
MODEL_DIR_PREFIX = "faster-whisper-"
REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json")
VOCABULARY_FILES = ("vocabulary.json", "vocabulary.txt")
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_model_name(model_name: str) -> str:
    if not _MODEL_NAME_PATTERN.fullmatch(model_name):
        raise ModelError(
            "model_name_invalid",
            "The transcription model name is not valid.",
        )
    return model_name


def model_directory(paths: AppPaths, model_name: str) -> Path:
    validate_model_name(model_name)
    return paths.models_dir / f"{MODEL_DIR_PREFIX}{model_name}"


def missing_model_files(model_dir: Path) -> list[str]:
    missing = [name for name in REQUIRED_MODEL_FILES if not (model_dir / name).is_file()]
    if not any((model_dir / name).is_file() for name in VOCABULARY_FILES):
        missing.append("vocabulary.json or vocabulary.txt")
    return missing


class ModelLocator:
    def __init__(
        self,
        paths: AppPaths,
        model_name: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._model_name = validate_model_name(model_name)
        self._logger = logger or logging.getLogger(__name__)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_dir(self) -> Path:
        return model_directory(self._paths, self._model_name)

    def locate(self) -> Path:
        root = self._paths.models_dir.resolve()
        model_dir = self.model_dir.resolve()
        if not model_dir.is_relative_to(root):
            raise ModelError(
                "model_path_invalid",
                "The local transcription model path is not valid.",
            )
        if not model_dir.is_dir():
            raise ModelError(
                "model_missing",
                (
                    "The local transcription model is not installed. "
                    "Run: uv run python -m audio_report_ai "
                    f"--setup-models --model {self._model_name}"
                ),
            )
        missing = missing_model_files(model_dir)
        if missing:
            raise ModelError(
                "model_incomplete",
                (
                    "The local transcription model is incomplete. "
                    "Re-run the model setup command before transcribing."
                ),
            )
        return model_dir

    def is_available(self) -> bool:
        try:
            self.locate()
        except ModelError:
            return False
        return True
