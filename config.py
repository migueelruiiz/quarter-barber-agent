"""
config.py — Single source of truth for Quarter Barber Agent structured data.

This file contains all business data that, if wrong, would cause a real
booking error (wrong barber, wrong duration, wrong hours) or a wrong price
quoted to a real customer. It must never be duplicated or re-derived from
free text in the LLM prompt — see CLAUDE.md, "Key decisions and constraints".

This module has zero internal project imports (no `src.*` imports). It is
pure data plus serialization functions that only read this module's own
data — no business logic that depends on external state (calendar, current
time, etc.) belongs here.

Data provenance:
- Service durations, working hours, barber assignment fallback order, and
  bleaching eligibility. See docs/quarter_barber_spec.md Section 1.5 and 
  Section 2 (R-15, R-16, R-17).
- colorId per barber: See CLAUDE.md, "Key decisions and constraints".
- Prices: currency EUR (business is located in Spain).

Design note — duration and price are deliberately separate structures.
SERVICES models booking duration categories only. Duration is coarse: e.g.
every haircut variant (standard, fade, child, senior) takes the same 30
minutes, and every color-related service (fantasy color, highlights, plain
color) uses the same duration scale as bleaching (R-16), regardless of
price. PRICE_MENU models the actual price list, which is finer-grained than
duration — multiple price variants map to a single duration category. These
must not be merged into one structure: doing so would force picking one
arbitrary price per duration category and silently discarding real business
data.

Naming convention: dictionary keys use plain ASCII (no accented characters).
"""

from zoneinfo import ZoneInfo
from datetime import time


# ---------------------------------------------------------------------------
# Services — duration only, used for Calendar booking (R-10, R-12, R-16)
# ---------------------------------------------------------------------------
# duration_minutes is the value ALWAYS reserved on the Calendar, not a
# "typical" estimate. For any service including bleaching-scale color work,
# this is the MAXIMUM documented duration (R-16) — reserving less risks a
# real double-booking if the actual service overruns; reserving the max
# only costs available inventory, which the barber can correct manually by
# shortening the Calendar event afterward (see CLAUDE.md "Bleaching
# duration"). Confirmed by the owner: all price variants within a category
# (e.g. corte / corte_fade / corte_infantil / corte_jubilado) share the same
# duration — see PRICE_MENU below for how price variants map here.

SERVICES = {
    "corte": {"duration_minutes": 30},
    "barba": {"duration_minutes": 30},
    "corte_barba": {"duration_minutes": 60},
    "decoloracion": {"duration_minutes": 120},         # max of 90-120 min range
    "decoloracion_corte": {"duration_minutes": 150},
    "decoloracion_corte_barba": {"duration_minutes": 180},
}


# ---------------------------------------------------------------------------
# Price menu — transcribed from the business's official Instagram photo
# ---------------------------------------------------------------------------
# All prices in EUR. `duration_category` cross-references SERVICES above,
# for documentation purposes only — it is not consumed by any lookup, since
# combo bookings (corte_barba, decoloracion_corte, etc.) have no combo price
# in the source photo. When a customer asks about a combo price, the agent
# must present it as the sum of its component prices, not as a single stored
# value here — do not invent a combo price that isn't in the source data
# (R-4/R-8).
#
# Per the owner's decision: when a customer asks about a price (e.g. "how
# much is a haircut"), the agent presents the full set of variants for that
# category (as the Instagram photo itself does), rather than asking the
# customer's age or profile to pick one. This is a deliberate simplification
# — customers rarely ask about price via WhatsApp in practice, and the
# in-person system already runs on the same honor basis.

PRICE_MENU = {
    "corte": {"price_eur": 15.00, "duration_category": "corte"},
    "corte_fade": {"price_eur": 15.00, "duration_category": "corte"},
    "corte_infantil": {"price_eur": 12.00, "duration_category": "corte"},
    "corte_jubilado": {"price_eur": 12.00, "duration_category": "corte"},
    "arreglo_barba": {"price_eur": 10.00, "duration_category": "barba"},
    "afeitado": {"price_eur": 15.00, "duration_category": "barba"},
    "colores_fantasia": {"price_eur": 50.00, "duration_category": "decoloracion"},
    "mechas": {"price_eur": 30.00, "duration_category": "decoloracion"},
    "color": {"price_eur": 20.00, "duration_category": "decoloracion"},
}


