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

Cache dependency
----------------
The customer chat path does not depend on Django's database-backed cache.
"""

import json
import logging
import re
from typing import Any, Dict

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from fixnow_app.models import ServiceCategory

from .model_provider import get_configured_client as get_client
from .provider_discovery import (
    build_pending_action,
    build_discovery_message,
    find_providers,
    get_eligible_provider,
    resolve_location_from_text,
    select_provider_from_message,
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
AFFIRMATIVE_MESSAGE_RE = re.compile(
    r"^(?:yes|yeah|yep|sure|okay|ok|go ahead|please do|show them)[.! ]*$",
    re.IGNORECASE,
)
BOOKING_REQUEST_RE = re.compile(
    r"\b(?:book|booking|schedule|hire|reserve)\b|\bcan\s+you\s+(?:book|schedule|hire)\b",
    re.IGNORECASE,
)
CANCEL_FLOW_RE = re.compile(
    r"\b(?:cancel|abandon|forget|restart|start\s+over|never\s*mind)\b|\bdo\s+not\s+book\b|\bdon't\s+book\b",
    re.IGNORECASE,
)

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
                elif key == "category_slug" and isinstance(value, str) and ServiceCategory.objects.filter(
                    slug=value, is_active=True
                ).exists():
                    safe_context[key] = value
                elif key == "pincode" and isinstance(value, str) and len(value) == 6 and value.isdigit():
                    safe_context[key] = value
                elif key == "problem_summary" and isinstance(value, str):
                    safe_context[key] = value[:MAX_HISTORY_MESSAGE_LEN]
                elif key == "pending_action" and value is None:
                    safe_context[key] = None
            pending = context.get("pending_action")
            if "pending_action" in context and pending is None:
                safe_context["pending_action"] = None
            if isinstance(pending, dict) and pending.get("action") == "provider_search":
                category = pending.get("category_slug")
                pin = pending.get("pincode")
                area = pending.get("area_name", "")
                if (
                    (
                        category in ALIAS_TO_CANONICAL
                        or (
                            isinstance(category, str)
                            and ServiceCategory.objects.filter(
                                slug=category, is_active=True
                            ).exists()
                        )
                    )
                    and isinstance(pin, str) and len(pin) == 6 and pin.isdigit()
                    and isinstance(area, str) and len(area) <= MAX_HISTORY_MESSAGE_LEN
                ):
                    safe_context["pending_action"] = {
                        "action": "provider_search",
                        "category_slug": (
                            category
                            if ServiceCategory.objects.filter(
                                slug=category, is_active=True
                            ).exists()
                            else ALIAS_TO_CANONICAL[category]
                        ),
                        "pincode": pin,
                        "area_name": area,
                    }
            entry["context"] = safe_context
        history.append(entry)

    return message, history, None


def _last_pending_action(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return only the latest assistant turn's pending action.

    Requiring the latest assistant context prevents an old action from being
    revived when a client omits or reorders newer conversation turns.
    """
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        context = item.get("context") or {}
        return context.get("pending_action") if "pending_action" in context else None
    return None


def _is_affirmative(message: str) -> bool:
    return bool(AFFIRMATIVE_MESSAGE_RE.fullmatch(message.strip()))


def _is_booking_request(message: str) -> bool:
    return bool(BOOKING_REQUEST_RE.search(message or ""))


def _is_flow_cancellation(message: str) -> bool:
    return bool(CANCEL_FLOW_RE.search(message or ""))


