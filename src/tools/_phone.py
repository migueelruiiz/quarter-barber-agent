"""
Shared phone-number normalization helper for tool files.

Not itself a tool -- no entry in CLAUDE.md's "Tools implemented" list.
Exists because book_appointment and find_appointments must apply the exact
same normalization rule (see each module's docstring for why), and this
repo has no other shared-logic location between tool files (config.py is
reserved for data + serialization only, per its own docstring).
"""

SPANISH_COUNTRY_CODE = "34"
SPANISH_NATIONAL_LENGTH = 9


def normalize_spanish_phone(phone: str) -> str:
    """Strip all non-digit characters. If the result is exactly 11 digits
    and starts with the Spanish country code ("34"), drop those first 2
    digits, leaving the 9-digit national number. Any other length/prefix
    combination is returned as-is (digits-only, but unmodified otherwise)
    -- this avoids false positives with other countries' codes."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == SPANISH_NATIONAL_LENGTH + 2 and digits.startswith(SPANISH_COUNTRY_CODE):
        return digits[2:]
    return digits
