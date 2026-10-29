"""Fallback backend: the same graph + queries in memory with NetworkX.
Used automatically when TG_HOST is not set, so nobody is blocked if TigerGraph is down.
"""
from __future__ import annotations

import networkx as nx

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
