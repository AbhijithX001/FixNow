"""
FixNow AI Assistant — Ollama HTTP Client

A thin, dependency-free wrapper around the Ollama /api/chat endpoint.
All network errors are caught and surfaced as structured dicts so that
callers never need to handle raw exceptions.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

# ─── System prompt (V2 — anchored-schema) ─────────────────────────────────────

SYSTEM_PROMPT = """You are the FixNow AI Assistant intent parser. FixNow is an on-demand home services marketplace.

Supported Service Categories (use exact category_slug):
- "plumbing-services": Taps, washbasins, pipe leaks, flush tanks, drainage, water supply.
- "electrical-services": Lights, fans, sockets, wiring, geysers, circuit breakers.
- "ac-repair-servicing": Split/window AC cooling issues, rattling noise, gas filling, servicing.
- "carpentry": Wooden doors, window frames, latches, locks, hinges, cabinets, furniture repair.
- "house-cleaning": Deep cleaning, floor scrubbing, bathroom tile stains, balcony cleaning.
- "painting": Interior and exterior wall painting, waterproof wall coating.
- "pest-control": Cockroaches, termites, bedbugs, general pest management.

High-Level Intents (you MUST choose one of these exact strings for "intent"):
- "service_request": User wants or describes a supported home service.
- "provider_search": User explicitly asks to find, list, or view providers.
- "unsupported_or_out_of_scope": Request is outside FixNow's scope (e.g. laptop/phone repair, car mechanics, tutoring, medical).
- "needs_clarification": Request is too vague or ambiguous to identify a service.

Rules:
1. CRITICAL: "intent" MUST be a high-level intent (e.g. "service_request", NOT "plumbing-services"). Put the trade slug in "category_slug".
2. If the request is unsupported, set "intent": "unsupported_or_out_of_scope", "category_slug": null, "needs_clarification": false.
3. If the request is vague, set "intent": "needs_clarification", "category_slug": null, "needs_clarification": true.
4. Extract 6-digit postal PIN codes only. If no PIN or invalid format, set "pincode": null.
5. Use the preceding conversation messages when the latest message is a follow-up. Carry forward a clearly established service category or problem, and extract newly supplied location/details.
6. Do NOT invent dates, provider names, availability, PIN codes, or prices.
7. Return ONLY a single valid JSON object matching the required schema.

Example 1:
User: "The kitchen water pipe is leaking under the sink"
Output: {"intent": "service_request", "category_slug": "plumbing-services", "problem_summary": "Kitchen water pipe leaking under sink", "pincode": null, "missing_fields": ["pincode", "preferred_date", "preferred_time", "address"], "needs_clarification": true}

Example 2:
User: "Can you fix the broken screen on my iPhone?"
Output: {"intent": "unsupported_or_out_of_scope", "category_slug": null, "problem_summary": "iPhone screen repair requested", "pincode": null, "missing_fields": [], "needs_clarification": false}

