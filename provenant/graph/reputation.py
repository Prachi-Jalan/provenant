"""Reputation check backed by TigerGraph. Owner: Person 3.

Schema + queries: schema.gsql, queries.gsql. Setup: setup_tigergraph.py.
Backend: TigerGraph when TG_HOST is set in .env, else the local NetworkX copy.
If TigerGraph is set but doesn't answer (e.g. the workspace is asleep), each call falls back
to the local copy of the same graph, so the checks never silently disappear mid-demo.
"""
import os

from ..schemas import CheckResult, ContentItem, Policy, ProposedTx
from .attacks import AttackMatch
from .embeddings import embed, threshold

_backend = None


def _short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if addr and len(addr) > 12 else str(addr)


class FallbackBackend:
    """Try TigerGraph first; on any error answer from the local copy and remember that we did."""

    def __init__(self):
        from .local_graph import LocalGraph
        self.local = LocalGraph()
        self.tg = None
        self.last_source = "local"
        try:
            from .tg_graph import TigerGraphBackend
            self.tg = TigerGraphBackend()
        except Exception:
            self.tg = None

    def _call(self, name, *args):
        if self.tg is not None:
            try:
                out = getattr(self.tg, name)(*args)
                self.last_source = "tigergraph"
                return out
            except Exception:
                pass
        self.last_source = "local"
        return getattr(self.local, name)(*args)

    def hops_to_flagged(self, address, max_hops=2):
        return self._call("hops_to_flagged", address, max_hops)

    def payee_addresses(self, payee):
        return self._call("payee_addresses", payee)

    def similar_attacks(self, vec, k=3):
        return self._call("similar_attacks", vec, k)

    def neighbourhood(self, address, depth=2):
        return self._call("neighbourhood", address, depth)

    def trace_rows(self, address, contents):
        return self._call("trace_rows", address, contents)

    def learn(self, rec):
        """Remember in BOTH: the local copy always, TigerGraph if it's awake."""
        self.local.learn(rec)
        if self.tg is not None:
            try:
                self.tg.learn(rec)
                self.last_source = "tigergraph"
            except Exception:
                pass

    def forget(self, recs):
        if self.tg is not None:
            try:
                self.tg.forget(recs)
            except Exception:
                pass


def get_backend():
    """TigerGraph (with local fallback) if TG_HOST is set in .env, otherwise the local copy."""
    global _backend
    if _backend is None:
        if os.getenv("TG_HOST"):
            _backend = FallbackBackend()
        else:
            from .local_graph import LocalGraph
            _backend = LocalGraph()
    return _backend


def graph_source() -> str:
    """"tigergraph" or "local": which graph answered the most recent question."""
    return getattr(get_backend(), "last_source", "local")


def neighbourhood(address: str, depth: int = 2) -> dict:
    return get_backend().neighbourhood(address, depth)


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
    from .taint import effective  # a forwarded scam is still a scam: check it too
    untrusted = [c for c in effective(contents) if c.trust == "untrusted"]
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
                        f"({m.similarity:.2f}) that used this same wallet {_short(tx.to)}.",
                        score=m.similarity, hard=True)
    except Exception as e:
        return CheckResult("attack_pattern", True,
                           f"attack search unavailable ({e.__class__.__name__})", hard=False)

    if best is None:  # attack index empty or returned nothing for every message
        return CheckResult("attack_pattern", True, "no known attack patterns to compare against",
                           score=0.0, hard=False)
    c, m = best
    if m.similarity >= threshold():
        return CheckResult("attack_pattern", False,
                           f"{c.source} resembles known {m.category} attack {m.attack_id} ({m.similarity:.2f}).",
                           score=m.similarity, hard=False)
    return CheckResult("attack_pattern", True,
                       f"No known attack pattern (closest {m.similarity:.2f}).", score=m.similarity, hard=False)


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
        from .learn import lookup
        seen = lookup(addr)
        if seen:
            return CheckResult("reputation", False,
                               f"Seen before: {addr[:6]}…{addr[-4:]} was used in an attack Provenant blocked "
                               f"at {seen['ts']} ({seen['source']}).")
        return CheckResult("reputation", False, f"{_short(addr)} is a flagged scam wallet.")
    if hops is not None:
        return CheckResult("reputation", False,
                           f"{_short(addr)} is {hops} hop{'s' if hops > 1 else ''} from a flagged scam wallet.")
    if known and addr not in known:
        usual = ", ".join(_short(a) for a in sorted(known))
        return CheckResult("reputation", False,
                           f"New address for {policy.payee_label}: always paid at {usual}.")
    if known:
        return CheckResult("reputation", True, f"Usual address for {policy.payee_label}.")
    return CheckResult("reputation", True, "No links to flagged wallets.")
