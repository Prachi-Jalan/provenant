"""Claim check: did the PAYEE THEMSELF tell us their wallet changed?

Why this exists
    Repair pays the address we have on record. That's right when a *stranger* claims
    "Rahul's new wallet is 0x...": the stranger has no say over Rahul's wallet.
    It's wrong when Rahul himself says "lost my old wallet, use 0x... now". Repairing
    would then pay an address he says he no longer controls (and if it was stolen, the
    thief gets the money). We can't verify the new address either. So: ESCALATE, never repair.

How it decides
    1. Who is the payee?             policy.payee_label ("Rahul", "Acme")
    2. Which sources ARE the payee?  their verified channels (below). Exact match only:
                                     "telegram:rahul" counts, "telegram:rahul_k_2" does not.
    3. Did one of those sources mention an address that isn't on record for anyone?
       -> fail (hard). The check doesn't depend on tx.to, so it also fails for the repaired
          tx, and the firewall ends at ESCALATE instead of REPAIRED.

Verified channels
    Default: "telegram:<payee name>", matching how agent/tools.py names chat senders.
    Optional data/channels.json adds or replaces them, e.g.
        {"Rahul": ["telegram:rahul"], "Acme": ["telegram:acme_billing", "email:billing@acme.com"]}
    Invoices are NOT payee channels by default: anyone can send a PDF that says "Acme".
    A fake invoice is an attack (repair it). Only a channel we verified belongs to the payee
    counts as the payee speaking.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from provenant import records
from provenant.schemas import CheckResult, ContentItem, Policy, ProposedTx

NAME = "claim"
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
CHANNELS_FILE = records.DATA_DIR / "channels.json"


def payee_channels(payee: str, channels_file: Path | None = None) -> set[str]:
    """Sources that we've verified belong to this payee (lowercase)."""
    name = payee.lower()
    channels = {f"telegram:{name}"}
    path = channels_file or CHANNELS_FILE
    if path.exists():
        configured = {k.lower(): v for k, v in json.loads(path.read_text()).items()}
        if name in configured:
            channels = {c.lower() for c in configured[name]}
    return channels


def _known_addresses() -> set[str]:
    """Every address on record, for anyone. Mentioning Priya's address isn't a wallet change."""
    known = set(records.contacts().values())
    known |= {v["address"] for v in records.vendors().values()}
    return known


def claim_check(tx: ProposedTx, policy: Policy, contents: list[ContentItem],
                channels_file: Path | None = None) -> CheckResult:
    payee = policy.payee_label
    if not payee or not policy.allowed_recipients:
        return CheckResult(NAME, True, "No payee to check claims for.")

    own = payee_channels(payee, channels_file)
    known = _known_addresses()
    for item in contents:
        if item.source.lower() not in own:
            continue
        new = sorted({a.lower() for a in _ADDRESS.findall(item.text or "")} - known)
        if new:
            addr = new[0]
            return CheckResult(
                NAME, False,
                f"{payee} themself ({item.source}) says to use a new address {addr[:6]}…{addr[-4:]}. "
                f"We can't verify it, and paying the old one may pay a wallet they no longer control. "
                f"Confirm with {payee} on another channel, then update the records.",
                hard=True)
    return CheckResult(NAME, True, f"{payee} hasn't claimed a new address.")
