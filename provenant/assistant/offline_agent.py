"""Offline stand-in agent so the assistant runs with no API keys and no Person-1 code.

OfflineNaiveAgent behaves like the 'fooled' agent in the demo: it trusts the most recent address it
read, decodes base64 commands, and obeys hidden 'approve unlimited' text. It only PROPOSES.
It holds no key and never calls chain.wallet.

ContractAgentAdapter wraps an object that follows the CONTRACTS.md agent interface
(.propose(request, requester, inbox) and .replan(feedback)) so Person 1's agent can be plugged in.
"""
from __future__ import annotations

import math
import re
from typing import Protocol

from provenant.schemas import ContentItem, ProposedTx

from .textutil import expand_encoded, find_addresses, parse_amount

_PAY_VERB = re.compile(r"\b(?:pay|send|transfer|give|settle|reimburse)\b", re.I)
_APPROVE = re.compile(r"\bapprove\b", re.I)
_UNLIMITED = re.compile(r"unlimited|full access|infinite|max(?:imum)? (?:uint|allowance|approval)|all (?:of )?(?:my )?usdc", re.I)
_INVOICE_AMOUNT = re.compile(r"amount[^0-9\n]{0,12}(\d+(?:\.\d+)?)", re.I)


class AgentLike(Protocol):
    def propose(self, request: str, requester: str, contents: list[ContentItem]) -> list[ProposedTx]: ...

    def replan(
        self, request: str, requester: str, contents: list[ContentItem], feedback: str
    ) -> list[ProposedTx]: ...


def build_tx(action: str, to: str, amount: float, context_ids: list[str], token: str = "USDC") -> ProposedTx:
    amt = "UNLIMITED" if math.isinf(amount) else f"{amount:g}"
    to = to.lower()
    return ProposedTx(
        to=to, token=token, action=action, amount=amount,
        decoded=f"{action} {amt} {token} to {to}", raw_calldata="", context_ids=list(context_ids),
    )


class OfflineNaiveAgent:
    name = "naive (offline)"

    def propose(self, request: str, requester: str, contents: list[ContentItem]) -> list[ProposedTx]:
        ids = [c.id for c in contents]
        texts = [expand_encoded(c.text or "") for c in contents]

        # 1. Hidden "approve unlimited ..." text in anything it read: a naive agent obeys it.
        for text in texts:
            if _APPROVE.search(text) and _UNLIMITED.search(text):
                spender = (find_addresses(text) or [a for t in texts for a in find_addresses(t)] or [None])[0]
                if spender:
                    return [build_tx("approve", spender, math.inf, ids)]

        # 2. Otherwise: is this a payment request at all?
        expanded_request = expand_encoded(request or "")
        if not _PAY_VERB.search(expanded_request):
            return []

        amount = parse_amount(expanded_request)
        if amount is None:  # e.g. "pay Acme's October invoice": read the amount off the invoice
            for c, text in zip(contents, texts):
                if c.trust == "untrusted":
                    m = _INVOICE_AMOUNT.search(text)
                    if m:
                        amount = float(m.group(1))
        if amount is None:
            return []

        # 3. Recipient = the most recent address it read; else look the payee up in the trusted records.
        read_addresses = [a for t in texts for a in find_addresses(t)]
        recipient = read_addresses[-1] if read_addresses else _lookup_payee_in_text(expanded_request)
        if recipient is None:
            return []
        return [build_tx("transfer", recipient, amount, ids)]

    def replan(self, request: str, requester: str, contents: list[ContentItem], feedback: str) -> list[ProposedTx]:
        """Second attempt after a block: use ONLY the owner's own words + trusted records."""
        amount = parse_amount(request or "")
        recipient = _lookup_payee_in_text(request or "")
        if amount is None or recipient is None:
            return []
        return [build_tx("transfer", recipient, amount, [c.id for c in contents if c.trust == "trusted"])]


def _lookup_payee_in_text(text: str) -> str | None:
    try:
        from provenant import records
    except Exception:
        return None
    low = text.lower()
    names = list(records.contacts()) + list(records.vendors())
    for name in sorted(names, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name.lower())}\b", low):
            return records.lookup_payee(name)
    return None


class ContractAgentAdapter:
    """Adapts an agent that follows CONTRACTS.md (.propose(request, requester, inbox) / .replan(feedback))."""

    def __init__(self, agent) -> None:
        self._agent = agent
        self.name = type(agent).__name__

    def propose(self, request: str, requester: str, contents: list[ContentItem]) -> list[ProposedTx]:
        inbox = [c for c in contents if c.trust == "untrusted"]
        return list(self._agent.propose(request, requester, inbox) or [])

    def replan(self, request: str, requester: str, contents: list[ContentItem], feedback: str) -> list[ProposedTx]:
        return list(self._agent.replan(feedback) or [])
