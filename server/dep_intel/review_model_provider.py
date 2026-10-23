"""Optional review model. Deterministic review does not require it."""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

import httpx

from dep_intel.sources import redact


class ReviewModelProvider(Protocol):
    name: str

    def review(self, request: dict[str, Any]) -> dict[str, Any]:
        ...


class NoneProvider:
    name = "none"

    def review(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"findings": [], "summary": "", "available": False, "provider": self.name}


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, model: str, base_url: str, api_key: str) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key

    def review(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key or not self.base_url:
            return {"findings": [], "summary": "", "available": False, "provider": self.name, "diagnostic": "model_unavailable"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Return only JSON with keys findings and summary. Findings are advisory."},
                {"role": "user", "content": json.dumps(_bounded(request))},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            return {"findings": [], "summary": "", "available": False, "provider": self.name, "diagnostic": "model_unavailable", "detail": redact(str(exc), [self._api_key])}
        if response.status_code != 200:
            return {"findings": [], "summary": "", "available": False, "provider": self.name, "diagnostic": "model_unavailable"}
        try:
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return {"findings": [], "summary": "", "available": False, "provider": self.name, "diagnostic": "model_invalid_response"}
        if not isinstance(parsed, dict) or not isinstance(parsed.get("findings"), list):
            return {"findings": [], "summary": "", "available": False, "provider": self.name, "diagnostic": "model_invalid_response"}
        return {"findings": parsed.get("findings") or [], "summary": parsed.get("summary") or "", "available": True, "provider": self.name, "model": self.model}


def provider_from_env() -> ReviewModelProvider:
    name = os.environ.get("NAMI_REVIEW_MODEL_PROVIDER") or "none"
    if name == "openai_compatible":
        return OpenAICompatibleProvider(
            os.environ.get("NAMI_REVIEW_MODEL") or "",
            os.environ.get("NAMI_REVIEW_BASE_URL") or "",
            os.environ.get("NAMI_REVIEW_API_KEY") or "",
        )
    return NoneProvider()


def model_configured() -> bool:
    return bool(os.environ.get("NAMI_REVIEW_API_KEY")) and (os.environ.get("NAMI_REVIEW_MODEL_PROVIDER") == "openai_compatible")


def _bounded(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "goal": "Suggest advisory review findings grounded in the supplied evidence identities.",
        "changed_files": (request.get("changed_files") or [])[:20],
        "symbols": (request.get("changed_symbols") or [])[:20],
        "context_ids": [item.get("id") for item in (request.get("context") or [])[:20]],
        "policies": [item.get("policy_id") for item in (request.get("policies") or [])[:20]],
    }
