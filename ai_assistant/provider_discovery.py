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
        "display_name": display_name,
        "category": _category_summary(profile.category),
    }


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
    """Find eligible public provider summaries for a configured PIN code."""
    pin = str(pincode or "").strip()
    if not PINCODE_RE.fullmatch(pin):
        return {"status": "invalid_pincode", "pincode": pin, "providers": []}

    try:
        service_pin = ServicePincode.objects.get(pincode=pin)
    except ServicePincode.DoesNotExist:
        return {"status": "unknown_pincode", "pincode": pin, "providers": []}

    resolution = _resolve_category(category)
    if resolution["status"] == "ambiguous":
        return {
            "status": "category_ambiguous",
            "pincode": pin,
            "providers": [],
            "categories": resolution["matches"],
        }
    if resolution["status"] in {"inactive", "not_found"}:
        return {
            "status": f"category_{resolution['status']}",
            "pincode": pin,
            "providers": [],
            "categories": resolution["matches"],
        }

    profiles = _eligible_profiles().filter(service_pincodes=service_pin)
    if resolution["category"] is not None:
        profiles = profiles.filter(category=resolution["category"])

    profiles = profiles.select_related("user", "category").order_by("user__first_name", "user__last_name", "id")
    providers = [_provider_summary(profile) for profile in profiles]
    if providers:
        status = "ok"
    elif resolution["category"] is None:
        status = "configured_uncovered"
    else:
        status = "no_category_match"

    result: Dict[str, Any] = {
        "status": status,
        "pincode": pin,
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
    if status == "invalid_pincode":
        return "Please provide a valid six-digit PIN code so I can check coverage."
    if status == "configured_uncovered":
        return f"PIN {result['pincode']} is configured, but no eligible provider currently serves it."
    if status == "no_category_match":
        alternatives = ", ".join(sorted({item["name"] for item in result.get("other_categories", [])}))
        suffix = f" Other available categories there are: {alternatives}." if alternatives else ""
        return f"No eligible provider was found for that category in PIN {result['pincode']}.{suffix}"
    if status == "category_ambiguous":
        options = ", ".join(item["name"] for item in result.get("categories", []))
        return f"Which category did you mean: {options}?"
    if status == "category_inactive":
        return "That category is not currently active on FixNow."
    if status == "category_not_found":
        return "I could not match that service category to an active FixNow category."
    return "I could not confirm provider coverage from the current FixNow records."
