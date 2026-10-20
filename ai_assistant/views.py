"""
FixNow AI Assistant — Django Views

Endpoints
---------
GET  /ai/health/   — Verify Ollama connectivity and model availability.
POST /ai/chat/     — Accept a user message, run the safety guard, query the
                     LLM, validate/normalize the response, and return a
                     structured JSON result.

Authentication
--------------
Both endpoints require an authenticated session (``@login_required``).
The chat endpoint additionally enforces the ``customer`` user type so that
providers and admin users cannot invoke the assistant.

Rate limiting
-------------
A fixed-window counter is stored in Django's configured cache. The default
DatabaseCache backend is shared by the current Django worker processes. The
counter update is protected by a short cache lock because Django's
DatabaseCache ``incr()`` implementation is not atomic.
"""

import json
import logging
import time
from typing import Any, Dict
from uuid import uuid4

from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .ollama_client import get_client
from .provider_discovery import (
    build_discovery_message,
    find_providers,
    list_available_categories,
    list_covered_pincodes,
)
from .safety_guard import check_safety_hazard
from .validator import (
    ALIAS_TO_CANONICAL,
    MAX_CONVERSATION_HISTORY,
    MAX_HISTORY_MESSAGE_LEN,
    normalize_output,
)

logger = logging.getLogger(__name__)

# ─── Django-cache-backed rate limiter ─────────────────────────────────────────

_WINDOW_SECONDS = 60
_LOCK_SECONDS = 5
_LOCK_WAIT_SECONDS = 0.5


class RateLimitBackendUnavailable(Exception):
    """Raised when the configured shared cache cannot enforce a limit."""


def _get_rate_limit() -> int:
    """Read AI_CHAT_MAX_REQUESTS_PER_MINUTE from settings (default 10)."""
    try:
        from django.conf import settings  # noqa: PLC0415

        return int(getattr(settings, "AI_CHAT_MAX_REQUESTS_PER_MINUTE", 10))
    except Exception:  # noqa: BLE001
        return 10


def _is_rate_limited(user_id: int) -> bool:
    """Return True if the user has exceeded the shared-cache minute limit.

    ``cache.add`` is used as an atomic lock acquisition operation. The
    configured default is DatabaseCache so the limiter is shared by Django
    worker processes. A LocMemCache override is only process-local and should
    not be used for production enforcement.
    """
    limit = _get_rate_limit()
    counter_key = f"ai_assistant:rate:{user_id}"
    lock_key = f"{counter_key}:lock"
    lock_token = uuid4().hex
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS

    acquired = False
    try:
        while not cache.add(lock_key, lock_token, timeout=_LOCK_SECONDS):
            if time.monotonic() >= deadline:
                # Fail closed if the shared limiter is contended.
                return True
            time.sleep(0.01)
        acquired = True
        count = cache.get(counter_key, 0) + 1
        cache.set(counter_key, count, timeout=_WINDOW_SECONDS)
        return count > limit
    except Exception as exc:  # noqa: BLE001
        logger.error("AI rate-limit cache unavailable: %s", type(exc).__name__)
        raise RateLimitBackendUnavailable from exc
    finally:
        # A crashed worker leaves the short lock to expire rather than
        # blocking future requests forever.
        if acquired and cache.get(lock_key) == lock_token:
            cache.delete(lock_key)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _json_error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


