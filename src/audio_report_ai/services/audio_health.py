from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AudioDeviceInfo:
    name: str
    sample_rate: float
    input_channels: int

    @property
    def display(self) -> str:
        return f"{self.name} ({self.input_channels} channel(s), {self.sample_rate:g} Hz)"


@dataclass(frozen=True)
class AudioHealth:
    ffmpeg_path: str | None
    ffprobe_path: str | None
    sounddevice_available: bool
    input_device: AudioDeviceInfo | None
    warnings: tuple[str, ...] = ()

    @property
    def import_ready(self) -> bool:
        return self.ffmpeg_path is not None and self.ffprobe_path is not None

    @property
    def recording_ready(self) -> bool:
        return self.import_ready and self.input_device is not None


def check_audio_health(
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    device: str | int | None = None,
    device_module: Any | None = None,
) -> AudioHealth:
    """Inspect the local audio stack without opening a capture stream.

    Import only needs FFmpeg/FFprobe. Recording additionally needs
    ``sounddevice`` and a usable input device.
    """

    resolved_ffmpeg = shutil.which(ffmpeg_path)
    resolved_ffprobe = shutil.which(ffprobe_path)
    warnings: list[str] = []
    if resolved_ffmpeg is None:
        warnings.append(
            "FFmpeg is not available. Import and normalisation require it; run ./install.sh."
        )
    if resolved_ffprobe is None:
        warnings.append(
            "FFprobe is not available. Import validation requires it; run ./install.sh."
        )

    module = device_module
    if module is None:
        try:
            import sounddevice as module
        except ImportError:
            warnings.append(
                "The sounddevice package is not installed. "
                "Microphone recording is unavailable; run ./install.sh."
            )
            return AudioHealth(
                ffmpeg_path=resolved_ffmpeg,
                ffprobe_path=resolved_ffprobe,
                sounddevice_available=False,
                input_device=None,
                warnings=tuple(warnings),
            )
        except OSError:
            warnings.append(
                "PortAudio could not be loaded. "
                "Microphone recording is unavailable; install libportaudio2."
            )
            return AudioHealth(
                ffmpeg_path=resolved_ffmpeg,
                ffprobe_path=resolved_ffprobe,
                sounddevice_available=False,
                input_device=None,
                warnings=tuple(warnings),
            )

    try:
        info = module.query_devices(device, "input")
        channels = int(info["max_input_channels"])
        sample_rate = float(info["default_samplerate"])
        name = str(info.get("name") or "default input")
        if channels < 1:
            raise ValueError("No input channels.")
        if sample_rate <= 0:
            raise ValueError("No usable sample rate.")
    except Exception:
        warnings.append(
            "No usable microphone input device was found. "
            "Import still works; connect a microphone to record."
        )
        return AudioHealth(
            ffmpeg_path=resolved_ffmpeg,
            ffprobe_path=resolved_ffprobe,
            sounddevice_available=True,
            input_device=None,
            warnings=tuple(warnings),
        )

    return AudioHealth(
        ffmpeg_path=resolved_ffmpeg,
        ffprobe_path=resolved_ffprobe,
        sounddevice_available=True,
        input_device=AudioDeviceInfo(
            name=name,
            sample_rate=sample_rate,
            input_channels=channels,
        ),
        warnings=tuple(warnings),
    )


def format_audio_health(health: AudioHealth) -> str:
    lines = [
        f"FFmpeg: {health.ffmpeg_path or 'not found'}",
        f"FFprobe: {health.ffprobe_path or 'not found'}",
        f"Microphone: {health.input_device.display if health.input_device else 'not available'}",
    ]
    lines.extend(f"Warning: {warning}" for warning in health.warnings)
    if health.import_ready and health.recording_ready:
        lines.append("Audio checks passed: import and microphone recording are available.")
    elif health.import_ready:
        lines.append("Audio checks passed for import. Microphone recording needs attention.")
    else:
        lines.append("Audio checks failed: FFmpeg and FFprobe are required.")
    return "\n".join(lines)
