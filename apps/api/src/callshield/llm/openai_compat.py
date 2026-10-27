"""OpenAI-compatible chat-completions client for local models. Off by default; never
constructed in tests against a real endpoint (tests inject httpx.MockTransport)."""

from __future__ import annotations

from typing import Any

import httpx

from callshield.llm.client import LLMError, LLMTimeoutError


class OpenAICompatibleClient:
    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._http = http

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _payload(self, system: str, user: str) -> dict[str, Any]:
        return {
            "model": self._model,
            "temperature": 0,
            "max_tokens": 80,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

    def complete(self, system: str, user: str, *, timeout_seconds: float) -> str:
        client = self._http or httpx.Client()
        try:
            resp = client.post(
                self._url,
                headers=self._headers(),
                json=self._payload(system, user),
                timeout=timeout_seconds,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("llm timeout") from exc
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("llm request failed") from exc
        finally:
            if self._http is None:
                client.close()
        if not isinstance(content, str):
            raise LLMError("llm returned non-text content")
        return content
