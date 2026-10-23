"""
FixNow AI Assistant — Deterministic Output Validator & Normalizer

Validates raw model JSON, normalises unambiguous slot errors, and enforces
strict business rules.  Dependency-free (stdlib only).
"""

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone

# ─── Constants ────────────────────────────────────────────────────────────────

ALLOWED_INTENTS = [
    "service_request",
    "provider_search",
    "booking_help",
    "policy_or_faq",
    "account_help",
    "unsupported_or_out_of_scope",
    "needs_clarification",
    "emergency_safety_alert",
]

# Intents the LLM is permitted to produce.  'emergency_safety_alert' is
# deliberately excluded: that intent is reserved for the deterministic
# safety guard and must never be set by the model (prompt injection risk).
LLM_ALLOWED_INTENTS = [
    "service_request",
    "provider_search",
    "booking_help",
    "policy_or_faq",
    "account_help",
    "unsupported_or_out_of_scope",
    "needs_clarification",
]

# Maximum length we accept for the free-text problem_summary field.
MAX_PROBLEM_SUMMARY_LEN = 1000
MAX_CONVERSATION_HISTORY = 6
MAX_HISTORY_MESSAGE_LEN = 1000

CANONICAL_CATEGORIES: Dict[str, List[str]] = {
    "plumbing-services":    ["plumbing-services", "plumbing"],
    "electrical-services":  ["electrical-services", "electrical"],
    "ac-repair-servicing":  ["ac-repair-servicing", "ac-repair"],
    "house-cleaning":       ["house-cleaning", "home-cleaning"],
    "carpentry":            ["carpentry"],
    "painting":             ["painting"],
    "pest-control":         ["pest-control"],
}

# Reverse lookup: alias → canonical
ALIAS_TO_CANONICAL: Dict[str, str] = {}
for _canonical, _aliases in CANONICAL_CATEGORIES.items():
    for _alias in _aliases:
        ALIAS_TO_CANONICAL[_alias] = _canonical

REQUIRED_SCHEMA_KEYS = [
    "intent",
    "category_slug",
    "problem_summary",
    "pincode",
    "missing_fields",
    "needs_clarification",
]

# ─── Schema validation ────────────────────────────────────────────────────────


def is_raw_schema_valid(parsed: Any) -> Tuple[bool, Optional[str]]:
    """
    Checks whether raw model output strictly conforms to the expected schema
    without any normalisation.
    """
    if not isinstance(parsed, dict):
        return False, "Output is not a JSON object"

    for k in REQUIRED_SCHEMA_KEYS:
        if k not in parsed:
            return False, f"Missing required key '{k}'"

    if parsed["intent"] not in ALLOWED_INTENTS:
        return False, f"Invalid intent: {parsed['intent']}"

    slug = parsed["category_slug"]
    if slug is not None and slug not in ALIAS_TO_CANONICAL:
        return False, f"Invalid category_slug: {slug}"

    pin = parsed["pincode"]
    if pin is not None:
        if not (isinstance(pin, str) and len(pin) == 6 and pin.isdigit()):
            return False, f"Invalid pincode format: {pin}"

    if not isinstance(parsed["missing_fields"], list):
        return False, "missing_fields is not a list"

    if not isinstance(parsed["needs_clarification"], bool):
        return False, "needs_clarification is not a boolean"

    return True, None


# ─── Deterministic helpers ────────────────────────────────────────────────────


def extract_pincode_deterministically(message: str) -> Optional[str]:
    """Extract a valid Indian 6-digit postal code (PIN) starting with 1-9."""
    match = re.search(r"\b([1-9][0-9]{5})\b", message)
    return match.group(1) if match else None


def compute_missing_fields(
    intent: str,
    category_slug: Optional[str],
    pincode: Optional[str],
    message: str,
    reference_date: Optional[date] = None,
) -> List[str]:
    """
    Deterministically computes missing booking parameters for service requests.

    A FixNow booking requires: category, pincode, preferred_date,
    preferred_time, and address.
    """
    if intent != "service_request":
        if intent == "needs_clarification":
            return ["problem_details"]
        return []

    missing: List[str] = []
    msg_lower = message.lower()

    if not category_slug:
        missing.append("category")

    if not pincode:
        missing.append("pincode")

    # Only an explicit, valid, non-past ISO date is treated as confirmed.
    # Relative terms are intentionally not guessed. ``reference_date`` keeps
    # tests and callers deterministic while the default follows Django's app
    # timezone.
    requested_date = extract_valid_iso_date(message)
    today = reference_date or timezone.localdate()
    if requested_date is None or requested_date < today:
        missing.append("preferred_date")

    # Detect explicit time expressions
    has_time = bool(
        re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", msg_lower)
        or re.search(r"\b\d{1,2}:\d{2}\b", msg_lower)
    )
    if not has_time:
        missing.append("preferred_time")

    # Detect address keywords
    address_keywords = [
        "flat", "apartment", "road", "street", "valley", "nagar",
        "colony", "house no", "floor",
    ]
    if not any(w in msg_lower for w in address_keywords):
        missing.append("address")

    return missing


