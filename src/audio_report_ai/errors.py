class AppError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return self.message


class AudioImportError(AppError):
    pass


class AudioRecordingError(AppError):
    pass


class AudioNormalizationError(AppError):
    pass


class ConfigError(AppError):
    pass


class DatabaseError(AppError):
    pass


class DiarizationError(AppError):
    pass


class ExportError(AppError):
    pass


class JobError(AppError):
    pass


class LocalLLMError(AppError):
    pass


class LoggingError(AppError):
    pass


class ModelError(AppError):
    pass


class PathError(AppError):
    pass


class ReportError(AppError):
    pass


class SelfCheckError(AppError):
    pass


class SessionError(AppError):
    pass


class SpeakerError(AppError):
    pass


class TranscriptionError(AppError):
    pass
