#!/usr/bin/env python3
"""
Normalize raw country and subdivision data into the format used by meta endpoints.

Inputs (expected paths):
- app/resources/countries.json: list of {"code": "...", "name": "..."}
- app/resources/subdivisions.json: list of {"country_code": "...", "code": "...", "name": "..."}

Outputs:
- Writes cleaned Python module app/resources/countries.py with:
    COUNTRIES = [{"code": "US", "name": "United States"}, ...]
    SUBDIVISIONS = {"US": [{"code": "CA", "name": "California"}, ...], ...}

Usage:
    python scripts/clean_locations.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
RAW_COUNTRIES = ROOT / "app" / "resources" / "countries.json"
RAW_SUBDIVISIONS = ROOT / "app" / "resources" / "subdivisions.json"
TARGET = ROOT / "app" / "resources" / "countries.py"


def load_countries() -> List[Dict[str, str]]:
    data = json.loads(RAW_COUNTRIES.read_text(encoding="utf-8"))
    cleaned = []
    seen = set()
    for row in data:
        # Support both alpha2/alpha3 keys or code
        code = str(row.get("code") or row.get("alpha2") or "").strip().upper()
        name = str(row.get("name", "")).strip()
        if not code or not name:
            continue
        if code in seen:
            continue
        seen.add(code)
        cleaned.append({"code": code, "name": name})
    cleaned.sort(key=lambda x: x["name"])
    return cleaned


def load_subdivisions() -> Dict[str, List[Dict[str, str]]]:
    data = json.loads(RAW_SUBDIVISIONS.read_text(encoding="utf-8"))
    cleaned: Dict[str, List[Dict[str, str]]] = {}
    for row in data:
        country_code = str(row.get("country") or row.get("country_code") or "").strip().upper()
        code = str(row.get("code") or "").strip().upper()
        if len(code) > 2 and "-" in code:
            code = code.split("-", 1)[-1]
        code = code[:2] if len(code) > 2 else code
        name_raw = str(row.get("name_en") or row.get("name") or "").strip()
        name = name_raw if name_raw else code
        if not country_code or not code or not name:
            continue
        cleaned.setdefault(country_code, [])
        cleaned[country_code].append({"code": code, "name": name})
    for country_code, items in cleaned.items():
        # Remove duplicates
        unique = {}
        for item in items:
            unique[item["code"]] = item["name"]
        cleaned[country_code] = [
            {"code": c, "name": n} for c, n in sorted(unique.items(), key=lambda x: x[1])
        ]
    return cleaned


def write_python(
    countries: List[Dict[str, str]], subdivisions: Dict[str, List[Dict[str, str]]]
) -> None:
    lines = [
        "COUNTRIES = [",
    ]
    for c in countries:
        lines.append(f'    {{"code": "{c["code"]}", "name": "{c["name"]}"}},')
    lines.append("]\n")
    lines.append("SUBDIVISIONS = {")
    for country_code, items in sorted(subdivisions.items()):
        lines.append(f'    "{country_code}": [')
        for item in items:
            lines.append(f'        {{"code": "{item["code"]}", "name": "{item["name"]}"}},')
        lines.append("    ],")
    lines.append("}")
    content = "\n".join(lines) + "\n"
    TARGET.write_text(content, encoding="utf-8")


def main() -> None:
    if not RAW_COUNTRIES.exists() or not RAW_SUBDIVISIONS.exists():
        raise SystemExit("Raw countries.json or subdivisions.json not found under app/resources/")
    countries = load_countries()
    subdivisions = load_subdivisions()
    write_python(countries, subdivisions)
    print(f"Wrote cleaned data to {TARGET}")


if __name__ == "__main__":
    main()
