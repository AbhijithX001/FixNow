"""Read-only provider discovery tools for the FixNow assistant.

The database is authoritative for provider and service-area facts.  This
module deliberately returns small, public-facing dictionaries rather than
model instances or private account fields.
"""

import re
from typing import Any, Dict, Optional

from django.db.models import Count, Q

from fixnow_app.models import ProviderProfile, ServiceCategory, ServicePincode


PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")
LOCATION_PHRASE_RE = re.compile(
    r"\b(?:in|at|over|near|around)\s+([A-Za-z][A-Za-z -]{1,48}?)(?=[,.!?]|$)",
    re.IGNORECASE,
)


def _eligible_profiles():
    """Return the fixed eligibility queryset used by every discovery query."""
    return ProviderProfile.objects.filter(
        status="approved",
        is_available=True,
        category__isnull=False,
        category__is_active=True,
    )


def _category_summary(category: ServiceCategory) -> Dict[str, Any]:
    return {
        "name": category.name,
        "slug": category.slug,
    }


def _provider_summary(profile: ProviderProfile) -> Dict[str, Any]:
    # Full name is already the public display value used by the marketplace.
    # Never fall back to email, phone, address or uploaded identity documents.
    display_name = profile.user.get_full_name().strip() or "FixNow provider"
    return {
        "provider_id": profile.pk,
        "display_name": display_name,
        "category": _category_summary(profile.category),
    }


def select_provider_from_message(
    message: str, discovery: Dict[str, Any]
) -> Dict[str, Any]:
    """Select one provider only from the current, database-backed results.

    The message may name a returned provider or use an ordinal such as
    ``the first one``.  No arbitrary provider identifier supplied by the
    browser is accepted here.
    """
    providers = discovery.get("providers", [])
    if discovery.get("status") != "ok" or not providers:
        return {"status": "provider_unavailable", "provider": None}

    lowered = (message or "").casefold()
    matches = []
    for provider in providers:
        name = str(provider.get("display_name", ""))
        if name and name.casefold() in lowered:
            matches.append(provider)

    if not matches:
        name_tokens = set(re.findall(r"[a-z][a-z'-]{2,}", lowered))
        for provider in providers:
            provider_tokens = set(
                re.findall(r"[a-z][a-z'-]{2,}", str(provider.get("display_name", "")).casefold())
            )
            if name_tokens.intersection(provider_tokens):
                matches.append(provider)

    if not matches:
        ordinal_match = re.search(
            r"\b(?:the\s+)?(first|1st|one|second|2nd|two|third|3rd|three)\b",
            lowered,
        )
        if ordinal_match:
            ordinal = {
                "first": 0, "1st": 0, "one": 0,
                "second": 1, "2nd": 1, "two": 1,
                "third": 2, "3rd": 2, "three": 2,
            }[ordinal_match.group(1)]
            if ordinal < len(providers):
                matches = [providers[ordinal]]

    if not matches and len(providers) == 1 and re.search(
        r"\b(?:him|her|them|this provider|that provider|the provider)\b",
        lowered,
    ):
        matches = [providers[0]]

    if not matches and len(providers) == 1 and re.search(
        r"\b(?:book|booking|schedule|hire|reserve)\b", lowered
    ):
        matches = [providers[0]]

    if len(matches) == 1:
        return {"status": "selected", "provider": matches[0]}
    if len(matches) > 1:
        return {"status": "provider_ambiguous", "provider": None, "matches": matches}
    return {"status": "provider_required", "provider": None}


