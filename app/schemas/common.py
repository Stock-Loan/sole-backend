from __future__ import annotations

from enum import Enum


class MaritalStatus(str, Enum):
    SINGLE = "SINGLE"
    MARRIED = "MARRIED"
    DOMESTIC_PARTNER = "DOMESTIC_PARTNER"
    DIVORCED = "DIVORCED"
    SEPARATED = "SEPARATED"
    WIDOWED = "WIDOWED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value):  # type: ignore[override]
        try:
            normalized = normalize_marital_status(value)
        except Exception:
            return cls.UNKNOWN
        return normalized or cls.UNKNOWN


def normalize_marital_status(value: str | MaritalStatus | None) -> MaritalStatus | None:
    if value is None:
        return None
    if isinstance(value, MaritalStatus):
        return value
    cleaned = str(value).strip()
    if not cleaned:
        return None
    normalized = cleaned.upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "DOMESTICPARTNER": MaritalStatus.DOMESTIC_PARTNER,
        "DOMESTIC_PARTNERSHIP": MaritalStatus.DOMESTIC_PARTNER,
        "PARTNER": MaritalStatus.DOMESTIC_PARTNER,
        "UNSPECIFIED": MaritalStatus.UNKNOWN,
        "N/A": MaritalStatus.UNKNOWN,
        "NA": MaritalStatus.UNKNOWN,
        "NONE": MaritalStatus.UNKNOWN,
    }
    if normalized in aliases:
        return aliases[normalized]
    member = MaritalStatus._value2member_map_.get(normalized)
    if member is not None:
        return member
    raise ValueError("Invalid marital_status value")
