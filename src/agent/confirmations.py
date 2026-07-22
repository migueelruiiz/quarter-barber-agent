"""
Deterministic, server-side confirmation templates for book_appointment and
reschedule_appointment.

Implements the fix for the three bugs in
docs/loop_confirmation_bugs_findings.md: confirmation text for these two
write tools is rendered entirely in code, never drafted by the model, for
the turn immediately after either tool succeeds. There is no free-form
model generation on this turn to glue (bug 1), the weekday is looked up
from the same deterministic source as the system prompt's 14-day table,
never left to the model's own arithmetic (bug 2), and the template
controls exactly which fields are rendered -- end time and every internal
ID (event_id, color_id) are structurally never included (bug 3).

Same principle already applied to config.py: structured data belongs in
code, never re-derived by the model at request time.

Barber display name is derived from color_id via config.BARBERS, the same
"colorId is the source of truth" reverse lookup already used in
src/tools/find_appointments.py -- never trusted as a free-standing string,
since a hallucinated barber name would send a real customer to the wrong
person's calendar slot.

Price is deliberately omitted from both templates. `service`, as passed to
book_appointment, is a coarse SERVICES duration category (e.g. "barba"),
but config.PRICE_MENU has multiple, differently-priced variants per
category with no uniform price for any of the 6 categories (e.g. barba:
arreglo_barba=10€ vs afeitado=15€; corte: 15€ vs 12€; decoloracion:
50€/30€/20€ three ways). There is no structured signal anywhere in the
booking pipeline for which specific variant the customer meant -- only the
model's free-text reading of their wording, earlier in the conversation,
ever picked one. Rendering a price here would mean guessing among real,
differently-priced options, which is exactly the "never fabricate
information... must never guess prices" rule this project already commits
to (see CLAUDE.md) -- confirmed with the user as the intended behavior
rather than silently picking a variant.

client_name and service are also omitted from
render_reschedule_confirmation: RescheduleAppointmentArgs (schemas.py)
carries neither field, and reschedule_appointment's own contract never
receives or returns them (see docs/reschedule_appointment_spec.md, "No
service parameter") -- there is no structured value to render, only one
that would have to be scraped from an earlier, unrelated turn.

client_phone is included in both templates even though it isn't produced
by either tool's return value -- it's the one field that's always known
with certainty regardless of tool call arguments, since session_id *is*
the customer's phone number (see docs/react_loop_spec.md, "client_phone is
deliberately excluded from every tool schema"). Normalized the same way as
book_appointment/find_appointments (src/tools/_phone.py) before display.
"""

from datetime import datetime

import config
from src.tools._phone import normalize_spanish_phone


def _barber_display_name(color_id: str | None) -> str:
    color_to_barber = {data["color_id"]: name for name, data in config.BARBERS.items()}
    barber = color_to_barber.get(color_id)
    if barber is None:
        raise ValueError(f"No barber configured for color_id={color_id!r}")
    return barber.capitalize()


def _service_display_name(service: str) -> str:
    return service.replace("_", " ").title()


def _format_date(moment: datetime) -> str:
    weekday = config.SPANISH_WEEKDAYS[moment.weekday()]
    month = config.SPANISH_MONTHS[moment.month]
    return f"{moment.day} de {month} de {moment.year} ({weekday})"


def _format_time(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def _format_phone(client_phone: str) -> str:
    digits = normalize_spanish_phone(client_phone)
    groups = [digits[i : i + 3] for i in range(0, len(digits), 3)]
    return " ".join(groups)


def _closing_line() -> str:
    return f"Le esperamos en {config.SHOP_NAME}, {config.SHOP_ADDRESS}. ¡Hasta pronto!"


def render_booking_confirmation(
    client_name: str,
    service: str,
    start: datetime,
    color_id: str | None,
    client_phone: str,
) -> str:
    """Render the exact customer-facing confirmation text for a successful
    book_appointment call."""
    return (
        f"¡Perfecto, {client_name}! Su cita está confirmada:\n"
        f"- Servicio: {_service_display_name(service)}\n"
        f"- Barbero: {_barber_display_name(color_id)}\n"
        f"- Fecha: {_format_date(start)}\n"
        f"- Hora: {_format_time(start)}h\n"
        f"- Teléfono de contacto: {_format_phone(client_phone)}\n\n"
        f"{_closing_line()}"
    )


def render_reschedule_confirmation(
    new_start: datetime,
    color_id: str | None,
    client_phone: str,
) -> str:
    """Render the exact customer-facing confirmation text for a successful
    reschedule_appointment call. See module docstring for why this omits
    client_name/service/price, unlike render_booking_confirmation."""
    return (
        "¡Listo! Su cita ha sido reprogramada:\n"
        f"- Barbero: {_barber_display_name(color_id)}\n"
        f"- Nueva fecha: {_format_date(new_start)}\n"
        f"- Nueva hora: {_format_time(new_start)}h\n"
        f"- Teléfono de contacto: {_format_phone(client_phone)}\n\n"
        f"{_closing_line()}"
    )
