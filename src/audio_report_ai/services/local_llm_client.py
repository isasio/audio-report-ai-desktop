from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from ..errors import LocalLLMError

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_TAGS_PATH = "/api/tags"
OLLAMA_GENERATE_PATH = "/api/generate"
DEFAULT_TIMEOUT_SECONDS = 600.0
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")

_OPENER = build_opener(ProxyHandler({}))


def validate_report_model_name(model_name: str) -> str:
    if len(model_name) > 128 or not _MODEL_NAME_PATTERN.fullmatch(model_name):
        raise LocalLLMError(
            "ollama_model_invalid",
            "The report model name is not valid.",
        )
    return model_name


class LocalLLMClient:
    def __init__(
        self,
        model_name: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._model_name = validate_report_model_name(model_name)
        if timeout_seconds <= 0:
            raise ValueError("Ollama timeout must be positive.")
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener or _OPENER
        self._logger = logger or logging.getLogger(__name__)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def base_url(self) -> str:
        return OLLAMA_BASE_URL

    def list_models(self) -> list[str]:
        payload = self._request_json("GET", OLLAMA_TAGS_PATH)
        models = payload.get("models")
        if not isinstance(models, list):
            raise LocalLLMError(
                "ollama_invalid_response",
                "Ollama returned an invalid model list.",
            )
        names: list[str] = []
        for entry in models:
            if not isinstance(entry, dict):
                raise LocalLLMError(
                    "ollama_invalid_response",
                    "Ollama returned an invalid model list.",
                )
            name = entry.get("name", entry.get("model"))
            if not isinstance(name, str) or not name:
                raise LocalLLMError(
                    "ollama_invalid_response",
                    "Ollama returned an invalid model list.",
                )
            names.append(name)
        return names

    def verify_model(self) -> None:
        names = self.list_models()
        if not any(self._matches(name) for name in names):
            raise LocalLLMError(
                "ollama_model_missing",
                (
                    f"Ollama model '{self._model_name}' is not installed. "
                    f"Run: ollama pull {self._model_name}. "
                    "Runtime never downloads models automatically."
                ),
            )
        self._logger.info("ollama_model_verified model=%s", self._model_name)

    def is_available(self) -> bool:
        try:
            self.verify_model()
        except LocalLLMError:
            return False
        return True

    def generate(
        self,
        prompt: str,
        system: str,
        schema: Mapping[str, Any] | None = None,
        options: Mapping[str, Any] | None = None,
        think: bool = False,
    ) -> dict[str, Any]:
        if not prompt.strip() or not system.strip():
            raise LocalLLMError(
                "ollama_request_invalid",
                "The report prompt and system instructions must not be empty.",
            )
        payload: dict[str, Any] = {
            "model": self._model_name,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "think": think,
        }
        if schema is not None:
            payload["format"] = dict(schema)
        if options is not None:
            payload["options"] = dict(options)
        response = self._request_json("POST", OLLAMA_GENERATE_PATH, payload)
        output = response.get("response")
        if not isinstance(output, str) or not output.strip():
            raise LocalLLMError(
                "ollama_invalid_response",
                "Ollama returned an empty report response.",
            )
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError as exc:
            raise LocalLLMError(
                "ollama_invalid_json",
                "Ollama returned output that is not valid JSON.",
            ) from exc
        if not isinstance(decoded, dict):
            raise LocalLLMError(
                "ollama_invalid_json",
                "Ollama returned report output that is not a JSON object.",
            )
        return decoded

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{OLLAMA_BASE_URL}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise LocalLLMError(
                "ollama_http_error",
                f"Ollama returned HTTP status {exc.code}.",
            ) from exc
        except TimeoutError as exc:
            raise LocalLLMError(
                "ollama_timeout",
                "The local Ollama request timed out.",
            ) from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise LocalLLMError(
                    "ollama_timeout",
                    "The local Ollama request timed out.",
                ) from exc
            raise LocalLLMError(
                "ollama_unavailable",
                (
                    f"Could not reach the local Ollama service at {OLLAMA_BASE_URL}. "
                    "Start Ollama and ensure it listens only on loopback."
                ),
            ) from exc
        except OSError as exc:
            raise LocalLLMError(
                "ollama_unavailable",
                (
                    f"Could not reach the local Ollama service at {OLLAMA_BASE_URL}. "
                    "Start Ollama and ensure it listens only on loopback."
                ),
            ) from exc

        if len(body) > MAX_RESPONSE_BYTES:
            raise LocalLLMError(
                "ollama_response_too_large",
                "Ollama returned a response that is too large.",
            )
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalLLMError(
                "ollama_invalid_response",
                "Ollama returned an invalid response.",
            ) from exc
        if not isinstance(decoded, dict):
            raise LocalLLMError(
                "ollama_invalid_response",
                "Ollama returned an invalid response.",
            )
        error = decoded.get("error")
        if isinstance(error, str) and error.strip():
            raise LocalLLMError(
                "ollama_request_failed",
                "Ollama rejected the local request.",
            )
        return decoded

    def _matches(self, candidate: str) -> bool:
        if ":" not in self._model_name:
            return candidate in {self._model_name, f"{self._model_name}:latest"}
        return candidate == self._model_name
