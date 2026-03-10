"""Ollama HTTP client for LLM-based data generation."""

from dataclasses import dataclass

import httpx


class OllamaUnreachableError(Exception):
    """Raised when Ollama server cannot be reached."""


class OllamaInvalidResponseError(Exception):
    """Raised when Ollama returns an unparseable or non-200 response."""


class SeedingFailedError(Exception):
    """Raised when all retry attempts for product generation are exhausted."""


@dataclass
class OllamaResponse:
    """Parsed response from Ollama /api/generate endpoint."""

    response: str
    total_duration: int
    eval_duration: int
    eval_count: int
    prompt_eval_count: int


class OllamaClient:
    """Async client for Ollama's /api/generate endpoint."""

    def __init__(self, ollama_url: str = "http://localhost:11434", model: str = "llama3.2") -> None:
        self._url = f"{ollama_url}/api/generate"
        self._model = model

    async def generate(self, prompt: str) -> OllamaResponse:
        """Send prompt to Ollama, return parsed response with metadata.

        Raises:
            OllamaUnreachableError: On connection failure or timeout.
            OllamaInvalidResponseError: On non-200 status or unparseable response.
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._url,
                    json={"model": self._model, "prompt": prompt, "stream": False},
                    timeout=300.0,
                )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise OllamaUnreachableError(f"Failed to connect to Ollama: {exc}") from exc

        if resp.status_code != 200:
            raise OllamaInvalidResponseError(
                f"Ollama returned status {resp.status_code}: {resp.text}"
            )

        try:
            data = resp.json()
            return OllamaResponse(
                response=data["response"],
                total_duration=data["total_duration"],
                eval_duration=data["eval_duration"],
                eval_count=data["eval_count"],
                prompt_eval_count=data["prompt_eval_count"],
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise OllamaInvalidResponseError(
                f"Failed to parse Ollama response: {exc}"
            ) from exc