def extract_valid_iso_date(message: str) -> Optional[date]:
    """Return the first valid explicit ISO date in a message, if present."""
    for value in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", message):
        try:
            return date.fromisoformat(value)
        except ValueError:
            continue
    return None


def _category_hint_from_text(message: str) -> Optional[str]:
    """Infer a category only from unambiguous, high-signal service terms."""
    terms = {
        "plumbing-services": ("plumb", "tap", "faucet", "pipe", "drain", "flush", "washbasin"),
        "electrical-services": ("electric", "wiring", "socket", "switchboard", "ceiling fan", "light fixture"),
        "ac-repair-servicing": ("air conditioner", "air conditioning", " ac ", "split ac", "window ac"),
        "carpentry": ("carpenter", "wooden door", "cabinet", "furniture", "hinge"),
        "house-cleaning": ("house cleaning", "deep cleaning", "bathroom cleaning", "floor cleaning"),
        "painting": ("painting", "paint my wall", "wall coating"),
        "pest-control": ("cockroach", "termite", "bedbug", "pest control"),
    }
    lowered = f" {message.lower()} "
    matches = [category for category, keywords in terms.items() if any(keyword in lowered for keyword in keywords)]
    return matches[0] if len(matches) == 1 else None


def _history_text(history: Any) -> str:
    if not isinstance(history, list):
        return ""
    parts = []
    for item in history[-MAX_CONVERSATION_HISTORY:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content[:MAX_HISTORY_MESSAGE_LEN])
    return " ".join(parts)


def _last_context(history: Any) -> Dict[str, Any]:
    if not isinstance(history, list):
        return {}
    for item in reversed(history[-MAX_CONVERSATION_HISTORY:]):
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        context = item.get("context")
        if isinstance(context, dict):
            return context
    return {}


# ─── Main normaliser ──────────────────────────────────────────────────────────


