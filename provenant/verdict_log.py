"""Shared verdict log. Bot + demo WRITE it, dashboard + eval READ it.
Format: one JSON object per line in logs/verdicts.jsonl (see CONTRACTS.md).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .schemas import Verdict

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "verdicts.jsonl"


def log_verdict(verdict: Verdict, request: str, requester: str, channel: str) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "request": request,
        "requester": requester,
        "channel": channel,          # "telegram" | "demo" | "eval"
        **verdict.to_dict(),
    }
    LOG_FILE.parent.mkdir(exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def read_verdicts() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    return [json.loads(line) for line in LOG_FILE.read_text().splitlines() if line.strip()]
