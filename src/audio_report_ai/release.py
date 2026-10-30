from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .errors import AppError

RELEASE_NAME = "audio-report-ai"
_REQUIRED_PATHS = (
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "uv.lock",
    "install.sh",
    "run.sh",
    "uninstall.sh",
    "src",
    "packaging",
)
_OPTIONAL_PATHS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "THIRD_PARTY_NOTICES.md",
    ".python-version",
    ".gitignore",
    ".gitattributes",
    "docs",
    "evaluation",
    "scripts",
    "tests",
)
_SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        "data",
        "models",
        "recordings",
        "audio",
        "transcripts",
        "reports",
        "exports",
        "node_modules",
    }
)
_SKIP_SUFFIXES = frozenset(
    {
        ".wav",
        ".mp3",
        ".m4a",
        ".flac",
        ".ogg",
        ".webm",
        ".pyc",
        ".pyo",
        ".sqlite3",
        ".db",
        ".log",
        ".gguf",
        ".bin",
        ".pt",
        ".onnx",
        ".safetensors",
    }
)
_SKIP_NAMES = frozenset({".env", ".env.local", "id_rsa", "id_ed25519"})


def _validate_version(version: str) -> str:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.-]+)?", version):
        raise AppError("release_version_invalid", "Release version is not valid.")
    return version


def _should_skip(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if any(part in _SKIP_DIRECTORIES for part in relative.parts):
        return True
    if path.name in _SKIP_NAMES or path.name.startswith(".env."):
        return True
    if path.suffix.lower() in _SKIP_SUFFIXES:
        return True
    if path.is_symlink():
        return True
    return False


@dataclass(frozen=True)
class ReleaseArtifact:
    archive_path: Path
    checksum_path: Path
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_release_archive(
    project_root: str | Path,
    output_dir: str | Path,
    version: str | None = None,
) -> ReleaseArtifact:
    root = Path(project_root).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    release_version = _validate_version(version or __version__)
    missing = [name for name in _REQUIRED_PATHS if not (root / name).exists()]
    if missing:
        raise AppError(
            "release_source_incomplete",
            f"Release source is missing required paths: {', '.join(missing)}",
        )
    for script in ("install.sh", "run.sh", "uninstall.sh"):
        if not os.access(root / script, os.X_OK):
            raise AppError(
                "release_source_incomplete",
                f"{script} must be executable before building a release.",
            )

    output.mkdir(parents=True, exist_ok=True, mode=0o755)
    archive_path = output / f"{RELEASE_NAME}-{release_version}-source.tar.gz"
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    top_level = f"{RELEASE_NAME}-{release_version}"

    paths: list[Path] = []
    for name in (*_REQUIRED_PATHS, *_OPTIONAL_PATHS):
        candidate = root / name
        if candidate.exists():
            paths.append(candidate)

    try:
        with tarfile.open(archive_path, "w:gz") as archive:
            for candidate in paths:
                if candidate.is_file():
                    if _should_skip(candidate, root):
                        continue
                    archive.add(candidate, arcname=f"{top_level}/{candidate.name}")
                    continue
                if not candidate.is_dir():
                    continue
                if candidate.name in _SKIP_DIRECTORIES:
                    continue
                for path in sorted(candidate.rglob("*")):
                    if _should_skip(path, root):
                        continue
                    if path.is_dir():
                        continue
                    archive.add(
                        path,
                        arcname=f"{top_level}/{path.relative_to(root).as_posix()}",
                    )
        digest = _sha256(archive_path)
        checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
        os.chmod(archive_path, 0o644)
        os.chmod(checksum_path, 0o644)
    except OSError as exc:
        raise AppError(
            "release_build_failed",
            f"Could not build the release archive: {archive_path}",
        ) from exc

    return ReleaseArtifact(
        archive_path=archive_path,
        checksum_path=checksum_path,
        sha256=digest,
    )


def build_python_distributions(project_root: str | Path, output_dir: str | Path) -> None:
    root = Path(project_root).expanduser().resolve()
    uv = shutil.which("uv")
    if uv is None:
        raise AppError("release_uv_missing", "uv is required to build Python distributions.")
    try:
        completed = subprocess.run(
            [uv, "build", "--out-dir", str(Path(output_dir).expanduser().resolve())],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise AppError("release_build_failed", "Could not run uv build.") from exc
    if completed.returncode != 0:
        raise AppError(
            "release_build_failed",
            "uv build failed; check the project metadata and lockfile.",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="audio-report-ai-release",
        description="Build a source release archive with a SHA-256 checksum.",
    )
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="Project checkout to archive (default: this checkout).",
    )
    parser.add_argument("--output-dir", default="dist", help="Destination directory.")
    parser.add_argument("--version", default=__version__, help="Release version.")
    parser.add_argument(
        "--with-wheel",
        action="store_true",
        help="Also run 'uv build' for wheel and sdist artifacts.",
    )
    args = parser.parse_args(argv)
    artifact = create_release_archive(args.project_root, args.output_dir, args.version)
    if args.with_wheel:
        build_python_distributions(args.project_root, args.output_dir)
    print(f"Source release: {artifact.archive_path}")
    print(f"SHA-256: {artifact.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
