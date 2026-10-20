"""
FixNow AI Assistant — Deterministic Safety Guard
Runs BEFORE any LLM inference to intercept life/property emergency phrases.

This module is dependency-free (stdlib only) so it can be imported anywhere.
"""

import re

SAFETY_HAZARD_RULES = [
    {
        "type": "gas_leak",
        "patterns": [
            r"\b(lpg|gas)\b.*\b(smell|leak|leaking|hissing|burst)\b",
            r"\b(smell|odor|odour)\b.*\b(gas|cylinder|cooking gas)\b",
            r"\b(gas cylinder)\b.*\b(hiss|leak|open)\b",
        ],
        "guidance": (
            "EMERGENCY HAZARD (GAS LEAK): Immediately evacuate the premises. "
            "Do NOT operate any electrical switches, appliances, or open flames. "
            "Turn off the main cylinder regulator valve if safe to do so. "
            "Call the National Emergency Helpline (112) or LPG Emergency (1906) "
            "immediately from outside."
        ),
        "emergency_contacts": ["112", "1906 (LPG Helpline)", "101 (Fire)"],
    },
    {
        "type": "electrical_fire_sparks",
        "patterns": [
            r"\b(spark|sparks|sparking)\b.*\b(smoke|fire|meter|fuse|breaker|switchboard)\b",
            r"\b(spark|sparks|sparking)\b.*\b(socket|outlet|wire|wiring|electrical|panel)\b",
            r"\b(socket|outlet|wire|wiring|electrical|panel)\b.*\b(spark|sparks|sparking)\b",
            r"\b(smoke|black smoke|fire)\b.*\b(meter|fuse|box|electrical|socket|panel)\b",
            r"\b(burning smell)\b.*\b(wire|wiring|socket|switchboard|electrical)\b",
            r"\b(electric shock)\b",
            r"\b(fire|flames?|burning)\b.*\b(kitchen|house|home|room|building|socket|wire|electrical)\b",
            r"\b(kitchen|house|home|room|building|socket|wire|electrical)\b.*\b(fire|flames?|burning)\b",
        ],
        "guidance": (
            "EMERGENCY HAZARD (ELECTRICAL FIRE/SPARKS): Do NOT touch the electrical panel "
            "or appliances. Turn off the main power supply from outside if safe to do so. "
            "Do NOT use water on electrical fires. Evacuate if smoke or flames persist. "
            "Call the Fire Service (101) or Emergency Services (112) immediately."
        ),
        "emergency_contacts": ["112", "101 (Fire)"],
    },
    {
        "type": "structural_flooding",
        "patterns": [
            r"\b(pipe burst|burst pipe)\b.*\b(wall|flooding|rising|water rapidly)\b",
            r"\b(flooding)\b.*\b(entire floor|ground floor|living room|rapidly)\b",
            r"\b(flooding|flooded|water rising|water pouring|water everywhere)\b",
            r"\b(major flood)\b",
        ],
        "guidance": (
            "URGENT HAZARD (MAJOR FLOODING): Immediately shut off the main "
            "municipal/overhead water supply valve. Turn off the main electrical breaker "
            "if water is near outlets to prevent electrocution. "
            "Do NOT walk in standing water if wires or appliances are submerged."
        ),
        "emergency_contacts": ["112"],
    },
    {
        "type": "structural_collapse",
        "patterns": [
            r"\b(ceiling|wall|roof|balcony|structure)\b.*\b(collaps|falling|about to)\b",
            r"\b(collapsing|collapse imminent|about to collapse)\b",
        ],
        "guidance": (
            "URGENT STRUCTURAL HAZARD: Move away from the affected area and evacuate "
            "the building if it is safe to do so. Do not enter or stand beneath the "
            "damaged structure. Call Emergency Services (112) immediately."
        ),
        "emergency_contacts": ["112"],
    },
]


def check_safety_hazard(message: str) -> dict:
    """
    Evaluates a raw user message for immediate safety hazards.

    Returns a dict with ``is_hazard=True`` and guidance when a hazard is
    detected, or ``is_hazard=False`` when the message is safe to pass to
    the LLM.
    """
    clean_msg = message.lower().strip()

    for rule in SAFETY_HAZARD_RULES:
        for pat in rule["patterns"]:
            if re.search(pat, clean_msg):
                return {
                    "is_hazard": True,
                    "hazard_type": rule["type"],
                    "intent": "emergency_safety_alert",
                    "category_slug": None,
                    "problem_summary": (
                        f"Hazard detected: {rule['type'].replace('_', ' ').title()}"
                    ),
                    "safety_guidance": rule["guidance"],
                    "emergency_contacts": rule["emergency_contacts"],
                    "booking_allowed": False,
                    "needs_clarification": False,
                    "matched_pattern": pat,
                }

    return {
        "is_hazard": False,
        "hazard_type": None,
        "booking_allowed": True,
    }
