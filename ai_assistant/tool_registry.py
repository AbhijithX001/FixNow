"""Allow-listed, database-grounded tools for the customer assistant."""

from typing import Any

from .provider_discovery import find_providers, get_eligible_provider

MAX_TOOL_RESULTS = 8

TOOL_DEFINITIONS = [
    {
        "name": "search_providers",
        "description": "Find approved and currently available FixNow providers by service category and PIN or area.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Supported service category or exact category slug."},
                "location": {"type": "string", "description": "Six-digit PIN or configured area name."},
            },
            "required": ["category", "location"],
        },
    },
    {
        "name": "get_provider_details",
        "description": "Revalidate and return public details for a provider from a current discovery result.",
        "parameters": {
            "type": "object",
            "properties": {
                "provider_id": {"type": "integer", "description": "Provider ID returned by search_providers."},
                "location": {"type": "string", "description": "PIN or area used for the current search."},
                "category": {"type": "string", "description": "Category used for the current search."},
            },
            "required": ["provider_id", "location", "category"],
        },
    },
]


def _bounded(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _bounded(item) for key, item in list(value.items())[:20]}
    if isinstance(value, list):
        return [_bounded(item) for item in value[:MAX_TOOL_RESULTS]]
    if isinstance(value, str):
        return value[:500]
    return value


def execute_tool(name: str, arguments: Any) -> dict[str, Any]:
    """Validate tool name/arguments and execute only an allow-listed query."""
    if name not in {item["name"] for item in TOOL_DEFINITIONS}:
        return {"ok": False, "error": "Tool is not permitted."}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "Tool arguments must be an object."}

    if name == "search_providers":
        category = arguments.get("category")
        location = arguments.get("location")
        if not isinstance(category, str) or not isinstance(location, str):
            return {"ok": False, "error": "category and location are required strings."}
        result = find_providers(location.strip(), category.strip())
        return {"ok": True, "result": _bounded(result)}

    provider_id = arguments.get("provider_id")
    location = arguments.get("location")
    category = arguments.get("category")
    if not isinstance(location, str) or not isinstance(category, str):
        return {"ok": False, "error": "location and category are required strings."}
    result = get_eligible_provider(provider_id, location.strip(), category.strip())
    if result is None:
        return {"ok": False, "error": "Provider is not currently eligible for this search."}
    return {"ok": True, "result": _bounded(result)}
