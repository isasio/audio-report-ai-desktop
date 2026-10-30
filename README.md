# Audio Report AI

Local-first Ubuntu desktop application for recording or importing audio,
transcribing it on your own machine, reviewing the transcript, and producing
structured reports for human approval.

All runtime processing stays local. There are no cloud APIs, analytics,
telemetry, auto-update checks, or hidden downloads. The only expected local HTTP
endpoint is Ollama at `http://127.0.0.1:11434`.

This is the public install-and-run repository. It contains the application
runtime, installer, desktop assets, and user documentation needed to install and
use the app. Model weights are not bundled; setup is explicit and documented.

## Features

- Import WAV, MP3, M4A, FLAC, or OGG, or record from the microphone.
- Keep the original audio untouched and create a normalised mono 16 kHz copy.
- Transcribe locally with faster-whisper using app-managed model weights.
- Transcribe in Spanish by default, or use automatic language detection.
- Optionally diarize speakers locally with pyannote and assign names/roles manually.
- Edit corrected transcript text while preserving the raw transcript.
- Read the whole transcript with optional timestamps, copy, or save it as text.
- Seek playback to any transcript segment.
- Generate structured reports through loopback-only Ollama.
- Choose the local report model per report: Qwen, LFM3, or another installed model.
- Choose report language and length: short, normal, long, or extensive.
- Write custom report instructions and save them as reusable modes.
- Use a general conversation profile or a formal veterinary clinical profile.
- Validate report evidence against transcript segment IDs.
- Edit drafts, review flags, approve explicitly, and keep version history.
- Export PDF, DOCX, HTML, or structured JSON locally.
- Search sessions and delete all session-owned data.
- Run offline after the explicit local model setup.

## Requirements

