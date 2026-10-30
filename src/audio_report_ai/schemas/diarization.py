from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DIARIZATION_SCHEMA_VERSION = 1
_SPEAKER_PATTERN = r"^SPEAKER_[0-9]{2,}$"


class DiarizationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    speaker_id: str = Field(min_length=1, max_length=64, pattern=_SPEAKER_PATTERN)
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def _validate_interval(self) -> DiarizationTurn:
        if self.end <= self.start:
            raise ValueError("Diarization turn end must be after its start.")
        return self


class DiarizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = DIARIZATION_SCHEMA_VERSION
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    model: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    device: str = Field(min_length=1, max_length=32)
    created_at: str = Field(min_length=1, max_length=64)
    duration_seconds: float = Field(gt=0)
    turns: list[DiarizationTurn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_turns(self) -> DiarizationResult:
        for turn in self.turns:
            if turn.end > self.duration_seconds + 1.0:
                raise ValueError("Diarization turn is outside the audio duration.")
        return self
