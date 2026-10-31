"""Shared interfaces. Everyone codes against these four shapes.

ContentItem  - anything the agent reads (A produces, B consumes)
Policy       - the just-in-time rule built from the OWNER's request (B produces)
ProposedTx   - what the agent wants to do (A produces)
Verdict      - firewall output (B + C produce, dashboard shows)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal

Trust = Literal["trusted", "untrusted"]
Action = Literal["transfer", "approve", "swap", "other"]


@dataclass
class ContentItem:
    id: str                 # e.g. "tg_msg_42", "invoice_acme_oct", "contacts"
    source: str             # e.g. "telegram:owner", "telegram:@stranger", "web:https://..."
    trust: Trust
    text: str
    # id of the item this one forwards / quotes / summarises (taint tracking, see graph/taint.py).
    # An item is only as trusted as everything it was derived from.
    derived_from: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Policy:
    intent_text: str                      # the owner's words, verbatim
    allowed_recipients: list[str]         # lowercase 0x addresses, from TRUSTED records only
    max_amount: float                     # in token units (e.g. 500.0 USDC)
    token: str = "USDC"
    actions: list[str] = field(default_factory=lambda: ["transfer"])
    max_uses: int = 1
    expires_at: str = field(
        default_factory=lambda: (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    )
    payee_label: str | None = None        # "Rahul", "Acme" (used by repair + dashboard)
    repair_allowed: bool = True           # can the firewall rebuild the tx itself?

    @classmethod
    def deny_all(cls, reason: str) -> "Policy":
        return cls(intent_text=reason, allowed_recipients=[], max_amount=0.0,
                   actions=[], repair_allowed=False)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProposedTx:
    to: str                   # beneficiary: recipient (transfer) or spender (approve), lowercase
    token: str
    action: str
    amount: float             # token units; float('inf') = unlimited approval
    decoded: str = ""         # human-readable, e.g. "send 500 USDC to 0xabc..."
    raw_calldata: str = ""    # 0x... (optional until chain layer is wired)
    context_ids: list[str] = field(default_factory=list)  # ContentItems the agent used

    def to_dict(self) -> dict:
        return asdict(self)


class Decision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REPAIRED = "REPAIRED"     # original blocked, safe replacement allowed
    ESCALATE = "ESCALATE"     # no safe option, ask the owner


@dataclass
class CheckResult:
    name: str                 # "policy" | "provenance" | "intent" | "reputation"
    passed: bool
    reason: str
    score: float | None = None
    hard: bool = True         # hard fail = block regardless of other checks


@dataclass
class Verdict:
    decision: Decision
    risk: float
    checks: list[CheckResult]
    original: ProposedTx
    final_tx: ProposedTx | None = None    # what actually gets signed (None if blocked/escalated)
    message: str = ""                     # owner-facing explanation
    tx_hash: str | None = None            # filled by chain layer after sending

    def to_dict(self) -> dict:
        d = asdict(self)
        d["decision"] = self.decision.value
        return d
