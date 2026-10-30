# Third-party notices

The application source is licensed under the MIT License; see `LICENSE`.

The following are separate and are not covered by the application's MIT
license. They remain subject to their own licenses and terms:

- faster-whisper and its model weights
- FFmpeg and FFprobe
- Ollama and the local report models (for example Qwen and LFM3)
- pyannote.audio and the gated pyannote diarization model
- PySide6/Qt and other Python package dependencies
- Python itself and system libraries such as PortAudio and the X11/Qt stack

Model weights are never bundled with this repository. Model setup is an explicit
user action and is documented in `docs/model_setup.md`; runtime never downloads
models.

Before redistributing an application bundle that includes model weights, verify
the exact upstream model card, license, attribution requirements, and any
commercial-use limits.
