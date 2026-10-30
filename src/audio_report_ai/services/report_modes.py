from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..atomic_json import write_json_atomic
from ..errors import AppError

MODE_FILE_NAME = "report_modes.json"
REPORT_MODE_SCHEMA_VERSION = 1


class ReportModeError(AppError):
    pass


class ReportMode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(min_length=1, max_length=8_000)
    profile: Literal["conversation", "veterinary_clinical"] = "conversation"
    language: Literal["auto", "es", "en", "pt", "fr", "de", "it"] = "auto"
    length: Literal["short", "normal", "long", "extensive"] = "normal"


class ReportModeStore:
    """Local, user-editable report-writing modes."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser().resolve()

    @property
    def path(self) -> Path:
        return self._path

    def list_modes(self) -> list[ReportMode]:
        if not self._path.exists():
            return []
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ReportModeError(
                "report_modes_unreadable",
                f"Could not read saved report modes: {self._path}",
            ) from exc
        except json.JSONDecodeError as exc:
            raise ReportModeError(
                "report_modes_invalid",
                f"Saved report modes are not valid JSON: {self._path}",
            ) from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ReportModeError(
                "report_modes_invalid",
                f"Saved report modes have an unsupported format: {self._path}",
            )
        modes = payload.get("modes")
        if not isinstance(modes, list):
            raise ReportModeError(
                "report_modes_invalid",
                f"Saved report modes have an unsupported format: {self._path}",
            )
        try:
            parsed = [ReportMode.model_validate(item) for item in modes]
        except ValidationError as exc:
            raise ReportModeError(
                "report_modes_invalid",
                f"Saved report modes contain an invalid entry: {self._path}",
            ) from exc
        names = [mode.name for mode in parsed]
        if len(names) != len(set(names)):
            raise ReportModeError(
                "report_modes_invalid",
                f"Saved report modes contain duplicate names: {self._path}",
            )
        return sorted(parsed, key=lambda mode: mode.name.casefold())

    def save_mode(self, mode: ReportMode) -> None:
        modes = {item.name: item for item in self.list_modes()}
        modes[mode.name] = mode
        self._write([modes[name] for name in sorted(modes, key=str.casefold)])

    def delete_mode(self, name: str) -> bool:
        modes = {item.name: item for item in self.list_modes()}
        if name not in modes:
            return False
        del modes[name]
        self._write([modes[item] for item in sorted(modes, key=str.casefold)])
        return True

    def _write(self, modes: list[ReportMode]) -> None:
        payload = {
            "schema_version": REPORT_MODE_SCHEMA_VERSION,
            "modes": [mode.model_dump(mode="json") for mode in modes],
        }
        try:
            write_json_atomic(self._path, payload)
        except OSError as exc:
            raise ReportModeError(
                "report_modes_write_failed",
                f"Could not save report modes: {self._path}",
            ) from exc


__all__ = [
    "MODE_FILE_NAME",
    "ReportMode",
    "ReportModeError",
    "ReportModeStore",
]
