"""Small provider-neutral interface for bounded model tool calling."""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .ollama_client import OllamaClient
from .tool_registry import MAX_TOOL_RESULTS, TOOL_DEFINITIONS, execute_tool

MAX_TOOL_ROUNDS = 3
MAX_TOOL_RESPONSE_BYTES = 32_768


class ModelProviderError(Exception):
    pass


def _parse_structured_content(content: Any) -> dict | None:
    """Parse model JSON while tolerating common markdown wrappers."""
    if not isinstance(content, str):
        return None
    text = content.strip()
    candidates = [text]
    if text.startswith("```") and text.endswith("```"):
        candidates.append(text.split("\n", 1)[1].rsplit("```", 1)[0].strip())
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _messages(history, message):
    result = [{"role": item["role"], "content": item["content"]} for item in (history or []) if item.get("role") in {"user", "assistant"}]
    result.append({"role": "user", "content": message})
    return result[-12:]


class ToolCallingClient:
    def __init__(self, provider: str, **kwargs):
        self.provider = provider
        self.client = OllamaClient(**kwargs) if provider == "ollama" else None
        self.base_url = kwargs.get("base_url", "https://generativelanguage.googleapis.com/v1beta")
        self.model = kwargs.get("model", "gemini-2.5-flash-lite")
        self.api_key = kwargs.get("api_key")

    def chat(self, *args, **kwargs):
        if self.provider == "ollama":
            result = self.chat_with_tools(*args, **kwargs)
            if result.get("error"):
                return {"content": "", "parsed": None, "elapsed": 0.0, "tok_s": 0.0, "error": result["error"]}
            result["parsed"] = _parse_structured_content(result.get("content", ""))
            return {**result, "elapsed": 0.0, "tok_s": 0.0}
        result = self.chat_with_tools(*args, **kwargs)
        if result.get("error"):
            return {"content": "", "parsed": None, "elapsed": 0.0, "tok_s": 0.0, "error": result["error"]}
        result["parsed"] = _parse_structured_content(result.get("content", ""))
        return {**result, "elapsed": 0.0, "tok_s": 0.0}

    def health_check(self):
        if self.provider == "ollama":
            return self.client.health_check()
        return {"ok": bool(self.api_key), "model_available": bool(self.api_key), "available_models": [], "error": None if self.api_key else "Gemini API credentials are not configured."}

    def chat_with_tools(self, user_message, conversation_history=None):
        try:
            if self.provider == "ollama":
                return self._ollama(user_message, conversation_history)
            return self._gemini(user_message, conversation_history)
        except Exception as exc:  # noqa: BLE001
            # Keep transport/provider details server-side; callers receive a
            # stable failure shape and can return a safe 503 response.
            return {"content": "", "parsed": None, "tool_rounds": 0, "error": "Configured AI provider request failed."}

    def _ollama(self, message, history):
        if self.client is None:
            raise ModelProviderError("Ollama is not configured.")
        # Ollama models differ in tool support; sending tools is explicit and
        # failures are returned rather than silently switching providers.
        payload = self.client._request_payload(message, history, tools=TOOL_DEFINITIONS)
        messages = payload["messages"]
        for _ in range(MAX_TOOL_ROUNDS):
            result = self.client._post_json(payload)
            msg = result.get("message") if isinstance(result, dict) else None
            calls = msg.get("tool_calls", []) if isinstance(msg, dict) else []
            if not calls:
                return {"content": msg.get("content", "") if isinstance(msg, dict) else "", "parsed": None, "tool_rounds": 0, "error": None}
            messages.append(msg)
            for call in calls[:MAX_TOOL_RESULTS]:
                function = call.get("function", {})
                tool_result = execute_tool(function.get("name", ""), function.get("arguments", {}))
                messages.append({"role": "tool", "content": json.dumps(tool_result)})
            payload["messages"] = messages
        return {"content": "", "parsed": None, "tool_rounds": MAX_TOOL_ROUNDS, "error": "Tool-call limit exceeded."}

    def _gemini(self, message, history):
        if not self.api_key:
            return {"content": "", "parsed": None, "tool_rounds": 0, "error": "Gemini API credentials are not configured."}
        contents = [{"role": "user" if item["role"] == "user" else "model", "parts": [{"text": item["content"]}]} for item in _messages(history, message)]
        for round_number in range(MAX_TOOL_ROUNDS + 1):
            body = {"contents": contents, "tools": [{"function_declarations": TOOL_DEFINITIONS}]}
            response = self._gemini_request(body)
            candidate = response.get("candidates", [{}])[0]
            parts = candidate.get("content", {}).get("parts", [])
            calls = [part.get("functionCall") for part in parts if isinstance(part, dict) and part.get("functionCall")]
            if not calls:
                text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
                return {"content": text, "parsed": None, "tool_rounds": round_number, "error": None}
            contents.append({"role": "model", "parts": parts})
            response_parts = []
            for call in calls[:MAX_TOOL_RESULTS]:
                response_parts.append({"functionResponse": {"name": call.get("name", ""), "response": execute_tool(call.get("name", ""), call.get("args", {}))}})
            contents.append({"role": "user", "parts": response_parts})
        return {"content": "", "parsed": None, "tool_rounds": MAX_TOOL_ROUNDS, "error": "Tool-call limit exceeded."}

    def _gemini_request(self, body):
        url = f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent"
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read(MAX_TOOL_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelProviderError("Gemini request failed.") from exc
        if len(raw) > MAX_TOOL_RESPONSE_BYTES:
            raise ModelProviderError("Gemini response exceeded the configured limit.")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelProviderError("Gemini returned malformed JSON.") from exc
        if not isinstance(data, dict) or "candidates" not in data:
            raise ModelProviderError("Gemini returned an invalid response.")
        return data


def get_configured_client():
    """Build exactly the configured provider; failures never trigger fallback."""
    from django.conf import settings

    provider = getattr(settings, "AI_PROVIDER", "ollama").strip().lower()
    if provider == "gemini":
        return ToolCallingClient(
            "gemini",
            base_url=getattr(settings, "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
            model=getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash-lite"),
            api_key=getattr(settings, "GEMINI_API_KEY", None),
        )
    if provider != "ollama":
        raise ModelProviderError("AI_PROVIDER must be ollama or gemini.")
    return ToolCallingClient(
        "ollama",
        base_url=getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434"),
        model=getattr(settings, "OLLAMA_MODEL", "qwen2.5:3b"),
    )
