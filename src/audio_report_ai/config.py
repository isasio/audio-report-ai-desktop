from __future__ import annotations

import json
import os
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ConfigError
from .paths import AppPaths


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["es", "auto"] = "es"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    whisper_model: str = Field(
        default="large-v3",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    whisper_device: Literal["auto", "cpu", "cuda"] = "auto"
    diarization_model: str = Field(
        default="speaker-diarization-community-1",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    diarization_device: Literal["auto", "cpu", "cuda"] = "auto"
    report_model: str = Field(
        default="qwen3.5:4b",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    report_language: Literal["auto", "es", "en", "pt", "fr", "de", "it"] = "auto"
    report_length: Literal["short", "normal", "long", "extensive"] = "normal"
    report_profile: Literal["conversation", "veterinary_clinical"] = "conversation"


def load_config(paths: AppPaths) -> AppConfig:
    path = paths.config_path
    if not path.exists():
        config = AppConfig()
        save_config(config, paths)
        return config

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("config_unreadable", f"Could not read config file: {path}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "config_invalid_json",
            f"Config file is not valid JSON: {path}",
        ) from exc

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError("config_invalid", f"Config file has invalid values: {path}") from exc


def save_config(config: AppConfig, paths: AppPaths) -> None:
    path = paths.config_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        payload = json.dumps(config.model_dump(), indent=2, sort_keys=True) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
    except OSError as exc:
        raise ConfigError(
            "config_write_failed",
            f"Could not save config file: {path}",
        ) from exc