def normalize_output(
    raw_parsed: Any,
    message: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Normalises model output deterministically.

    - Resolves category-as-intent conflation.
    - Cleans malformed PIN codes and falls back to deterministic extraction.
    - Calculates required missing fields deterministically.
    - Annotates the result with ``normalization_applied``,
      ``normalization_notes``, and ``post_schema_valid``.
    """
    history = conversation_history or []
    prior_text = _history_text(history)
    combined_message = f"{prior_text} {message}".strip()
    prior_context = _last_context(history)

    if not isinstance(raw_parsed, dict):
        det_pin = extract_pincode_deterministically(combined_message)
        det_category = prior_context.get("category_slug") or _category_hint_from_text(combined_message)
        return {
            "intent": prior_context.get("intent", "needs_clarification"),
            "category_slug": det_category,
            "problem_summary": "Unparseable response; clarification needed",
            "pincode": det_pin,
            "missing_fields": compute_missing_fields(
                prior_context.get("intent", "needs_clarification"),
                det_category,
                det_pin,
                combined_message,
            ),
            "needs_clarification": True,
            "normalization_applied": True,
            "normalization_notes": ["Unparseable JSON; created safe fallback"],
            "post_schema_valid": True,
            "post_schema_err": None,
        }

    out = dict(raw_parsed)
    normalization_applied = False
    normalization_notes: List[str] = []

    raw_intent = out.get("intent")
    raw_cat = out.get("category_slug")

    # A follow-up often contains only a PIN or date.  Carry forward only
    # validated structured hints from the previous assistant response; the
    # client-provided history is never treated as authoritative database data.
    if not raw_cat:
        context_category = prior_context.get("category_slug")
        if context_category in ALIAS_TO_CANONICAL:
            out["category_slug"] = ALIAS_TO_CANONICAL[context_category]
            normalization_applied = True
            normalization_notes.append("Carried forward the previous service category")
        else:
            inferred_category = _category_hint_from_text(combined_message)
            if inferred_category:
                out["category_slug"] = inferred_category
                normalization_applied = True
                normalization_notes.append("Extracted service category from conversation text")

    if out.get("intent") == "needs_clarification" and prior_context.get("intent") in {
        "service_request", "provider_search"
    } and out.get("category_slug"):
        out["intent"] = prior_context["intent"]
        normalization_applied = True
        normalization_notes.append("Continued the previous service intent")

    # 0. Block model from producing 'emergency_safety_alert': that intent is
    #    only valid when set by the deterministic safety guard, never by the LLM.
    if raw_intent not in LLM_ALLOWED_INTENTS and raw_intent not in ALIAS_TO_CANONICAL:
        out["intent"] = "needs_clarification"
        out["needs_clarification"] = True
        normalization_applied = True
        normalization_notes.append(
            f"Blocked disallowed LLM intent '{raw_intent}' → 'needs_clarification'"
        )
        raw_intent = "needs_clarification"

    # 1. Resolve category slug placed in the intent field
    if raw_intent in ALIAS_TO_CANONICAL:
        canonical_from_intent = ALIAS_TO_CANONICAL[raw_intent]
        if raw_cat is None or ALIAS_TO_CANONICAL.get(raw_cat) == canonical_from_intent:
            out["intent"] = "service_request"
            out["category_slug"] = canonical_from_intent
            normalization_applied = True
            normalization_notes.append(
                f"Normalised intent '{raw_intent}' → 'service_request' "
                f"with category '{canonical_from_intent}'"
            )
        else:
            out["intent"] = "needs_clarification"
            out["category_slug"] = None
            out["needs_clarification"] = True
            normalization_applied = True
            normalization_notes.append(
                f"Contradictory intent '{raw_intent}' and category '{raw_cat}'"
            )
    elif raw_cat in ALIAS_TO_CANONICAL:
        canonical_slug = ALIAS_TO_CANONICAL[raw_cat]
        if out["category_slug"] != canonical_slug:
            out["category_slug"] = canonical_slug
            normalization_applied = True
            normalization_notes.append(
                f"Standardised category alias '{raw_cat}' → '{canonical_slug}'"
            )

    # 2. Validate and repair PIN code
    pin = out.get("pincode")
    det_pin = extract_pincode_deterministically(message) or extract_pincode_deterministically(prior_text)
    if not det_pin and isinstance(prior_context.get("pincode"), str):
        det_pin = prior_context["pincode"]
    if pin is not None:
        if isinstance(pin, str) and len(pin) == 6 and pin.isdigit():
            pass  # Already valid
        elif det_pin:
            out["pincode"] = det_pin
            normalization_applied = True
            normalization_notes.append(
                f"Replaced malformed pincode '{pin}' with extracted '{det_pin}'"
            )
        else:
            out["pincode"] = None
            normalization_applied = True
            normalization_notes.append(
                f"Cleared malformed pincode '{pin}' to None"
            )
    elif det_pin:
        out["pincode"] = det_pin
        normalization_applied = True
        normalization_notes.append(
            f"Extracted missing pincode '{det_pin}' from message"
        )

    # 3b. Clamp problem_summary length so an oversized model reply cannot
    #     inflate response payloads or exceed downstream storage limits.
    summary = out.get("problem_summary", "")
    if isinstance(summary, str) and len(summary) > MAX_PROBLEM_SUMMARY_LEN:
        out["problem_summary"] = summary[:MAX_PROBLEM_SUMMARY_LEN]
        normalization_applied = True
        normalization_notes.append(
            f"Truncated problem_summary from {len(summary)} to {MAX_PROBLEM_SUMMARY_LEN} chars"
        )

    # 4. Deterministic missing-field calculation
    calculated_missing = compute_missing_fields(
        out.get("intent", ""),
        out.get("category_slug"),
        out.get("pincode"),
        combined_message,
    )
    out["model_proposed_missing_fields"] = out.get("missing_fields", [])
    out["missing_fields"] = calculated_missing

    if calculated_missing:
        out["needs_clarification"] = True
    elif out.get("intent") in ("service_request", "provider_search"):
        out["needs_clarification"] = False

    # 4. Post-processing schema check
    post_valid, post_err = is_raw_schema_valid(out)
    out["post_schema_valid"] = post_valid
    out["post_schema_err"] = post_err
    out["normalization_applied"] = normalization_applied
    out["normalization_notes"] = normalization_notes

    return out