- Ubuntu 24.04
- Python 3.12 through [`uv`](https://docs.astral.sh/uv/)
- FFmpeg and FFprobe
- Qt runtime libraries:
  `libgl1`, `libegl1`, `libxkbcommon-x11-0`, `libxcb-cursor0`
- PortAudio (`libportaudio2`) and a working input device for recording
- Disk space for models; examples:
  - faster-whisper `small`: about 0.5 GB
  - faster-whisper `large-v3`: about 3.1 GB
  - Ollama report model `qwen3.5:4b`: about 3.3 GB
  - Ollama report model `lfm3:1.7b`: about 1.1 GB
- Optional Ollama for report generation
- Optional CUDA 12/cuDNN 9 for GPU transcription; CPU always works
- Optional pyannote dependencies and gated terms for diarization

## Install

Download the runtime bundle from
[GitHub Releases](https://github.com/isasio/audio-report-ai-desktop/releases),
extract it, and run the installer:

```bash
tar -xzf audio-report-ai-0.1.0-runtime.tar.gz
cd audio-report-ai-0.1.0
./install.sh
```

Or clone the repository:

```bash
git clone https://github.com/isasio/audio-report-ai-desktop.git
cd audio-report-ai-desktop
./install.sh
```

`install.sh` checks Ubuntu and system dependencies, creates the isolated Python
environment, prepares the XDG data/config directories, installs a desktop
launcher, and runs the offline self-checks. It does not download model weights by
default.

### Installer options

```bash
./install.sh --check-only
./install.sh --no-desktop
./install.sh --model small                 # explicit faster-whisper download
./install.sh --report-model qwen3.5:4b     # explicit Ollama model download
./install.sh --report-model lfm3:1.7b      # light report model
./install.sh --with-diarization            # optional Python dependencies only
./install.sh --configure-ollama            # loopback/cloud-disabled systemd helper
```

For a fully explicit CPU-first setup with Ollama already installed:

```bash
./install.sh --model small --report-model qwen3.5:4b
```

If system packages are missing, the installer prints the exact `apt-get`
command and exits without changing the system.

## Set up local models

Models are never downloaded automatically at runtime.

### Speech-to-text

```bash
uv run python -m audio_report_ai --setup-models --model small
uv run python -m audio_report_ai --check-models --model small
```

Use `large-v3` instead of `small` for higher accuracy if you have enough disk and
memory. Model weights are stored under
`~/.local/share/audio-report-ai/models/` and are loaded only from local paths.

### Report model

Install Ollama separately, keep it on loopback, and download one model
explicitly:

```bash
uv run python -m audio_report_ai --setup-report-model --report-model qwen3.5:4b
uv run python -m audio_report_ai --check-ollama --report-model qwen3.5:4b
```

LFM3 is lighter:

```bash
uv run python -m audio_report_ai --setup-report-model --report-model lfm3:1.7b
uv run python -m audio_report_ai --check-ollama --report-model lfm3:1.7b
```

LFM3 requires a current Ollama release. See [docs/model_setup.md](docs/model_setup.md)
for licenses and presets.

### Optional speaker diarization

```bash
uv sync --frozen --no-dev --extra diarization
```

Accept the CC-BY-4.0 model terms, then run:

```bash
HF_TOKEN=<token> uv run python -m audio_report_ai --setup-diarization
uv run python -m audio_report_ai --check-diarization
```

## Run

```bash
./run.sh
```

`run.sh` sets offline environment variables, checks local readiness, tries to
start a user-level Ollama unit if present, and launches the GUI. It never installs
packages or downloads models.

You can also use the installed desktop launcher.

## Basic use

1. Create a session.
2. Import audio or record from the microphone.
3. Choose **File > Transcribe** (`Ctrl+T`). The app normalises the audio and runs
   faster-whisper locally.
4. Review the transcript. Edit the corrected text and save; raw text is preserved.
   Select a segment to seek playback.
5. Optionally choose **Speakers > Diarize Speakers**, then assign names/roles.
6. Choose **Report > Generate Report** (`Ctrl+G`) for the configured defaults.
7. Or use **Report > Generate Report with Model...** (`Ctrl+Shift+G`) to choose a
   model, report profile, language, length, and writing instructions.
8. Review the draft and evidence links, then approve explicitly.
9. Export PDF/DOCX/HTML or structured JSON.

Reports are drafts until approval. Approval freezes that version. A new
generation creates a new version.

## Reports and clinical use

- General conversation reports include summaries, topics, decisions, action
  items, questions, and evidence.
- The veterinary clinical profile adds patient/visit fields and clinical
  sections, but remains a documentation aid, not a diagnosis.
- Writing instructions are style/format guidance. They cannot override evidence,
  safety, approval, or schema requirements.
- Saved modes store instructions plus report type/language/length locally.
- Structured JSON export is intended for local clinical-system import.
- The app does not upload automatically to a remote clinical database.

## Verify the installation

```bash
uv run python -m audio_report_ai --self-check
uv run python -m audio_report_ai --check-audio
uv run python -m audio_report_ai --runtime-check
uv run python -m audio_report_ai --offline-smoke-test
uv run python -m audio_report_ai --setup-guide
```

The deterministic offline smoke test uses synthetic audio and the real local
database/report/export code. It does not test ASR/LLM quality. A live local-model
smoke test can be run with:

```bash
uv run python -m audio_report_ai --live-smoke-test /path/to/speech.wav
```

See [docs/offline_smoke.md](docs/offline_smoke.md) for the manual disconnected
network test.

## Data and privacy

Runtime data stays outside the repository:

- Data, database, audio, transcripts, reports, exports, models:
  `~/.local/share/audio-report-ai/`
- Configuration and saved report modes: `~/.config/audio-report-ai/`
- Job scratch files: `~/.cache/audio-report-ai/jobs/`
- Logs: `~/.local/state/audio-report-ai/logs/`

Directories use `0700`; sensitive files use `0600`. The app stores no API keys.
Logs do not intentionally contain transcript/report text. See
[docs/privacy.md](docs/privacy.md).

## Uninstall

```bash
./uninstall.sh
```

This removes only the desktop launcher and icon. `./uninstall.sh --purge` asks
for confirmation and deletes all app-managed XDG data, including recordings,
transcripts, reports, and exports. It does not delete the checkout or `.venv`.

## Troubleshooting

- **Missing FFmpeg/Qt libraries:** run `./install.sh`; it prints the exact
  `apt-get install` command.
- **Speech model missing:** `uv run python -m audio_report_ai --setup-models
  --model small`.
- **Ollama unavailable:** start Ollama on `127.0.0.1:11434` and run
  `uv run python -m audio_report_ai --check-ollama`.
- **LFM3 setup fails with HTTP 412:** upgrade Ollama, then retry the setup.
- **No microphone:** import still works; check `--check-audio`.
- **PDF/DOCX export fails:** verify the report has a valid draft/approved status
  and the export directory is writable.

More detail is in:

- [Installation and operation](docs/installation.md)
- [Local model setup](docs/model_setup.md)
- [Offline verification](docs/offline_smoke.md)
- [Privacy and reliability](docs/privacy.md)

## Known limitations

- `large-v3` GPU performance and memory use are not benchmarked on this host.
- Real pyannote diarization requires optional dependencies and gated weights;
  diarization accuracy is not yet benchmarked.
- A true disconnected-network GUI acceptance test is still pending.
- Report editing uses a JSON dialog rather than a structured form.
- Session search matches titles/transcript text only, does not fold accents, and
  returns at most 200 results.
- Report generation and speaker attribution are probabilistic and require human
  review.
- Direct upload to a remote clinical database is not implemented.
- No PyInstaller/AppImage/`.deb` bundle is provided yet.

## License

Application source: MIT. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Model weights and third-party
dependencies remain under their own licenses and are not bundled.
