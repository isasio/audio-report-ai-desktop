# Changelog

## 0.1.0 - 2026-10-30

Initial public release.

### Included

- Local audio import and microphone recording
- FFmpeg normalisation to mono 16 kHz PCM
- Local faster-whisper transcription with app-managed offline weights
- Optional local pyannote diarization and manual speaker roles
- Transcript editing with separate raw/corrected text and playback seeking
- Local structured reports through loopback-only Ollama
- General conversation and formal veterinary clinical report profiles
- Per-report model, language, length, and custom writing instructions
- Reusable saved writing modes
- Evidence validation, draft editing, version history, and human approval
- DOCX, HTML, PDF, and structured JSON export
- Readable full-transcript view with copy and text export
- Session search and confirmed deletion
- Offline runtime readiness checks, setup guide, and installer/desktop launcher
- Deterministic and live local-model smoke tests
- Evaluation corpus starter manifest and scoring tools
- Source-release and Python wheel packaging

### Known limitations

- Final disconnected-network GUI acceptance on the target machine is pending.
- Real pyannote diarization weights were not installed/tested on the reference host.
- The evaluation corpus contains no committed real recordings or measured results.
- Direct upload to a remote/cloud clinical database is not implemented; structured
  JSON export is the local handoff.
- Length, language, and clinical structure are model instructions; output varies,
  especially with smaller models, and requires human review.
- No PyInstaller/AppImage/`.deb` bundle is provided yet.
