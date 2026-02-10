from __future__ import annotations

import re


ORG_ID_MIN_LENGTH = 2
ORG_ID_MAX_LENGTH = 64
_ORG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def normalize_org_id(value: str) -> str:
    cleaned = value.strip().lower()
    if len(cleaned) < ORG_ID_MIN_LENGTH or len(cleaned) > ORG_ID_MAX_LENGTH:
        raise ValueError(
            f"org_id must be between {ORG_ID_MIN_LENGTH} and {ORG_ID_MAX_LENGTH} characters"
        )
    if not _ORG_ID_RE.fullmatch(cleaned):
        raise ValueError(
            "org_id may only contain lowercase letters, numbers, '-' and '_'"
        )
    return cleaned


def is_valid_org_id(value: str | None) -> bool:
    if not value:
        return False
    try:
        normalize_org_id(value)
    except ValueError:
        return False
    return True

