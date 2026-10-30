"""TigerGraph backend. Same two methods as LocalGraph, answered by installed GSQL queries."""
from __future__ import annotations

import os

from .attacks import AttackMatch


def connect():
    """Open a pyTigerGraph connection using .env values (see .env.example)."""
    import pyTigerGraph as tg

    conn = tg.TigerGraphConnection(
        host=os.environ["TG_HOST"],                 # https://<workspace-id>.i.tgcloud.io (port 443 auto)
        graphname=os.environ.get("TG_GRAPH", "provenant"),
        gsqlSecret=os.environ["TG_SECRET"],         # Savanna: Database Secrets -> Create Secret
    )
    try:
        conn.getToken(os.environ["TG_SECRET"])      # older setups need an explicit REST++ token
    except Exception as e:                          # newer pyTigerGraph handles it from gsqlSecret,
        print("getToken skipped:", e)               # and it fails before the graph exists
    return conn


class TigerGraphBackend:
    def __init__(self):
        self.conn = connect()

    def hops_to_flagged(self, address: str, max_hops: int = 2) -> int | None:
        res = self.conn.runInstalledQuery("hops_to_flagged",
                                          params={"addr": address.lower(), "max_hops": max_hops})
        hops = res[0]["hops"]
        return None if hops < 0 else hops

    def payee_addresses(self, payee: str) -> set[str]:
        res = self.conn.runInstalledQuery("payee_addresses", params={"payee": payee.lower()})
        return set(res[0]["addresses"])

    def similar_attacks(self, query_vec: list[float], k: int = 3) -> list[AttackMatch]:
        """One TigerGraph query: vector search + walk USES edges to the wallets (hybrid)."""
        res = self.conn.runInstalledQuery("similar_attacks", params={"qv": query_vec, "k": k},
                                          usePost=True)
        wallets = _find(res, "wallets") or {}
        matches = []
        for row in _find(res, "matches"):
            # TigerGraph's COSINE metric returns a distance (0 = same); we want similarity.
            matches.append(AttackMatch(row["attack_id"], round(1 - float(row["distance"]), 3),
                                       row["category"], frozenset(wallets.get(row["attack_id"], []))))
        return sorted(matches, key=lambda m: m.similarity, reverse=True)


def _find(res: list[dict], key: str) -> list:
    """Installed-query output is a list of {name: value} blocks; grab the one we printed."""
    for block in res:
        if key in block:
            return block[key]
    return []
