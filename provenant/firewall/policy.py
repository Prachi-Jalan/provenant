"""Just-in-time policy: turn the owner's request into the narrowest rule set that fulfils it.

Rule 1: if the requester isn't the owner -> deny_all.
Rule 2: the recipient address comes ONLY from trusted records, never from the request text
        or anything the agent read.
"""
from __future__ import annotations

import re
import time

from provenant import records
from provenant.schemas import Policy

POLICY_TTL_SECONDS = 10 * 60
_VERBS = r"(?:pay|send|transfer|give|settle|reimburse)"
_ADDRESS_IN_TEXT = re.compile(r"0x[0-9a-fA-F]{6,}")


def make_policy(request: str, requester: str, owner_id: str = "owner") -> Policy:
    # Rule 1: only the owner can authorize money movement.
    if requester != owner_id:
        return Policy.deny_all(f"Requester '{requester}' is not the owner; nobody else can authorize payments.")

    text = (request or "").strip()
    if not re.search(_VERBS, text, re.I):
        return Policy.deny_all("Request doesn't ask for a payment.")

    payee = _find_payee(text)
    if payee is None:
        return Policy.deny_all("Payee is not in trusted contacts or vendors. Add them to records first.")

    kind, name, info = payee
    amount = _find_amount(text)

    if kind == "contact":
        if amount is None:
            return Policy.deny_all(f"No amount given for {name}.")
        address, cap = info, amount
    else:  # vendor
        address = info["address"]
        cap = amount if amount is not None else float(info.get("typical_max", 0))
        if cap <= 0:
            return Policy.deny_all(f"No amount given and no typical range on record for {name}.")

    return Policy(
        intent_text=text,
        allowed_recipients=[address],          # Rule 2: from records only
        max_amount=float(cap),
        token="USDC",
        actions=["transfer"],
        max_uses=1,
        expires_at=time.time() + POLICY_TTL_SECONDS,
        payee_label=name.title(),
        repair_allowed=True,
    )


def _find_payee(text: str):
    """Match a trusted contact or vendor name as a whole word. Longest name wins."""
    low = text.lower()
    candidates = [("contact", n, a) for n, a in records.contacts().items()]
    candidates += [("vendor", n, v) for n, v in records.vendors().items()]
    candidates.sort(key=lambda c: -len(c[1]))
    for kind, name, info in candidates:
        if re.search(rf"\b{re.escape(name.lower())}\b", low):
            return kind, name, info
    return None


def _find_amount(text: str):
    # Strip addresses first so hex digits inside them aren't read as an amount.
    cleaned = _ADDRESS_IN_TEXT.sub(" ", text)
    # Ignore years / dates like "October 2026" being read as an amount: take the first number
    # that sits next to a money word, else the first bare number.
    money = re.search(r"(?:\$\s*|usdc\s*)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:usdc|usd|dollars|\$)", cleaned, re.I)
    if money:
        return float(money.group(1) or money.group(2))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\b", cleaned):
        n = float(m.group(1))
        if 1900 <= n <= 2100 and "." not in m.group(1):
            continue  # looks like a year ("October 2026 invoice"), not an amount
        return n
    return None