# ---------------------------------------------------------------------------
# Barbers
# ---------------------------------------------------------------------------
# color_id: string, exactly as returned by the Calendar API's `event` color
# map (never int, never the `calendar` color map — see CLAUDE.md "Google
# Calendar colorId"). Juan's color_id is None: his events carry no explicit
# colorId and inherit the calendar's default color (visually Peacock in
# production). This is NOT equivalent to color_id "7" — do not hardcode "7"
# for Juan under any circumstance; check_slot_available must match None
# explicitly for him.
#
# day_off follows Python's date.weekday() convention: Monday=0 ... Sunday=6.
# This is independent from WORKING_HOURS below — a barber can be off on a
# day the business itself is open. Kept as a single day_off field (not a
# separate working_days list) for all four barbers for schema symmetry,
# even though Juan's day_off (Saturday) combined with the business-wide
# Sunday closure in WORKING_HOURS yields 5 working days instead of the
# 6-minus-1 pattern of the other three — that asymmetry is a fact about
# the business, not something that should be reflected in an inconsistent
# data model.
#
# eligible_services keys must match SERVICES exactly. Bleaching-scale color
# services are restricted to Dylan and Juan (R-13). Do not add a separate
# "priority" field for Dylan over Juan — filtering SENIORITY_ORDER below by
# eligible_services already produces Dylan first.

BARBERS = {
    "dylan": {
        "color_id": "9",  # Blueberry
        "day_off": 1,  # Tuesday
        "lunch_break": (time(15, 0), time(16, 0)),
        "eligible_services": [
            "corte", "barba", "corte_barba",
            "decoloracion", "decoloracion_corte", "decoloracion_corte_barba",
        ],
    },
    "yuri": {
        "color_id": "6",  # Tangerine
        "day_off": 3,  # Thursday
        "lunch_break": (time(14, 0), time(15, 0)),
        "eligible_services": ["corte", "barba", "corte_barba"],
    },
    "rafa": {
        "color_id": "10",  # Basil
        "day_off": 2,  # Wednesday
        "lunch_break": (time(15, 0), time(16, 0)),
        "eligible_services": ["corte", "barba", "corte_barba"],
    },
    "juan": {
        "color_id": None,  # inherits calendar default (visually Peacock)
        "day_off": 5,  # Saturday — combined with Sunday closure below,
                       # this means Juan effectively only works Mon-Fri
        "lunch_break": (time(14, 0), time(15, 0)),
        "eligible_services": [
            "corte", "barba", "corte_barba",
            "decoloracion", "decoloracion_corte", "decoloracion_corte_barba",
        ],
    },
}


# ---------------------------------------------------------------------------
# Assignment fallback (R-15)
# ---------------------------------------------------------------------------
SENIORITY_ORDER = ["dylan", "yuri", "rafa", "juan"]


# ---------------------------------------------------------------------------
# Business hours (R-10, R-18)
# ---------------------------------------------------------------------------
# Keyed by date.weekday(): Monday=0 ... Sunday=6. Value is
# (open_time, close_time) as "HH:MM" strings, or None if the business is
# closed that day for everyone, regardless of individual barber day_off.

WORKING_HOURS = {
    0: ("10:00", "20:00"),  # Monday
    1: ("10:00", "20:00"),  # Tuesday
    2: ("10:00", "20:00"),  # Wednesday
    3: ("10:00", "20:00"),  # Thursday
    4: ("10:00", "20:00"),  # Friday
    5: ("09:00", "14:00"),  # Saturday
    6: None,                # Sunday — closed
}


# ---------------------------------------------------------------------------
# Calendar API configuration
# ---------------------------------------------------------------------------
# CALENDAR_ID: no calendar beyond the OAuth-authenticated account's own
# calendar exists for this project. "primary" resolves to that account's
# calendar — currently the quarter-barber-dev calendar during development
# (see CLAUDE.md, "Development environment"). This must be confirmed with
# Miguel before it is relied on against the production barbershop calendar.
CALENDAR_ID = "primary"

# TIMEZONE: the business is located in Madrid (see CLAUDE.md header
# address). Used for every tz-aware datetime sent to the Calendar API.
TIMEZONE = ZoneInfo("Europe/Madrid")


# ---------------------------------------------------------------------------
# Cached context prefix (spec Section 3.5)
# ---------------------------------------------------------------------------

def render_price_menu() -> str:
    """
    Build the cached system-prompt prefix string listing the full price
    menu, generated once at load time and injected into the agent's system
    prompt (never re-derived from model memory at request time — see
    spec.md Section 3.5). This is consumed by the agent at runtime, not
    something called or edited manually.

    Presents every price variant, mirroring the business's own Instagram
    price list layout — the agent is expected to show the relevant subset
    (e.g. all haircut variants) when asked, rather than asking the customer
    which variant applies (see PRICE_MENU comment above).

    Raises NotImplementedError if any entry is missing a price. Currently
    defensive only — all entries are populated from the source photo.
    """
    missing = [name for name, data in PRICE_MENU.items() if data["price_eur"] is None]
    if missing:
        raise NotImplementedError(
            f"Cannot render price menu — prices not yet transcribed for: "
            f"{', '.join(missing)}. Source: business Instagram photo (R-8)."
        )

    lines = [
        f"- {name.replace('_', ' ').title()}: {data['price_eur']:.2f}\u20ac"
        for name, data in PRICE_MENU.items()
    ]
    return "\n".join(lines)