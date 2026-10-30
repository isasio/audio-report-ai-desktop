from __future__ import annotations

import logging
import shutil
import subprocess

from ..errors import LocalLLMError
from .local_llm_client import validate_report_model_name
from .report_models import ReportModelPreset, preset_for

_SETUP_TIMEOUT_SECONDS = 60 * 60


class ReportModelSetupService:
    """Explicit setup-time helper for downloading an Ollama model.

    The runtime application never calls this service; only the explicit
    ``--setup-report-model`` command does. It shells out to the Ollama CLI with
    an argument list and never contacts a remote endpoint itself.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        runner: object | None = None,
        which: object | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._runner = runner or subprocess.run
        self._which = which or shutil.which

    def setup(self, model_name: str) -> ReportModelPreset | None:
        validate_report_model_name(model_name)
        executable = self._which("ollama")
        if not isinstance(executable, str) or not executable:
            raise LocalLLMError(
                "ollama_not_installed",
                (
                    "Ollama is not installed. Install it from https://ollama.com/download, "
                    "then rerun the setup command."
                ),
            )
        try:
            result = self._runner(
                [executable, "pull", model_name],
                check=False,
                timeout=_SETUP_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise LocalLLMError(
                "ollama_pull_timeout",
                "The Ollama model download timed out.",
            ) from exc
        except OSError as exc:
            raise LocalLLMError(
                "ollama_pull_failed",
                "Could not start the Ollama model download.",
            ) from exc
        returncode = getattr(result, "returncode", 1)
        if returncode != 0:
            raise LocalLLMError(
                "ollama_pull_failed",
                (
                    f"Ollama could not download '{model_name}'. Check the model tag and "
                    "your Ollama version; newer models may require an Ollama upgrade."
                ),
            )
        self._logger.info("report_model_downloaded model=%s", model_name)
        return preset_for(model_name)