def _get_message_from_request(request) -> tuple[str, list[dict[str, Any]], str | None]:
    """
    Parse ``message`` from a JSON POST body.

    Returns ``(message, bounded_history, error_string_or_None)``.
    """
    try:
        body: Dict[str, Any] = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "", [], "Request body must be valid JSON."

    message = body.get("message", "")
    if not isinstance(message, str):
        return "", [], "'message' must be a string."
    message = message.strip()
    if not message:
        return "", [], "'message' is required and must not be blank."
    if len(message) > 2000:
        return "", [], "'message' must not exceed 2 000 characters."

    raw_history = body.get("history", [])
    if raw_history is None:
        raw_history = []
    if not isinstance(raw_history, list):
        return "", [], "'history' must be a list."
    if len(raw_history) > MAX_CONVERSATION_HISTORY:
        return "", [], "'history' contains too many messages."

    history = []
    for item in raw_history:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            return "", [], "Each history item must contain a valid role."
        content = item.get("content", "")
        if not isinstance(content, str) or len(content) > MAX_HISTORY_MESSAGE_LEN:
            return "", [], "Each history message must be valid and bounded."
        entry = {"role": item["role"], "content": content}
        context = item.get("context")
        if context is not None:
            if item["role"] != "assistant" or not isinstance(context, dict):
                return "", [], "History context is invalid."
            safe_context = {}
            for key in ("intent", "category_slug", "pincode", "problem_summary"):
                value = context.get(key)
                if key == "intent" and value in {
                    "service_request", "provider_search", "needs_clarification",
                }:
                    safe_context[key] = value
                elif key == "category_slug" and value in ALIAS_TO_CANONICAL:
                    safe_context[key] = ALIAS_TO_CANONICAL[value]
                elif key == "pincode" and isinstance(value, str) and len(value) == 6 and value.isdigit():
                    safe_context[key] = value
                elif key == "problem_summary" and isinstance(value, str):
                    safe_context[key] = value[:MAX_HISTORY_MESSAGE_LEN]
            entry["context"] = safe_context
        history.append(entry)

    return message, history, None


# ─── Views ────────────────────────────────────────────────────────────────────


@login_required
@require_GET
def health(request) -> JsonResponse:
    """
    GET /ai/health/

    Returns Ollama reachability and model availability.  Intended for
    monitoring and the frontend to decide whether to show the chat widget.

    Response (200):
        {
          "ok": true,
          "ollama_reachable": true,
          "model_available": true,
          "model": "qwen2.5:3b",
          "available_models": ["qwen2.5:3b"]
        }
    Response (503) if Ollama is unreachable:
        {
          "ok": false,
          "ollama_reachable": false,
          "error": "..."
        }
    """
    client = get_client()
    result = client.health_check()

    if not result["ok"]:
        return JsonResponse(
            {
                "ok": False,
                "ollama_reachable": False,
                "model": client.model,
                "error": "The AI assistant is temporarily unavailable.",
            },
            status=503,
        )

    return JsonResponse(
        {
            "ok": True,
            "ollama_reachable": True,
            "model_available": result["model_available"],
            "model": client.model,
            # available_models omitted intentionally: it would enumerate every
            # model installed on the Ollama server, which is an unnecessary
            # internal disclosure for a monitoring endpoint.
        }
    )


