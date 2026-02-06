from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_database_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url

    parts = urlsplit(url)
    scheme = parts.scheme

    if scheme in {"postgres", "postgresql"}:
        scheme = "postgresql+psycopg"
    elif scheme == "postgresql+asyncpg":
        scheme = "postgresql+psycopg"

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    ssl_val = query.get("ssl")
    if ssl_val is not None:
        normalized = ssl_val.lower()
        query.pop("ssl", None)
        if "sslmode" not in query:
            if normalized in {"0", "false", "no", "off", "disable"}:
                query["sslmode"] = "disable"
            elif normalized in {"require", "verify-ca", "verify-full"}:
                query["sslmode"] = normalized
            else:
                query["sslmode"] = "require"

    new_query = urlencode(query, doseq=True)
    return urlunsplit((scheme, parts.netloc, parts.path, new_query, parts.fragment))
