"""Reputation check backed by TigerGraph. Owner: Person 3.

Schema + queries: schema.gsql, queries.gsql. Setup: setup_tigergraph.py.
Backend: TigerGraph when TG_HOST is set in .env, else the local NetworkX copy.
"""
import os

from ..schemas import CheckResult, ContentItem, Policy, ProposedTx
from .attacks import AttackMatch
from .embeddings import embed, threshold

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


def payee_addresses(payee: str) -> set[str]:
    return get_backend().payee_addresses(payee)


def known_vendor_address(vendor: str) -> str | None:
    addrs = payee_addresses(vendor)
    return next(iter(addrs)) if len(addrs) == 1 else None


def similar_attacks(text: str, k: int = 3) -> list[AttackMatch]:
    """Closest known attacks to `text`: [(attack_id, similarity, category, wallets)]."""
    [vec] = embed([text])
    return get_backend().similar_attacks(vec, k)


def attack_pattern_check(tx: ProposedTx, contents: list[ContentItem]) -> CheckResult:
    """Does anything the agent read from an UNTRUSTED source look like a known attack?

    - looks like a known attack                      -> soft fail (raises risk, can't block alone:
                                                        an honest message can sound similar)
    - looks like a known attack AND that attack used
      the very wallet the agent now wants to pay     -> hard fail (meaning + graph agree)
    """
    untrusted = [c for c in contents if c.trust == "untrusted"]
    if not untrusted:
        return CheckResult("attack_pattern", True, "no untrusted content read", score=0.0, hard=False)
    try:
        best: tuple[ContentItem, AttackMatch] | None = None
        for c in untrusted:
            for m in similar_attacks(c.text, k=3):
                if best is None or m.similarity > best[1].similarity:
                    best = (c, m)
                if m.similarity >= threshold() and tx.to.lower() in m.wallets:
                    return CheckResult(
                        "attack_pattern", False,
                        f"{c.source} matches known {m.category} attack {m.attack_id} "
                        f"({m.similarity:.2f}) that used this same wallet {tx.to}",
                        score=m.similarity, hard=True)
    except Exception as e:
        return CheckResult("attack_pattern", True,
                           f"attack search unavailable ({e.__class__.__name__})", hard=False)

    c, m = best
    if m.similarity >= threshold():
        return CheckResult("attack_pattern", False,
                           f"{c.source} resembles known {m.category} attack {m.attack_id} ({m.similarity:.2f})",
                           score=m.similarity, hard=False)
    return CheckResult("attack_pattern", True,
                       f"no known attack pattern (closest {m.similarity:.2f})", score=m.similarity, hard=False)


def reputation_check(tx: ProposedTx, policy: Policy) -> CheckResult:
    """Ask the graph two questions about the address the agent wants to pay.

    1. Is it a known scam wallet, or connected to one within 2 hops?
    2. If we're paying a known payee (Rahul, Acme), is this the address we've
       always paid them at?
    Either red flag -> fail (hard). If the graph itself is down, return a soft
    result so the firewall keeps working on its other checks.
    """
    addr = tx.to.lower()
    try:
        hops = hops_to_flagged(addr)
        known = payee_addresses(policy.payee_label) if policy.payee_label else set()
    except Exception as e:  # TigerGraph unreachable, query error, ...
        return CheckResult("reputation", True, f"reputation unavailable ({e.__class__.__name__})",
                           hard=False)

    if hops == 0:
        return CheckResult("reputation", False, f"{addr} is a flagged scam wallet")
    if hops is not None:
        return CheckResult("reputation", False,
                           f"{addr} is {hops} hop{'s' if hops > 1 else ''} from a flagged scam wallet")
    if known and addr not in known:
        usual = ", ".join(sorted(known))
        return CheckResult("reputation", False,
                           f"new address for {policy.payee_label}: always paid at {usual}")
    if known:
        return CheckResult("reputation", True, f"usual address for {policy.payee_label}")
    return CheckResult("reputation", True, "no links to flagged wallets")
