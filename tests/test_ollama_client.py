"""Tests for OllamaClient."""

import httpx
import pytest

from seeder.ollama_client import (
    OllamaClient,
    OllamaInvalidResponseError,
    OllamaResponse,
    OllamaUnreachableError,
    SeedingFailedError,
)


@pytest.fixture
def client() -> OllamaClient:
    return OllamaClient(ollama_url="http://localhost:11434", model="llama3.2")


VALID_OLLAMA_JSON = {
    "response": "Generated product JSON here",
    "total_duration": 12345678,
    "eval_duration": 9876543,
    "eval_count": 150,
    "prompt_eval_count": 50,
}


class TestOllamaResponse:
    def test_dataclass_fields(self):
        resp = OllamaResponse(
            response="hello",
            total_duration=100,
            eval_duration=80,
            eval_count=10,
            prompt_eval_count=5,
        )
        assert resp.response == "hello"
        assert resp.total_duration == 100
        assert resp.eval_duration == 80
        assert resp.eval_count == 10
        assert resp.prompt_eval_count == 5


class TestExceptions:
    def test_ollama_unreachable_error_is_exception(self):
        assert issubclass(OllamaUnreachableError, Exception)

    def test_ollama_invalid_response_error_is_exception(self):
        assert issubclass(OllamaInvalidResponseError, Exception)

    def test_seeding_failed_error_is_exception(self):
        assert issubclass(SeedingFailedError, Exception)


class TestOllamaClientGenerate:
    @pytest.mark.anyio
    async def test_successful_generate(self, client: OllamaClient, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:11434/api/generate",
            method="POST",
            json=VALID_OLLAMA_JSON,
            status_code=200,
        )

        result = await client.generate("Generate a product")

        assert isinstance(result, OllamaResponse)
        assert result.response == "Generated product JSON here"
        assert result.total_duration == 12345678
        assert result.eval_duration == 9876543
        assert result.eval_count == 150
        assert result.prompt_eval_count == 50

    @pytest.mark.anyio
    async def test_sends_correct_request_body(self, client: OllamaClient, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:11434/api/generate",
            method="POST",
            json=VALID_OLLAMA_JSON,
            status_code=200,
        )

        await client.generate("my prompt")

        request = httpx_mock.get_request()
        import json
        body = json.loads(request.content)
        assert body["model"] == "llama3.2"
        assert body["prompt"] == "my prompt"
        assert body["stream"] is False

    @pytest.mark.anyio
    async def test_connection_error_raises_unreachable(self, client: OllamaClient, httpx_mock):
        httpx_mock.add_exception(httpx.ConnectError("Connection refused"))

        with pytest.raises(OllamaUnreachableError, match="Failed to connect to Ollama"):
            await client.generate("test")

    @pytest.mark.anyio
    async def test_timeout_raises_unreachable(self, client: OllamaClient, httpx_mock):
        httpx_mock.add_exception(httpx.ReadTimeout("Timed out"))

        with pytest.raises(OllamaUnreachableError, match="Failed to connect to Ollama"):
            await client.generate("test")

    @pytest.mark.anyio
    async def test_non_200_raises_invalid_response(self, client: OllamaClient, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:11434/api/generate",
            method="POST",
            text="Internal Server Error",
            status_code=500,
        )

        with pytest.raises(OllamaInvalidResponseError, match="status 500"):
            await client.generate("test")

    @pytest.mark.anyio
    async def test_invalid_json_raises_invalid_response(self, client: OllamaClient, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:11434/api/generate",
            method="POST",
            text="not json at all",
            status_code=200,
            headers={"content-type": "text/plain"},
        )

        with pytest.raises(OllamaInvalidResponseError, match="Failed to parse"):
            await client.generate("test")

    @pytest.mark.anyio
    async def test_missing_fields_raises_invalid_response(self, client: OllamaClient, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:11434/api/generate",
            method="POST",
            json={"response": "hello"},  # missing metadata fields
            status_code=200,
        )

        with pytest.raises(OllamaInvalidResponseError, match="Failed to parse"):
            await client.generate("test")

    @pytest.mark.anyio
    async def test_custom_url_and_model(self, httpx_mock):
        custom_client = OllamaClient(
            ollama_url="http://myhost:9999", model="mistral"
        )
        httpx_mock.add_response(
            url="http://myhost:9999/api/generate",
            method="POST",
            json=VALID_OLLAMA_JSON,
            status_code=200,
        )

        result = await custom_client.generate("test")
        assert result.response == "Generated product JSON here"

        request = httpx_mock.get_request()
        import json
        body = json.loads(request.content)
        assert body["model"] == "mistral"
