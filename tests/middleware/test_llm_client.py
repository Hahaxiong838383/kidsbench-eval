from __future__ import annotations

import pytest

from kidsbench.middleware.errors import AuthError, NetworkError, RateLimitError
from kidsbench.middleware.llm_client import FallbackChain, LLMClient, LLMResponse, QwenMaxClient


class _FakeResponse:
    def __init__(self, status_code: int, body: dict, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self) -> dict:
        return self._body


def test_qwen_client_success_and_seed(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "abc")
    payload_store = {}

    class FakeClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, json: dict, headers: dict):
            payload_store["json"] = json
            assert headers["Authorization"] == "Bearer abc"
            return _FakeResponse(
                200,
                {
                    "model": "qwen",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                    "choices": [{"message": {"content": "ok"}}],
                },
            )

    import kidsbench.middleware.llm_client as llm_mod

    monkeypatch.setattr(llm_mod.httpx, "Client", FakeClient)
    client = QwenMaxClient()
    out = client.complete("s", "u", temperature=0.0)
    assert out.text == "ok"
    assert out.cost_token_in == 10
    assert payload_store["json"]["seed"] == 42


def test_qwen_client_missing_key() -> None:
    client = QwenMaxClient(api_key_env="MISSING_KEY")
    with pytest.raises(AuthError):
        client.complete("s", "u")


def test_qwen_client_status_mapping(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "abc")

    class FakeClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, json: dict, headers: dict):
            return _FakeResponse(429, {}, text="rate")

    import kidsbench.middleware.llm_client as llm_mod

    monkeypatch.setattr(llm_mod.httpx, "Client", FakeClient)
    client = QwenMaxClient()
    with pytest.raises(RateLimitError):
        client.complete("s", "u")


class FlakyClient(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 2048) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            raise NetworkError("down")
        return LLMResponse("ok", "m", 1, 2, 1.0, {})


class AuthFailClient(LLMClient):
    def complete(self, system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 2048) -> LLMResponse:
        raise AuthError("bad key")


def test_fallback_chain_retries_network_errors() -> None:
    chain = FallbackChain([FlakyClient()], retry_per_client=2)
    out = chain.complete("s", "u")
    assert out.text == "ok"


def test_fallback_chain_auth_error_not_retried() -> None:
    chain = FallbackChain([AuthFailClient(), FlakyClient()], retry_per_client=3)
    with pytest.raises(AuthError):
        chain.complete("s", "u")
