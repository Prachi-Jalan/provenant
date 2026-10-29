"""Reputation check backed by TigerGraph. Owner: Person 3.

TigerGraph schema:
  VERTEX Wallet(PRIMARY_ID address STRING, flagged BOOL)
  VERTEX Vendor(PRIMARY_ID name STRING)
  VERTEX Source(PRIMARY_ID sender STRING, trust STRING)
  VERTEX AttackPattern(PRIMARY_ID id STRING, text STRING, embedding LIST<FLOAT>)
  UNDIRECTED EDGE SENT_TO(FROM Wallet, TO Wallet)
  DIRECTED EDGE PAID_AT(FROM Vendor, TO Wallet, count INT)
  DIRECTED EDGE MENTIONS(FROM Source, TO Wallet)
  DIRECTED EDGE USES(FROM AttackPattern, TO Wallet)
Seed data: data/flagged.json, data/vendors.json, data/contacts.json
Fallback if TigerGraph isn't up by 15:00: NetworkX over the same seed files.
"""
import os

from ..schemas import CheckResult, Policy, ProposedTx

_backend = None


def get_backend():
    """TigerGraph if TG_HOST is set in .env, otherwise the local NetworkX fallback."""
    global _backend
    if _backend is None:
        if os.getenv("TG_HOST"):
            from .tg_graph import TigerGraphBackend
            _backend = TigerGraphBackend()
        else:
            from .local_graph import LocalGraph
            _backend = LocalGraph()
    return _backend


def hops_to_flagged(address: str, max_hops: int = 2) -> int | None:
    return get_backend().hops_to_flagged(address, max_hops)


def known_vendor_address(vendor: str) -> str | None:
    addrs = get_backend().payee_addresses(vendor)
    return next(iter(addrs)) if len(addrs) == 1 else None


def similar_attacks(text: str, k: int = 3) -> list[tuple[str, float]]:
    """Vector search over AttackPattern embeddings -> [(attack_id, similarity)]."""
    raise NotImplementedError("Person 3")


def reputation_check(tx: ProposedTx, policy: Policy) -> CheckResult:
    """name="reputation", hard=True.
    STUB: always passes until Person 3 replaces it, so Person 2 isn't blocked."""
    return CheckResult("reputation", True, "stub: reputation not wired yet")
