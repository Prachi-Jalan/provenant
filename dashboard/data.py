"""Data shaping for the dashboard. Kept free of Streamlit so it can be tested with pytest."""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "eval" / "eval_summary.json"
EXPLORER = "https://sepolia.basescan.org/tx/"
DECISION_ORDER = ["BLOCK", "ESCALATE", "REPAIRED", "ALLOW"]
CHECK_LABELS = {"policy": "Policy", "provenance": "Provenance", "intent": "Intent (LLM judge)",
                "reputation": "Reputation (TigerGraph)", "attack_pattern": "Attack text (TigerGraph vectors)"}


def short(addr: str | None) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if addr and len(addr) > 12 else (addr or "")


def describe_tx(tx: dict | None) -> str:
    if not tx:
        return "nothing sent"
    amt = tx.get("amount")
    shown = "UNLIMITED" if amt in (None, "inf", "Infinity") or (isinstance(amt, float) and math.isinf(amt)) else f"{float(amt):g}"
    return f"{tx.get('action', '?')} {shown} {tx.get('token', 'USDC')} → {short(tx.get('to'))}"


def load_summary() -> dict | None:
    return json.loads(SUMMARY.read_text()) if SUMMARY.exists() else None


def headline(summary: dict) -> dict:
    """The four numbers for the top of the page."""
    fw, none = summary["setups"]["firewall"], summary["setups"]["no_firewall"]
    return {"attacks": fw["attacks"], "unsafe_with": fw["unsafe_payments"], "unsafe_without": none["unsafe_payments"],
            "repaired": fw["attacks_auto_repaired"], "legit": fw["legit"], "legit_paid": fw["legit_completed"],
            "false_blocks": fw["false_blocks"]}


def setup_table(summary: dict) -> list[dict]:
    n = summary["setups"]["firewall"]["attacks"]
    rows = []
    for name, s in summary["setups"].items():
        rows.append({"setup": name, "attacks stopped": n - s["unsafe_payments"], "unsafe payments": s["unsafe_payments"],
                     "auto-repaired": s["attacks_auto_repaired"], "legit paid": s["legit_completed"],
                     "false blocks": s["false_blocks"]})
    return rows


def single_check_strength(summary: dict) -> dict[str, int]:
    """Attacks each check stops on its own (from the only_<check> runs)."""
    n = summary["setups"]["firewall"]["attacks"]
    return {CHECK_LABELS.get(k[5:].replace("attack_text", "attack_pattern"), k[5:]): n - s["unsafe_payments"]
            for k, s in summary["setups"].items() if k.startswith("only_")}


def verdict_rows(verdicts: list[dict]) -> list[dict]:
    """Newest first, flattened for the list view."""
    rows = []
    for v in reversed(verdicts):
        failed = [c for c in v.get("checks", []) if not c.get("passed")]
        rows.append({"ts": v.get("ts", ""), "decision": v.get("decision"), "request": v.get("request"),
                     "requester": v.get("requester"), "channel": v.get("channel"),
                     "proposed": describe_tx(v.get("original")), "sent": describe_tx(v.get("final_tx")),
                     "risk": v.get("risk"), "failed_checks": [c["name"] for c in failed],
                     "message": v.get("message", ""), "tx_link": tx_link(v.get("tx_hash")), "raw": v})
    return rows


def check_rows(verdict: dict) -> list[dict]:
    return [{"check": CHECK_LABELS.get(c["name"], c["name"]),
             "result": "pass" if c.get("passed") else "FAIL",
             "kind": "hard (can block)" if c.get("hard", True) else "soft (adds risk)",
             "reason": c.get("reason", "")} for c in verdict.get("checks", [])]


def tx_link(tx_hash: str | None) -> str | None:
    if not tx_hash or "DRY" in tx_hash.upper():
        return None
    return EXPLORER + tx_hash


def decision_counts(verdicts: list[dict]) -> dict[str, int]:
    counts = {d: 0 for d in DECISION_ORDER}
    for v in verdicts:
        counts[v.get("decision")] = counts.get(v.get("decision"), 0) + 1
    return counts
