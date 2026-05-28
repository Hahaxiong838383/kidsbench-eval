"""LLM client abstractions for adapter middleware."""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from kidsbench.contract import AdapterError

from .errors import AuthError, NetworkError, RateLimitError, TimeoutError_


@dataclass(frozen=True)
class LLMResponse:
    """Normalized LLM response object."""

    text: str
    model: str
    cost_token_in: int
    cost_token_out: int
    latency_ms: float
    raw: dict[str, Any]


class LLMClient(ABC):
    """Completion-only interface for middleware callers."""

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """Call text completion with chat-style prompt."""


class QwenMaxClient(LLMClient):
    """DashScope Qwen client implemented using raw HTTP."""

    def __init__(
        self,
        api_key_env: str = "DASHSCOPE_API_KEY",
        model: str = "qwen-max-2025-01-25",
    ) -> None:
        self._api_key_env = api_key_env
        self._model = model
        self._url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """Issue one completion request to DashScope."""
        api_key = os.getenv(self._api_key_env)
        if not api_key:
            raise AuthError(f"missing api key env: {self._api_key_env}")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if temperature == 0:
            payload["seed"] = 42

        headers = {"Authorization": f"Bearer {api_key}"}
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(self._url, json=payload, headers=headers)
        except httpx.TimeoutException as err:
            raise TimeoutError_(str(err)) from err
        except httpx.HTTPError as err:
            raise NetworkError(str(err)) from err

        latency_ms = (time.perf_counter() - t0) * 1000.0
        if response.status_code == 401:
            raise AuthError(response.text)
        if response.status_code == 429:
            raise RateLimitError(response.text)
        if response.status_code >= 500:
            raise NetworkError(response.text)
        if response.status_code >= 400:
            raise AdapterError(f"llm request failed: {response.status_code} {response.text}")

        raw = response.json()
        usage = raw.get("usage", {})
        text = _extract_text(raw)
        return LLMResponse(
            text=text,
            model=raw.get("model", self._model),
            cost_token_in=int(usage.get("prompt_tokens", 0)),
            cost_token_out=int(usage.get("completion_tokens", 0)),
            latency_ms=latency_ms,
            raw=raw,
        )


class FallbackChain(LLMClient):
    """Try multiple LLM clients sequentially with bounded retries."""

    def __init__(self, clients: list[LLMClient], retry_per_client: int = 1) -> None:
        if not clients:
            raise AdapterError("fallback chain requires at least one client")
        self._clients = clients
        self._retry_per_client = max(1, retry_per_client)

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """Execute fallback strategy across configured clients."""
        last_error: Exception | None = None
        retryable = (NetworkError, RateLimitError, TimeoutError_)

        for client in self._clients:
            for _ in range(self._retry_per_client):
                try:
                    return client.complete(
                        system,
                        user,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                except AuthError:
                    raise
                except retryable as err:
                    last_error = err
                    continue
                except Exception as err:
                    last_error = err
                    break

        if last_error is None:
            raise AdapterError("fallback chain exhausted without response")
        raise last_error


def _extract_text(raw: dict[str, Any]) -> str:
    choices = raw.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [chunk.get("text", "") for chunk in content if isinstance(chunk, dict)]
        return "".join(parts)
    return ""