Example 3:
User: "Something is not working in my house"
Output: {"intent": "needs_clarification", "category_slug": null, "problem_summary": "Unspecified household problem", "pincode": null, "missing_fields": ["problem_details"], "needs_clarification": true}"""

# ─── Default configuration ────────────────────────────────────────────────────

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_TIMEOUT_SECONDS = 60
MAX_OLLAMA_RESPONSE_BYTES = 1_048_576


class OllamaResponseTooLarge(Exception):
    """Raised when Ollama returns more data than the bounded response limit."""


# ─── Client ───────────────────────────────────────────────────────────────────


class OllamaClient:
    """
    Thin HTTP wrapper for the Ollama /api/chat endpoint.

    Parameters
    ----------
    base_url:
        Base URL of the local Ollama server (no trailing slash).
    model:
        Name of the model to query (must already be pulled).
    timeout:
        Per-request HTTP timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._chat_url = f"{self.base_url}/api/chat"
        self._tags_url = f"{self.base_url}/api/tags"

    # ── Public API ────────────────────────────────────────────────────────────

    def health_check(self) -> Dict[str, Any]:
        """
        Verify that Ollama is reachable and the configured model is available.

        Returns a dict with keys:
        - ``ok`` (bool)
        - ``model_available`` (bool)
        - ``available_models`` (list[str])
        - ``error`` (str | None)
        """
        try:
            with urllib.request.urlopen(self._tags_url, timeout=10) as resp:
                data = json.loads(self._read_response(resp).decode("utf-8"))
        except urllib.error.URLError as exc:
            return {
                "ok": False,
                "model_available": False,
                "available_models": [],
                "error": f"Cannot reach Ollama at {self.base_url}: {exc}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "model_available": False,
                "available_models": [],
                "error": str(exc),
            }

        available = [m.get("name", "") for m in data.get("models", [])]
        model_available = self.model in available

        return {
            "ok": True,
            "model_available": model_available,
            "available_models": available,
            "error": None,
        }

    def chat(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        conversation_history: Optional[list[Dict[str, str]]] = None,
        temperature: float = 0.0,
        seed: int = 42,
    ) -> Dict[str, Any]:
        """
        Send a single user message to the model and return a structured result.

        Returns a dict with keys:
        - ``content``   (str)   — raw text content of the model reply
        - ``parsed``    (dict | None) — JSON-decoded content, or None
        - ``elapsed``   (float) — wall-clock seconds
        - ``tok_s``     (float) — tokens/second (0 if unavailable)
        - ``error``     (str | None)
        """
        messages = [
            {
                "role": "system",
                "content": system_prompt if system_prompt is not None else SYSTEM_PROMPT,
            }
        ]
        for item in (conversation_history or []):
            if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str):
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": user_message})

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "format": "json",
            "stream": False,
            "options": {
                "temperature": temperature,
                "seed": seed,
            },
        }

        req = urllib.request.Request(
            self._chat_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = self._read_response(resp).decode("utf-8")
        except OllamaResponseTooLarge as exc:
            return {
                "content": "",
                "parsed": None,
                "elapsed": round(time.perf_counter() - t0, 3),
                "tok_s": 0.0,
                "error": str(exc),
            }
        except urllib.error.URLError as exc:
            return {
                "content": "",
                "parsed": None,
                "elapsed": round(time.perf_counter() - t0, 3),
                "tok_s": 0.0,
                "error": f"Network error talking to Ollama: {exc}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "content": "",
                "parsed": None,
                "elapsed": round(time.perf_counter() - t0, 3),
                "tok_s": 0.0,
                "error": str(exc),
            }

        elapsed = round(time.perf_counter() - t0, 3)

        try:
            response_data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return {
                "content": raw,
                "parsed": None,
                "elapsed": elapsed,
                "tok_s": 0.0,
                "error": f"Ollama returned non-JSON envelope: {exc}",
            }

        if not isinstance(response_data, dict):
            return {
                "content": raw,
                "parsed": None,
                "elapsed": elapsed,
                "tok_s": 0.0,
                "error": "Ollama returned an invalid response envelope.",
            }

        message_data = response_data.get("message", {})
        content = message_data.get("content", "") if isinstance(message_data, dict) else ""
        if not isinstance(content, str):
            return {
                "content": "",
                "parsed": None,
                "elapsed": elapsed,
                "tok_s": 0.0,
                "error": "Ollama returned an invalid message payload.",
            }

        try:
            eval_count = int(response_data.get("eval_count", 0))
            eval_duration = float(response_data.get("eval_duration", 1)) / 1e9
        except (TypeError, ValueError):
            eval_count = 0
            eval_duration = 0.0
        tok_s = round(eval_count / eval_duration, 2) if eval_duration > 0 else 0.0

        # Try to parse the model's JSON reply
        parsed: Optional[Dict[str, Any]] = None
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            pass

        return {
            "content": content,
            "parsed": parsed,
            "elapsed": elapsed,
            "tok_s": tok_s,
            "error": None,
        }

    @staticmethod
    def _read_response(resp) -> bytes:
        """Read at most the configured response bound from an HTTP response."""
        content_length = resp.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_OLLAMA_RESPONSE_BYTES:
                    raise OllamaResponseTooLarge(
                        "Ollama response exceeded the configured size limit."
                    )
            except ValueError:
                pass

        raw = resp.read(MAX_OLLAMA_RESPONSE_BYTES + 1)
        if len(raw) > MAX_OLLAMA_RESPONSE_BYTES:
            raise OllamaResponseTooLarge(
                "Ollama response exceeded the configured size limit."
            )
        return raw


# ─── Module-level singleton ───────────────────────────────────────────────────

_default_client: Optional[OllamaClient] = None


def get_client(
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    model: str = DEFAULT_MODEL,
) -> OllamaClient:
    """
    Return (and lazily create) the module-level default ``OllamaClient``.

    Settings override: if ``django.conf.settings`` is configured and exposes
    ``OLLAMA_BASE_URL`` / ``OLLAMA_MODEL``, those values take precedence.
    """
    global _default_client  # noqa: PLW0603

    try:
        from django.conf import settings  # type: ignore[import]

        base_url = getattr(settings, "OLLAMA_BASE_URL", base_url)
        model = getattr(settings, "OLLAMA_MODEL", model)
    except Exception:  # noqa: BLE001
        pass

    if _default_client is None:
        _default_client = OllamaClient(base_url=base_url, model=model)

    return _default_client
