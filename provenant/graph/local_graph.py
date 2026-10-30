"""Fallback backend: the same graph + queries in memory with NetworkX.
Used automatically when TG_HOST is not set, so nobody is blocked if TigerGraph is down.
"""
from __future__ import annotations

import networkx as nx

from .attacks import AttackMatch, load_attacks
from .embeddings import cosine, embed
from .seed import build


class LocalGraph:
    def __init__(self):
        data = build()
        self.g = nx.Graph()
        for addr, attrs in data.wallets.items():
            self.g.add_node(addr, **attrs)
        self.g.add_edges_from(data.sent_to)
        self.paid_at: dict[str, set[str]] = {}
        for payee, addr, _times in data.paid_at:
            self.paid_at.setdefault(payee.lower(), set()).add(addr)

    def hops_to_flagged(self, address: str, max_hops: int = 2) -> int | None:
        addr = address.lower()
        if addr not in self.g:
            return None
        # BFS outward from addr up to max_hops; keep the closest flagged wallet
        dists = nx.single_source_shortest_path_length(self.g, addr, cutoff=max_hops)
        flagged = [d for n, d in dists.items() if self.g.nodes[n].get("flagged")]
        return min(flagged) if flagged else None

    def payee_addresses(self, payee: str) -> set[str]:
        return self.paid_at.get(payee.lower(), set())

    def similar_attacks(self, query_vec: list[float], k: int = 3) -> list[AttackMatch]:
        """Brute-force cosine search over every known attack (fine for ~50 items)."""
        if not hasattr(self, "_attacks"):
            self._attacks = load_attacks()
            self._vecs = embed([a["text"] for a in self._attacks])
        scored = sorted(zip((cosine(query_vec, v) for v in self._vecs), self._attacks),
                        key=lambda p: p[0], reverse=True)[:k]
        return [AttackMatch(a["id"], round(s, 3), a["category"],
                            frozenset([a["wallet"]] if a.get("wallet") else []))
                for s, a in scored]
