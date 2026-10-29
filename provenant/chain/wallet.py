"""Signing + sending on Base Sepolia. Owner: Person 1.
Only the firewall's approved Verdict.final_tx ever reaches send().
"""
from ..schemas import ProposedTx

EXPLORER = "https://sepolia.basescan.org/tx/"


def send(tx: ProposedTx) -> str:
    """Sign + broadcast an ERC-20 transfer/approve from the OWNER wallet.
    Returns the tx hash ("0x..."). Respect DRY_RUN=1 -> return "0xDRYRUN"."""
    raise NotImplementedError("Person 1")


def send_usdc(to: str, amount: float) -> str:
    """First milestone: send test USDC from Python."""
    raise NotImplementedError("Person 1")
