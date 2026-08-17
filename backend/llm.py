"""Azure OpenAI client wrapper.

Provides a single `complete_json()` helper that all agents can use. If the
env var OFFLINE_MODE=1 or credentials are missing, it falls back to a
deterministic stub so the pipeline still runs.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import AzureOpenAI

log = logging.getLogger(__name__)

_client: AzureOpenAI | None = None


def _client_or_none() -> AzureOpenAI | None:
    global _client
    if _client is not None:
        return _client
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview").strip()
    if not endpoint or not api_key:
        log.warning("Azure OpenAI credentials not set — falling back to offline stubs.")
        return None
    _client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
    return _client


def _deployment() -> str:
    return (
        os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")
        or os.getenv("AZURE_OPENAI_DEPLOYMENT")
        or "gpt-4o"
    ).strip()


def is_online() -> bool:
    if os.getenv("OFFLINE_MODE", "0") == "1":
        return False
    return _client_or_none() is not None


def complete(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 1800,
    json_mode: bool = False,
) -> str:
    """Return the model's text response. Falls back to '' when offline."""
    client = _client_or_none()
    if client is None or os.getenv("OFFLINE_MODE", "0") == "1":
        return ""
    kwargs: dict[str, Any] = {
        "model": _deployment(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def complete_json(system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 2000) -> dict:
    """Return parsed JSON, with retry-then-fallback semantics."""
    text = complete(system, user, temperature=temperature, max_tokens=max_tokens, json_mode=True)
    if not text or (text.startswith("{") and not text.rstrip().endswith("}")):
        log.warning("LLM response looks truncated (%d chars). Falling back.", len(text))
        return {}
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Attempt lenient extraction: find first { and last }
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        log.warning("LLM response was not valid JSON. Returning empty dict.")
        return {}
