from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..errors import AppError

DESKTOP_FILENAME = "audio-report-ai.desktop"
ICON_FILENAME = "audio-report-ai.svg"
DESKTOP_TEMPLATE_PATH = Path("packaging") / "audio-report-ai.desktop.in"
ICON_TEMPLATE_PATH = Path("packaging") / ICON_FILENAME


class DesktopIntegrationError(AppError):
    pass


@dataclass(frozen=True)
class DesktopInstallResult:
    desktop_path: Path
    icon_path: Path


def _escape_exec_value(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
        .replace("%", "%%")
    )
    return f'"{escaped}"'


def _escape_string_value(value: str) -> str:
    return value.replace("\n", " ").replace("\r", " ")


def desktop_entry_text(project_root: str | Path, icon_path: str | Path) -> str:
    root = Path(project_root).expanduser().resolve()
    icon = Path(icon_path).expanduser().resolve()
    template_path = root / DESKTOP_TEMPLATE_PATH
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DesktopIntegrationError(
            "desktop_template_missing",
            f"Desktop launcher template is missing: {template_path}",
        ) from exc

    values = {
        "@EXEC@": _escape_exec_value(str(root / "run.sh")),
        "@WORKDIR@": _escape_string_value(str(root)),
        "@ICON@": _escape_string_value(str(icon)),
    }
    text = template
    for token, value in values.items():
        if token not in text:
            raise DesktopIntegrationError(
                "desktop_template_invalid",
                f"Desktop launcher template is missing the {token} placeholder.",
            )
        text = text.replace(token, value)
    if any(token in text for token in values):
        raise DesktopIntegrationError(
            "desktop_template_invalid",
            "Desktop launcher template contains an unresolved placeholder.",
        )
    return text


def _default_data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME")
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate
    return Path.home() / ".local" / "share"


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def install_desktop_entry(
    project_root: str | Path,
    data_home: str | Path | None = None,
) -> DesktopInstallResult:
    root = Path(project_root).expanduser().resolve()
    run_script = root / "run.sh"
    if not run_script.is_file():
        raise DesktopIntegrationError(
            "desktop_project_invalid",
            (
                "Cannot install the desktop launcher because run.sh was not found. "
                "Run this command from a source checkout or extracted release."
            ),
        )
    if not os.access(run_script, os.X_OK):
        raise DesktopIntegrationError(
            "desktop_project_invalid",
            "run.sh is not executable. Run: chmod +x run.sh",
        )

    share_root = Path(data_home).expanduser() if data_home is not None else _default_data_home()
    applications_dir = share_root / "applications"
    icons_dir = share_root / "icons" / "hicolor" / "scalable" / "apps"
    icon_path = icons_dir / ICON_FILENAME
    desktop_path = applications_dir / DESKTOP_FILENAME

    try:
        icon_template = (root / ICON_TEMPLATE_PATH).read_text(encoding="utf-8")
    except OSError as exc:
        raise DesktopIntegrationError(
            "desktop_icon_missing",
            f"Desktop launcher icon is missing: {root / ICON_TEMPLATE_PATH}",
        ) from exc

    entry = desktop_entry_text(root, icon_path)
    try:
        _atomic_write(icon_path, icon_template, 0o644)
        _atomic_write(desktop_path, entry, 0o644)
    except OSError as exc:
        raise DesktopIntegrationError(
            "desktop_install_failed",
            "Could not write the desktop launcher files.",
        ) from exc

    return DesktopInstallResult(desktop_path=desktop_path, icon_path=icon_path)


def uninstall_desktop_entry(data_home: str | Path | None = None) -> tuple[Path, ...]:
    share_root = Path(data_home).expanduser() if data_home is not None else _default_data_home()
    candidates = (
        share_root / "applications" / DESKTOP_FILENAME,
        share_root / "icons" / "hicolor" / "scalable" / "apps" / ICON_FILENAME,
    )
    removed: list[Path] = []
    for path in candidates:
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                removed.append(path)
        except OSError as exc:
            raise DesktopIntegrationError(
                "desktop_uninstall_failed",
                f"Could not remove desktop launcher file: {path}",
            ) from exc
    return tuple(removed)