def _history_text(history: list[dict[str, Any]]) -> str:
    return " ".join(
        item.get("content", "")
        for item in history
        if isinstance(item.get("content"), str)
    )


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
    # The health endpoint is consumed by the customer-facing widget and is
    # therefore restricted to the same explicit customer role as chat.
    if getattr(request.user, "user_type", None) != "customer":
        return _json_error(
            "The AI assistant is available to customers only.", status=403
        )

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

    Error responses: 400 (bad request), 503 (configured AI provider down).
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

    # ── 2. Handle one-shot read-only confirmations ────────────────────────
    pending_action = _last_pending_action(conversation_history)
    provider_discovery = None
    assistant_message = None
    next_pending_action = None
    booking_url = None

    if _is_flow_cancellation(message):
        normalized = {
            "intent": "needs_clarification",
            "category_slug": None,
            "problem_summary": "Conversation flow cancelled",
            "pincode": None,
            "missing_fields": [],
            "needs_clarification": False,
            "normalization_applied": True,
            "normalization_notes": ["Cleared the pending conversational action"],
            "post_schema_valid": True,
        }
        assistant_message = "Okay, I cancelled that pending action. What would you like to do next?"
        llm_result = {"elapsed": 0.0, "tok_s": 0.0}
    elif _is_booking_request(message):
        # Booking is a consequential action.  The assistant only creates a
        # server-validated handoff to FixNow's existing booking form; it never
        # creates a Booking directly from model output or browser state.
        normalized = {
            "intent": "booking_help",
            "category_slug": None,
            "problem_summary": "Booking request",
            "pincode": None,
            "missing_fields": ["provider", "booking_details"],
            "needs_clarification": True,
            "normalization_applied": True,
            "normalization_notes": ["Handled explicit booking request deterministically"],
            "post_schema_valid": True,
        }
        if pending_action:
            provider_discovery = find_providers(
                pending_action["pincode"], pending_action["category_slug"]
            )
            if provider_discovery.get("status") == "ok":
                selection = select_provider_from_message(message, provider_discovery)
                selected = selection.get("provider")
                if selected:
                    category = provider_discovery.get("category") or {}
                    provider = get_eligible_provider(
                        selected.get("provider_id"),
                        provider_discovery.get("pincode"),
                        category.get("slug"),
                    )
                    if provider:
                        normalized.update({
                            "category_slug": provider["category"]["slug"],
                            "pincode": provider_discovery.get("pincode"),
                            "missing_fields": ["preferred_date", "preferred_time", "address", "description"],
                            "needs_clarification": True,
                        })
                        booking_url = reverse(
                            "book_provider", args=[provider["provider_id"]]
                        )
                        assistant_message = (
                            f"I can help you book {provider['display_name']}. "
                            "Please complete the booking details in the FixNow form."
                        )
                    else:
                        assistant_message = (
                            "That provider is no longer eligible for this service area. "
                            "Please run a fresh provider search."
                        )
                elif selection.get("status") == "provider_ambiguous":
                    names = ", ".join(
                        item["display_name"] for item in selection.get("matches", [])
                    )
                    assistant_message = f"Which provider would you like to book: {names}?"
                    normalized["missing_fields"] = ["provider"]
                else:
                    assistant_message = (
                        "Which provider from the results would you like to book? "
                        "Please include the provider's name."
                    )
                    normalized["missing_fields"] = ["provider"]
            else:
                assistant_message = (
                    "I could not confirm an eligible provider from the previous results. "
                    "Please search again before starting a booking."
                )
        else:
            assistant_message = (
                "I can help start a booking, but I first need a provider selected "
                "from a current FixNow search."
            )
        llm_result = {"elapsed": 0.0, "tok_s": 0.0}
    elif _is_affirmative(message):
        if pending_action:
            normalized = {
                "intent": "provider_search",
                "category_slug": pending_action["category_slug"],
                "problem_summary": "Provider search confirmed",
                "pincode": pending_action["pincode"],
                "missing_fields": [],
                "needs_clarification": False,
                "normalization_applied": True,
                "normalization_notes": ["Executed one pending provider search"],
                "post_schema_valid": True,
            }
            provider_discovery = find_providers(
                pending_action["pincode"], pending_action["category_slug"]
            )
            assistant_message = build_discovery_message(provider_discovery)
        else:
            normalized = {
                "intent": "needs_clarification",
                "category_slug": None,
                "problem_summary": "No pending provider search",
                "pincode": None,
                "missing_fields": ["request_details"],
                "needs_clarification": True,
                "normalization_applied": True,
                "normalization_notes": ["No pending action to confirm"],
                "post_schema_valid": True,
            }
            assistant_message = "What would you like me to confirm?"
        llm_result = {"elapsed": 0.0, "tok_s": 0.0}
    else:
        # ── 3. LLM inference ──────────────────────────────────────────────
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

        # ── 4. Validate & normalise ───────────────────────────────────────
        normalized = normalize_output(
            llm_result["parsed"], message, conversation_history
        )

        # Provider facts always come from the database.  The model may suggest
        # an intent/category/PIN, but it cannot create or modify lookup results.
        combined_text = f"{_history_text(conversation_history)} {message}".strip()
        location = resolve_location_from_text(combined_text)
        category_slug = normalized.get("category_slug")
        if (
            normalized.get("intent") in {"provider_search", "service_request"}
            and (category_slug or normalized.get("intent") == "provider_search")
        ):
            if normalized.get("pincode"):
                provider_discovery = find_providers(normalized["pincode"], category_slug)
            elif location["status"] == "resolved":
                normalized["pincode"] = location["pincode"]
                provider_discovery = find_providers(location["pincode"], category_slug)
            elif location["status"] in {"unknown_location", "location_ambiguous"}:
                provider_discovery = {
                    "status": location["status"],
                    "location": location.get("location", ""),
                    "matches": location.get("matches", []),
                    "providers": [],
                }
            elif normalized.get("intent") == "provider_search":
                provider_discovery = list_available_categories()

            if provider_discovery is not None:
                assistant_message = build_discovery_message(provider_discovery)
                next_pending_action = build_pending_action(provider_discovery)

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
            "pending_action": next_pending_action,
            "booking_url": booking_url,
            # ── Performance metadata ───────────────────────────────────
            "elapsed_s": llm_result.get("elapsed", 0),
            "tok_s": llm_result.get("tok_s", 0),
        }
    )
