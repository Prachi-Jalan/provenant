"""ERC-20 calldata <-> ProposedTx. Owner: Person 1.

ERC-20 calldata = 4-byte selector + 32-byte words (each argument padded to 32 bytes):
  transfer(address,uint256)             0xa9059cbb
  approve(address,uint256)              0x095ea7b3
  transferFrom(address,address,uint256) 0x23b872dd   (decoded as action "other")

USDC has 6 decimals, so 1 USDC = 1_000_000 raw units. Pure Python: no web3 needed.
"""
from __future__ import annotations

import re
from decimal import Decimal

from provenant.schemas import ProposedTx

TRANSFER_SELECTOR = "a9059cbb"
APPROVE_SELECTOR = "095ea7b3"
TRANSFER_FROM_SELECTOR = "23b872dd"

MAX_UINT256 = 2**256 - 1
# An approval of this many raw units or more counts as "unlimited". Wallet UIs use
# 2**256-1 (some 2**255); nobody legitimately approves 2**128 raw units.
UNLIMITED_THRESHOLD_RAW = 2**128

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def is_address(value: str) -> bool:
    return bool(value) and bool(_ADDR_RE.match(value))


def normalize_address(value: str) -> str:
    """Lowercase 0x address, or ValueError."""
    value = (value or "").strip()
    if not is_address(value):
        raise ValueError(f"not a valid 0x address (need 0x + 40 hex chars): {value!r}")
    return value.lower()


def to_raw(amount: float, decimals: int = 6) -> int:
    """Human amount (500, 1.5) -> raw integer units. inf is not representable here."""
    if amount != amount or amount in (float("inf"), float("-inf")):
        raise ValueError("amount must be a finite number")
    if amount < 0:
        raise ValueError("amount must not be negative")
    return int((Decimal(str(amount)) * (10**decimals)).to_integral_value())


def from_raw(raw: int, decimals: int = 6) -> float:
    return float(Decimal(raw) / (10**decimals))


def _word_address(address: str) -> str:
    return normalize_address(address)[2:].rjust(64, "0")


def _word_uint(value: int) -> str:
    if value < 0 or value > MAX_UINT256:
        raise ValueError("uint256 out of range")
    return format(value, "x").rjust(64, "0")


def encode_erc20(action: str, to: str, amount: float, decimals: int = 6) -> str:
    """0x-prefixed calldata for a transfer or approve.

    amount=float('inf') is allowed ONLY for approve (becomes 2**256-1).
    """
    if action == "transfer":
        selector, raw = TRANSFER_SELECTOR, to_raw(amount, decimals)
    elif action == "approve":
        selector = APPROVE_SELECTOR
        raw = MAX_UINT256 if amount == float("inf") else to_raw(amount, decimals)
    else:
        raise ValueError(f"can only encode 'transfer' or 'approve', got {action!r}")
    return "0x" + selector + _word_address(to) + _word_uint(raw)


def describe(action: str, to: str, amount: float, token: str = "USDC") -> str:
    """One readable line, e.g. 'send 500 USDC to 0x...' / 'approve UNLIMITED USDC spending by 0x...'."""
    if action == "approve":
        amt = "UNLIMITED" if amount == float("inf") else f"{amount:g}"
        return f"approve {amt} {token} spending by {to}"
    if action == "transfer":
        return f"send {amount:g} {token} to {to}"
    return f"{action} involving {to}"


def decode_erc20(
    calldata: str,
    token: str = "USDC",
    decimals: int = 6,
    context_ids: list[str] | None = None,
) -> ProposedTx:
    """Decode ERC-20 calldata into a ProposedTx.

    Huge approvals -> amount = float('inf'), decoded = "approve UNLIMITED ...".
    Unknown selectors -> action "other" so the firewall can reject them.
    """
    data = (calldata or "").strip().lower()
    if data.startswith("0x"):
        data = data[2:]
    if len(data) < 8 or any(c not in "0123456789abcdef" for c in data):
        raise ValueError("calldata must be 0x-prefixed hex with at least a 4-byte selector")

    selector, args = data[:8], data[8:]
    ctx = list(context_ids or [])
    raw_hex = "0x" + data

    def word(i: int) -> str:
        chunk = args[i * 64 : (i + 1) * 64]
        if len(chunk) != 64:
            raise ValueError("calldata is too short for this function")
        return chunk

    if selector in (TRANSFER_SELECTOR, APPROVE_SELECTOR):
        to = "0x" + word(0)[-40:]
        raw_amount = int(word(1), 16)
        action = "transfer" if selector == TRANSFER_SELECTOR else "approve"
        if action == "approve" and raw_amount >= UNLIMITED_THRESHOLD_RAW:
            amount = float("inf")
        else:
            amount = from_raw(raw_amount, decimals)
        return ProposedTx(
            to=to, token=token, action=action, amount=amount,
            decoded=describe(action, to, amount, token), raw_calldata=raw_hex, context_ids=ctx,
        )

    if selector == TRANSFER_FROM_SELECTOR:
        src, dst = "0x" + word(0)[-40:], "0x" + word(1)[-40:]
        amount = from_raw(int(word(2), 16), decimals)
        return ProposedTx(
            to=dst, token=token, action="other", amount=amount,
            decoded=f"transferFrom {amount:g} {token} from {src} to {dst}",
            raw_calldata=raw_hex, context_ids=ctx,
        )

    return ProposedTx(
        to="0x" + "0" * 40, token=token, action="other", amount=0.0,
        decoded=f"unknown contract call (selector 0x{selector})",
        raw_calldata=raw_hex, context_ids=ctx,
    )
