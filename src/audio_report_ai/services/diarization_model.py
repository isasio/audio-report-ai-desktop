from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from huggingface_hub import snapshot_download

from ..errors import ModelError
from ..paths import AppPaths

DEFAULT_DIARIZATION_MODEL_NAME = "speaker-diarization-community-1"
DIARIZATION_DIR_PREFIX = "pyannote-"
DIARIZATION_REPO_PREFIX = "pyannote/"
DIARIZATION_MODEL_LICENSE = "CC-BY-4.0"
DIARIZATION_MODEL_TERMS_URL = "https://huggingface.co/pyannote/speaker-diarization-community-1"
REQUIRED_DIARIZATION_FILES = (
    "config.yaml",
    "segmentation/pytorch_model.bin",
    "embedding/pytorch_model.bin",
    "plda/plda.npz",
    "plda/xvec_transform.npz",
)
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_diarization_model_name(model_name: str) -> str:
    if not _MODEL_NAME_PATTERN.fullmatch(model_name):
        raise ModelError(
            "diarization_model_name_invalid",
            "The diarization model name is not valid.",
        )
    return model_name


def diarization_model_directory(paths: AppPaths, model_name: str) -> Path:
    validate_diarization_model_name(model_name)
    return paths.models_dir / f"{DIARIZATION_DIR_PREFIX}{model_name}"


def missing_diarization_files(model_dir: Path) -> list[str]:
    return [name for name in REQUIRED_DIARIZATION_FILES if not (model_dir / name).is_file()]


class DiarizationModelLocator:
    def __init__(
        self,
        paths: AppPaths,
        model_name: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._model_name = validate_diarization_model_name(model_name)
        self._logger = logger or logging.getLogger(__name__)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_dir(self) -> Path:
        return diarization_model_directory(self._paths, self._model_name)

    def locate(self) -> Path:
        root = self._paths.models_dir.resolve()
        model_dir = self.model_dir.resolve()
        if not model_dir.is_relative_to(root):
            raise ModelError(
                "diarization_model_path_invalid",
                "The local diarization model path is not valid.",
            )
        if not model_dir.is_dir():
            raise ModelError(
                "diarization_model_missing",
                (
                    "The local speaker diarization model is not installed. "
                    "Accept the model terms and run: "
                    "HF_TOKEN=<token> uv run python -m audio_report_ai "
                    f"--setup-diarization --diarization-model {self._model_name}"
                ),
            )
        missing = missing_diarization_files(model_dir)
        if missing:
            raise ModelError(
                "diarization_model_incomplete",
                "The local diarization model is incomplete. Re-run the setup command.",
            )
        return model_dir

    def is_available(self) -> bool:
        try:
            self.locate()
        except ModelError:
            return False
        return True


class DiarizationModelSetupService:
    def __init__(
        self,
        paths: AppPaths,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = paths
        self._logger = logger or logging.getLogger(__name__)

    def setup(self, model_name: str) -> Path:
        validate_diarization_model_name(model_name)
        target = diarization_model_directory(self._paths, model_name)
        if target.is_dir():
            missing = missing_diarization_files(target)
            if missing:
                raise ModelError(
                    "diarization_model_incomplete",
                    (
                        "The existing local diarization model directory is incomplete. "
                        "Remove it and run the setup command again."
                    ),
                )
            try:
                _secure_tree(target)
            except OSError as exc:
                raise ModelError(
                    "diarization_setup_failed",
                    "Could not secure the existing diarization model directory.",
                ) from exc
            return target

        try:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(target.parent, 0o700)
        except OSError as exc:
            raise ModelError(
                "diarization_setup_failed",
                "Could not prepare the diarization model directory.",
            ) from exc

        temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        self._logger.info("diarization_model_setup_started model=%s", model_name)
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        try:
            snapshot_download(
                repo_id=f"{DIARIZATION_REPO_PREFIX}{model_name}",
                local_dir=temporary,
                ignore_patterns=["*.gif"],
                token=os.environ.get("HF_TOKEN"),
                max_workers=4,
            )
            missing = missing_diarization_files(temporary)
            if missing:
                raise ModelError(
                    "diarization_model_incomplete",
                    "The downloaded diarization model is incomplete.",
                )
            _secure_tree(temporary)
            os.replace(temporary, target)
        except ModelError:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            raise ModelError(
                "diarization_model_download_failed",
                (
                    "Could not download the diarization model. Accept the model terms at "
                    f"{DIARIZATION_MODEL_TERMS_URL}, set HF_TOKEN, and retry the setup command."
                ),
            ) from exc

        self._logger.info("diarization_model_setup_complete model=%s", model_name)
        return target


def _secure_tree(root: Path) -> None:
    os.chmod(root, 0o700)
    for path in root.rglob("*"):
        if path.is_dir():
            os.chmod(path, 0o700)
        elif path.is_file():
            os.chmod(path, 0o600)
