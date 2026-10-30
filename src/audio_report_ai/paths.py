from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import PathError


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    audio_dir: Path
    transcripts_dir: Path
    reports_dir: Path
    exports_dir: Path
    models_dir: Path
    config_dir: Path
    cache_dir: Path
    jobs_dir: Path
    state_dir: Path
    log_dir: Path
    database_path: Path
    config_path: Path
    log_path: Path


def _xdg_dir(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable)
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate
    return fallback


def resolve_paths() -> AppPaths:
    home = Path.home()
    data_home = _xdg_dir("XDG_DATA_HOME", home / ".local" / "share")
    config_home = _xdg_dir("XDG_CONFIG_HOME", home / ".config")
    cache_home = _xdg_dir("XDG_CACHE_HOME", home / ".cache")
    state_home = _xdg_dir("XDG_STATE_HOME", home / ".local" / "state")

    data_dir = data_home / "audio-report-ai"
    audio_dir = data_dir / "audio"
    transcripts_dir = data_dir / "transcripts"
    reports_dir = data_dir / "reports"
    exports_dir = data_dir / "exports"
    models_dir = data_dir / "models"
    config_dir = config_home / "audio-report-ai"
    cache_dir = cache_home / "audio-report-ai"
    jobs_dir = cache_dir / "jobs"
    state_dir = state_home / "audio-report-ai"
    log_dir = state_dir / "logs"

    return AppPaths(
        data_dir=data_dir,
        audio_dir=audio_dir,
        transcripts_dir=transcripts_dir,
        reports_dir=reports_dir,
        exports_dir=exports_dir,
        models_dir=models_dir,
        config_dir=config_dir,
        cache_dir=cache_dir,
        jobs_dir=jobs_dir,
        state_dir=state_dir,
        log_dir=log_dir,
        database_path=data_dir / "database.sqlite3",
        config_path=config_dir / "config.json",
        log_path=log_dir / "app.log",
    )


def ensure_directories(paths: AppPaths) -> None:
    directories = (
        paths.data_dir,
        paths.audio_dir,
        paths.transcripts_dir,
        paths.reports_dir,
        paths.exports_dir,
        paths.models_dir,
        paths.config_dir,
        paths.cache_dir,
        paths.jobs_dir,
        paths.state_dir,
        paths.log_dir,
    )
    for directory in directories:
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        except OSError as exc:
            raise PathError(
                "path_unavailable",
                f"Could not prepare application directory: {directory}",
            ) from exc
