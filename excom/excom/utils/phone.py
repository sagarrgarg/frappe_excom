"""
Shared phone number validation and normalization for Excom.

All DocTypes that store phone numbers should call validate_phone_number()
in their validate() hook to ensure consistent E.164 formatting.
"""

import re

import frappe
from frappe import _


_E164_PATTERN = re.compile(r"^\+?[1-9]\d{6,14}$")


def normalize_phone(number: str) -> str:
    """
    Strip non-digit characters (except leading +) and return a clean number.
    Does NOT add a country code — the input must already have one.

    Args:
        number: Raw phone string, e.g. "+91 98765-43210"

    Returns:
        Cleaned string like "919876543210"
    """
    if not number:
        return ""
    has_plus = number.strip().startswith("+")
    digits = re.sub(r"[^\d]", "", number)
    if has_plus:
        return f"+{digits}"
    return digits


def validate_phone_number(number: str, field_label: str = "Phone") -> str:
    """
    Validate and normalize a phone number to E.164-compatible format.

    Enforces:
    - 7-15 digit length (ITU-T E.164 spec)
    - Must start with a non-zero country code digit
    - Strips all non-numeric chars (except leading +)

    Args:
        number: Raw phone string
        field_label: Human-readable field name for error messages

    Returns:
        Normalized phone string (with leading + if present)

    Raises:
        frappe.ValidationError if the number is invalid
    """
    if not number:
        return ""

    cleaned = normalize_phone(number)
    check = cleaned.lstrip("+")

    if not check:
        frappe.throw(
            _("{0} is empty after cleaning: {1}").format(field_label, number),
            frappe.ValidationError,
        )

    if len(check) < 7 or len(check) > 15:
        frappe.throw(
            _("{0} must be 7-15 digits (got {1} digits): {2}").format(
                field_label, len(check), number
            ),
            frappe.ValidationError,
        )

    if not _E164_PATTERN.match(cleaned):
        frappe.throw(
            _("{0} is not a valid phone number: {1}").format(field_label, number),
            frappe.ValidationError,
        )

    return cleaned


# India writes the same mobile four ways, and different parts of a CRM pick different ones: a
# telephony webhook sends 919876543210, an imported contact list holds 09876543210, somebody types
# 9876543210, and an E.164 field stores +919876543210. Matched as strings those are four people.
_NATIONAL_DIGITS = 10
_INDIA = "91"


def phone_variants(number: str) -> list[str]:
    """Every spelling of one number, exactly — never a LIKE.

    Deliberately narrow. The obvious version, "anything sharing the last ten digits", quietly
    merges a foreign number into an Indian one: +1 921 702 5599 ends in the same ten digits as
    +91 92170 25599 and is a different person. So the national-format variants are only produced
    for numbers that actually read as Indian — a 12-digit 91…, an 11-digit 0…, or a bare 10-digit
    national number — and anything else gets only its own two spellings.
    """
    bare = "".join(ch for ch in str(number or "") if ch.isdigit())
    if not bare:
        return []

    out = [bare, f"+{bare}"]

    national = ""
    if len(bare) == len(_INDIA) + _NATIONAL_DIGITS and bare.startswith(_INDIA):
        national = bare[len(_INDIA) :]
    elif len(bare) == _NATIONAL_DIGITS + 1 and bare.startswith("0"):
        national = bare[1:]
    elif len(bare) == _NATIONAL_DIGITS:
        national = bare

    if national:
        out += [national, f"0{national}", f"{_INDIA}{national}", f"+{_INDIA}{national}"]

    seen, unique = set(), []
    for value in out:
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return unique