@login_required
@require_POST
def chat(request) -> JsonResponse:
    """
    POST /ai/chat/

    Body (JSON):
        { "message": "<user text>" }

    Successful response (200):
        {
          "ok": true,
          "intent": "service_request",
          "category_slug": "plumbing-services",
          "problem_summary": "...",
          "pincode": "682001",
          "missing_fields": ["preferred_date", "preferred_time", "address"],
          "needs_clarification": true,
          "is_hazard": false,
          "normalization_applied": false,
          "elapsed_s": 1.23
        }

    Emergency response (200):
        {
          "ok": true,
          "is_hazard": true,
          "intent": "emergency_safety_alert",
          "safety_guidance": "...",
          "emergency_contacts": ["112", "101 (Fire)"]
        }

    Error responses: 400 (bad request), 429 (rate limited), 503 (Ollama down).
    """
    # ── User type guard ────────────────────────────────────────────────────
    # Only explicit 'customer' accounts may use the assistant.
    # user_type=None (blank/null, possible for admin-created accounts) is
    # deliberately denied rather than granted.
    user = request.user
    user_type = getattr(user, "user_type", None)
    if user_type != "customer":
        return _json_error(
            "The AI assistant is available to customers only.", status=403
        )

    # ── Rate limit ─────────────────────────────────────────────────────────
    try:
        rate_limited = _is_rate_limited(user.pk)
    except RateLimitBackendUnavailable:
        return _json_error(
            "The AI assistant is temporarily unavailable. Please try again later.",
            status=503,
        )
    if rate_limited:
        return _json_error(
            "Too many requests. Please wait a moment before trying again.",
            status=429,
        )

    # ── Parse body ─────────────────────────────────────────────────────────
    message, conversation_history, parse_error = _get_message_from_request(request)
    if parse_error:
        return _json_error(parse_error)

    logger.info("AI chat request from user=%s, message_len=%d", user.pk, len(message))

    # ── 1. Safety guard (deterministic, runs before LLM) ──────────────────
    safety_result = check_safety_hazard(message)
    if safety_result["is_hazard"]:
        logger.warning(
            "Safety hazard intercepted for user=%s: type=%s",
            user.pk,
            safety_result.get("hazard_type"),
        )
        return JsonResponse(
            {
                "ok": True,
                "is_hazard": True,
                "intent": safety_result["intent"],
                "hazard_type": safety_result["hazard_type"],
                "problem_summary": safety_result["problem_summary"],
                "safety_guidance": safety_result["safety_guidance"],
                "emergency_contacts": safety_result["emergency_contacts"],
                "booking_allowed": False,
                "needs_clarification": False,
                "elapsed_s": 0.0,
            }
        )

    # ── 2. LLM inference ───────────────────────────────────────────────────
    client = get_client()
    llm_result = client.chat(
        user_message=message,
        conversation_history=conversation_history,
    )

    if llm_result["error"]:
        logger.error("Ollama error for user=%s: %s", user.pk, llm_result["error"])
        # Do NOT forward llm_result["error"] to the client — it may contain
        # internal hostnames, SSL certificate details, or file-system paths.
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "The AI assistant is temporarily unavailable. "
                    "Please try again in a moment."
                ),
            },
            status=503,
        )

    # ── 3. Validate & normalise ────────────────────────────────────────────
    normalized = normalize_output(
        llm_result["parsed"], message, conversation_history
    )

    # Provider facts always come from the database.  The model may suggest
    # an intent/category/PIN, but it cannot create or modify lookup results.
    provider_discovery = None
    assistant_message = None
    if normalized.get("intent") in {"provider_search", "service_request"} and normalized.get("category_slug") and normalized.get("pincode"):
        category_slug = normalized.get("category_slug")
        pincode = normalized.get("pincode")
        if pincode:
            provider_discovery = find_providers(pincode, category_slug)
        elif category_slug:
            provider_discovery = list_covered_pincodes(category_slug)
        else:
            provider_discovery = list_available_categories()
        assistant_message = build_discovery_message(provider_discovery)

    logger.info(
        "AI chat response for user=%s: intent=%s, tok_s=%.1f, elapsed=%.2fs",
        user.pk,
        normalized.get("intent"),
        llm_result.get("tok_s", 0),
        llm_result.get("elapsed", 0),
    )

    return JsonResponse(
        {
            "ok": True,
            "is_hazard": False,
            # ── Structured intent fields ───────────────────────────────
            "intent": normalized.get("intent"),
            "category_slug": normalized.get("category_slug"),
            "problem_summary": normalized.get("problem_summary"),
            "pincode": normalized.get("pincode"),
            "missing_fields": normalized.get("missing_fields", []),
            "needs_clarification": normalized.get("needs_clarification", True),
            # ── Normalisation metadata (useful for debugging) ──────────
            "normalization_applied": normalized.get("normalization_applied", False),
            "normalization_notes": normalized.get("normalization_notes", []),
            "post_schema_valid": normalized.get("post_schema_valid", False),
            "provider_discovery": provider_discovery,
            "assistant_message": assistant_message,
            # ── Performance metadata ───────────────────────────────────
            "elapsed_s": llm_result.get("elapsed", 0),
            "tok_s": llm_result.get("tok_s", 0),
        }
    )