def get_eligible_provider(
    provider_id: Any, pincode: Optional[str], category: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Revalidate a selected provider against current database facts."""
    try:
        provider_pk = int(provider_id)
    except (TypeError, ValueError):
        return None

    location = resolve_location(pincode)
    if location.get("status") != "resolved":
        return None
    resolution = _resolve_category(category)
    if resolution.get("status") != "resolved":
        return None

    profile = (
        _eligible_profiles()
        .filter(
            pk=provider_pk,
            category=resolution["category"],
            service_pincodes__pincode=location["pincode"],
        )
        .select_related("user", "category")
        .first()
    )
    return _provider_summary(profile) if profile else None


def resolve_location(value: Optional[str]) -> Dict[str, Any]:
    """Resolve a six-digit PIN or an exact configured area name."""
    term = str(value or "").strip()
    if not term:
        return {"status": "location_required", "location": "", "matches": []}

    if PINCODE_RE.fullmatch(term):
        try:
            pin = ServicePincode.objects.get(pincode=term)
        except ServicePincode.DoesNotExist:
            return {"status": "unknown_pincode", "location": term, "matches": []}
        return {
            "status": "resolved",
            "location": term,
            "pincode": pin.pincode,
            "area_name": pin.area_name,
            "district": pin.district,
        }

    matches = list(
        ServicePincode.objects.filter(area_name__iexact=term).order_by("pincode")
    )
    if len(matches) == 1:
        pin = matches[0]
        return {
            "status": "resolved",
            "location": term,
            "pincode": pin.pincode,
            "area_name": pin.area_name,
            "district": pin.district,
        }
    if len(matches) > 1:
        return {
            "status": "location_ambiguous",
            "location": term,
            "matches": [
                {"pincode": pin.pincode, "area_name": pin.area_name, "district": pin.district}
                for pin in matches
            ],
        }
    return {"status": "unknown_location", "location": term, "matches": []}


def extract_location_from_text(text: str) -> Optional[str]:
    """Extract a location phrase only after an explicit location preposition."""
    for match in LOCATION_PHRASE_RE.finditer(text or ""):
        candidate = re.sub(r"\s+", " ", match.group(1)).strip(" .")
        if candidate:
            return candidate
    return None


def resolve_location_from_text(text: str) -> Dict[str, Any]:
    """Resolve a location mentioned in text using configured DB records."""
    candidate = extract_location_from_text(text)
    if not candidate:
        return {"status": "location_required", "location": "", "matches": []}
    return resolve_location(candidate)


def _resolve_category(value: Optional[str]) -> Dict[str, Any]:
    """Resolve a category without merging distinct database categories."""
    if not value:
        return {"status": "not_requested", "category": None, "matches": []}

    term = str(value).strip()
    if not term:
        return {"status": "not_requested", "category": None, "matches": []}

    exact = list(
        ServiceCategory.objects.filter(
            Q(slug__iexact=term) | Q(name__iexact=term)
        ).order_by("name")
    )
    if len(exact) == 1:
        category = exact[0]
        if not category.is_active:
            return {
                "status": "inactive",
                "category": None,
                "matches": [_category_summary(category)],
            }
        return {"status": "resolved", "category": category, "matches": []}
    if len(exact) > 1:
        return {
            "status": "ambiguous",
            "category": None,
            "matches": [_category_summary(c) for c in exact],
        }

    # A fragment such as "cleaning" is intentionally ambiguous when both
    # Home Cleaning and House Cleaning exist.  No fuzzy winner is selected.
    fragments = list(
        ServiceCategory.objects.filter(
            Q(slug__icontains=term) | Q(name__icontains=term)
        ).order_by("name")
    )
    active_fragments = [c for c in fragments if c.is_active]
    if len(active_fragments) == 1:
        return {
            "status": "resolved",
            "category": active_fragments[0],
            "matches": [],
        }
    if len(active_fragments) > 1:
        return {
            "status": "ambiguous",
            "category": None,
            "matches": [_category_summary(c) for c in active_fragments],
        }

    requested_tokens = set(re.findall(r"[a-z]+", term.lower())) - {
        "service", "services", "repair", "and"
    }
    semantic_matches = []
    for category in ServiceCategory.objects.filter(is_active=True).order_by("name"):
        category_tokens = set(re.findall(r"[a-z]+", category.name.lower())) - {
            "service", "services", "repair", "and"
        }
        if requested_tokens and requested_tokens.intersection(category_tokens):
            semantic_matches.append(category)
    if len(semantic_matches) == 1:
        return {
            "status": "resolved",
            "category": semantic_matches[0],
            "matches": [],
        }
    if len(semantic_matches) > 1:
        return {
            "status": "ambiguous",
            "category": None,
            "matches": [_category_summary(c) for c in semantic_matches],
        }
    return {"status": "not_found", "category": None, "matches": []}


def list_available_categories() -> Dict[str, Any]:
    """List active categories with at least one eligible provider."""
    categories = (
        ServiceCategory.objects.filter(
            is_active=True,
            providers__status="approved",
            providers__is_available=True,
            providers__category__isnull=False,
        )
        .annotate(provider_count=Count("providers", distinct=True))
        .order_by("name")
        .distinct()
    )
    return {
        "status": "ok",
        "categories": [
            {**_category_summary(category), "provider_count": category.provider_count}
            for category in categories
        ],
    }


def list_covered_pincodes(category: Optional[str] = None) -> Dict[str, Any]:
    """List configured PIN codes that have eligible coverage."""
    resolution = _resolve_category(category)
    if resolution["status"] in {"ambiguous", "inactive", "not_found"}:
        return {
            "status": f"category_{resolution['status']}",
            "categories": resolution["matches"],
            "pincodes": [],
        }

    profiles = _eligible_profiles()
    if resolution["category"] is not None:
        profiles = profiles.filter(category=resolution["category"])

    pincode_ids = profiles.values_list("service_pincodes", flat=True)
    pincodes = ServicePincode.objects.filter(id__in=pincode_ids).order_by("pincode")
    return {
        "status": "ok",
        "category": (
            _category_summary(resolution["category"])
            if resolution["category"] is not None
            else None
        ),
        "pincodes": [
            {"pincode": pin.pincode, "area_name": pin.area_name, "district": pin.district}
            for pin in pincodes
        ],
    }


def find_providers(
    pincode: Optional[str], category: Optional[str] = None
) -> Dict[str, Any]:
    """Find eligible public provider summaries for a PIN or area name."""
    location = resolve_location(pincode)
    if location["status"] != "resolved":
        result = {"status": location["status"], "providers": [], **location}
        if location["status"] == "unknown_pincode":
            result["pincode"] = location["location"]
        return result

    pin = location["pincode"]
    service_pin = ServicePincode.objects.get(pincode=pin)

    resolution = _resolve_category(category)
    if resolution["status"] == "ambiguous":
        return {
            "status": "category_ambiguous",
            "pincode": pin,
            "area_name": location["area_name"],
            "providers": [],
            "categories": resolution["matches"],
        }
    if resolution["status"] in {"inactive", "not_found"}:
        return {
            "status": f"category_{resolution['status']}",
            "pincode": pin,
            "area_name": location["area_name"],
            "providers": [],
            "categories": resolution["matches"],
        }

    profiles = _eligible_profiles().filter(service_pincodes=service_pin)
    if resolution["category"] is not None:
        profiles = profiles.filter(category=resolution["category"])

    profiles = profiles.select_related("user", "category").order_by("user__first_name", "user__last_name", "id")
    providers = [_provider_summary(profile) for profile in profiles]

    # Existing data contains both "Electrical" and "Electrical Services".
    # If the requested category has no match, use a sibling category only
    # when exactly one semantically matching category has coverage at this
    # location; otherwise report the ambiguity instead of merging categories.
    matched_category = resolution["category"]
    if not providers and matched_category is not None:
        location_profiles = list(
            _eligible_profiles()
            .filter(service_pincodes=service_pin)
            .select_related("category", "user")
        )
        requested_tokens = set(re.findall(r"[a-z]+", matched_category.name.lower())) - {"service", "services", "repair", "and"}
        sibling_profiles = [
            profile for profile in location_profiles
            if requested_tokens.intersection(
                set(re.findall(r"[a-z]+", profile.category.name.lower()))
                - {"service", "services", "repair", "and"}
            )
        ]
        sibling_categories = {profile.category.slug: profile.category for profile in sibling_profiles}
        if len(sibling_categories) == 1 and matched_category.slug not in sibling_categories:
            matched_category = next(iter(sibling_categories.values()))
            providers = [_provider_summary(profile) for profile in sibling_profiles]
        elif len(sibling_categories) > 1:
            return {
                "status": "category_ambiguous",
                "pincode": pin,
                "area_name": location["area_name"],
                "providers": [],
                "categories": [_category_summary(category) for category in sibling_categories.values()],
            }
    if providers:
        status = "ok"
    elif resolution["category"] is None:
        status = "configured_uncovered"
    else:
        status = "no_category_match"

    result: Dict[str, Any] = {
        "status": status,
        "pincode": pin,
        "area_name": location["area_name"],
        "category": _category_summary(matched_category) if matched_category else None,
        "providers": providers,
    }
    if not providers:
        other_profiles = (
            _eligible_profiles()
            .filter(service_pincodes=service_pin)
            .select_related("category")
            .order_by("category__name", "category__slug")
        )
        result["other_categories"] = list(
            {
                profile.category.slug: _category_summary(profile.category)
                for profile in other_profiles
            }.values()
        )
    return result


def build_discovery_message(result: Dict[str, Any]) -> str:
    """Create a deterministic user-facing message from a lookup result."""
    status = result.get("status")
    if status == "ok":
        if "categories" in result and "providers" not in result:
            categories = result["categories"]
            if not categories:
                return "No active FixNow service category currently has eligible provider coverage."
            names = ", ".join(item["name"] for item in categories)
            return f"Eligible FixNow services currently include: {names}."
        if "pincodes" in result and "providers" not in result:
            pincodes = result["pincodes"]
            if not pincodes:
                return "No configured PIN codes currently have eligible coverage for that category."
            values = ", ".join(item["pincode"] for item in pincodes)
            return f"That category currently has eligible coverage in PINs: {values}."
        providers = result["providers"]
        names = ", ".join(
            f"{item['display_name']} ({item['category']['name']})" for item in providers
        )
        return f"I found {len(providers)} eligible provider(s) for PIN {result['pincode']}: {names}."
    if status == "unknown_pincode":
        return f"PIN {result['pincode']} is not configured in FixNow, so I cannot confirm provider coverage there."
    if status == "unknown_location":
        return f"I could not match {result.get('location', 'that area')} to a configured FixNow service area. Please provide a six-digit PIN or another configured area."
    if status == "location_ambiguous":
        options = ", ".join(
            f"{item['area_name']} ({item['pincode']})" for item in result.get("matches", [])
        )
        return f"Which service area did you mean: {options}?"
    if status == "location_required":
        return "Which area or six-digit PIN should I check?"
    if status == "invalid_pincode":
        return "Please provide a valid six-digit PIN code so I can check coverage."
    if status == "configured_uncovered":
        return f"PIN {result['pincode']} is configured, but no eligible provider currently serves it."
    if status == "no_category_match":
        alternatives = ", ".join(sorted({item["name"] for item in result.get("other_categories", [])}))
        suffix = f" Other available categories there are: {alternatives}." if alternatives else ""
        return f"No eligible provider was found for that category in {result.get('area_name', 'that area')} (PIN {result['pincode']}).{suffix}"
    if status == "category_ambiguous":
        options = ", ".join(item["name"] for item in result.get("categories", []))
        return f"Which category did you mean: {options}?"
    if status == "category_inactive":
        return "That category is not currently active on FixNow."
    if status == "category_not_found":
        return "I could not match that service category to an active FixNow category."
    return "I could not confirm provider coverage from the current FixNow records."


def build_pending_action(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Create a one-shot, read-only action descriptor for an unambiguous match."""
    if result.get("status") != "ok" or not result.get("category"):
        return None
    return {
        "action": "provider_search",
        "category_slug": result["category"]["slug"],
        "pincode": result["pincode"],
        "area_name": result.get("area_name", ""),
    }
