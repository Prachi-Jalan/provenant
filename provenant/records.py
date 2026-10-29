"""Trusted records: the owner's contacts and vendor payment history.

These are the ONLY places a repair is allowed to take an address from.
Swap the JSON files for a DB / TigerGraph later; keep the function signatures.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .schemas import ContentItem

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache
def contacts() -> dict[str, str]:
    """name (lowercase) -> verified address (lowercase)."""
    raw = json.loads((DATA_DIR / "contacts.json").read_text())
    return {k.lower(): v.lower() for k, v in raw.items()}


@lru_cache
def vendors() -> dict[str, dict]:
    """vendor name (lowercase) -> {address, typical_min, typical_max, paid_count}."""
    raw = json.loads((DATA_DIR / "vendors.json").read_text())
    return {k.lower(): {**v, "address": v["address"].lower()} for k, v in raw.items()}


def lookup_payee(name: str) -> str | None:
    n = name.lower()
    if n in contacts():
        return contacts()[n]
    if n in vendors():
        return vendors()[n]["address"]
    return None


def records_as_content() -> list[ContentItem]:
    """Expose trusted records to provenance as trusted ContentItems."""
    lines = [f"{k}: {v}" for k, v in contacts().items()]
    lines += [f"{k}: {v['address']}" for k, v in vendors().items()]
    return [ContentItem(id="trusted_records", source="records:owner",
                        trust="trusted", text="\n".join(lines))]
