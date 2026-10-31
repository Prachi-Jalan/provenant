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
        self._written: set[str] = set()   # taint sessions already written

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


    def neighbourhood(self, address: str, depth: int = 2) -> dict:
        """Wallets within `depth` SENT_TO hops, from the installed wallet_neighbourhood query."""
        res = self.conn.runInstalledQuery("wallet_neighbourhood",
                                          params={"addr": address.lower(), "depth": depth})
        nodes = {r["address"]: bool(r["flagged"]) for r in _find(res, "wallets")}
        edges = [(e["a"], e["b"]) for e in _find(res, "links")]
        return {"nodes": nodes or {address.lower(): False}, "edges": edges}

    # ------------------------------------------------------------ taint graph (#1)
    def write_session(self, contents) -> str:
        """Write what the agent read as Source -POSTED-> Content -DERIVED_FROM-> Content -CONTAINS-> Wallet."""
        from .taint import ADDR_RE, session_id
        sess = session_id(contents)
        if sess in self._written:
            return sess
        ids = {c.id for c in contents}
        cid = lambda i: f"{sess}:{i}"
        self.conn.upsertVertices("Content", [
            (cid(c.id), {"local_id": c.id, "sess": sess, "src": c.source, "trust": c.trust,
                         "body": (c.text or "")[:2000]}) for c in contents])
        self.conn.upsertVertices("Source", [(c.source, {"trust": c.trust}) for c in contents])
        self.conn.upsertEdges("Source", "POSTED", "Content", [(c.source, cid(c.id), {}) for c in contents])
        derived = [(cid(c.id), cid(c.derived_from), {}) for c in contents if c.derived_from in ids]
        if derived:
            self.conn.upsertEdges("Content", "DERIVED_FROM", "Content", derived)
        contains = [(cid(c.id), a.lower(), {}) for c in contents for a in set(ADDR_RE.findall(c.text or ""))]
        if contains:
            self.conn.upsertEdges("Content", "CONTAINS", "Wallet", contains)
        self._written.add(sess)
        return sess

    def trace_rows(self, address: str, contents) -> list[dict]:
        sess = self.write_session(contents)
        res = self.conn.runInstalledQuery("trace_origin", params={"addr": address.lower(), "sess": sess})
        return [{"cid": r["cid"], "depth": int(r["depth"]), "child": r["child"]} for r in _find(res, "rows")]

    # ------------------------------------------------------------ attack memory (#2)
    def learn(self, rec: dict) -> None:
        from .embeddings import embed
        [vec] = embed([rec["text"]])
        self.conn.upsertVertices("Wallet", [(rec["wallet"], {"flagged": True, "label": f"learned: {rec['source']}"})])
        self.conn.upsertVertices("AttackPattern", [(rec["id"], {"text": rec["text"], "category": rec["category"],
                                                                "embedding": vec})])
        self.conn.upsertEdges("AttackPattern", "USES", "Wallet", [(rec["id"], rec["wallet"], {})])
        self.conn.upsertVertices("Source", [(rec["source"], {"trust": "untrusted"})])
        self.conn.upsertEdges("Source", "MENTIONS", "Wallet", [(rec["source"], rec["wallet"], {})])

    def forget(self, recs: list[dict]) -> None:
        for r in recs:
            self.conn.delVerticesById("AttackPattern", r["id"])
            self.conn.upsertVertices("Wallet", [(r["wallet"], {"flagged": bool(r.get("was_flagged"))})])


def _find(res: list[dict], key: str) -> list:
    """Installed-query output is a list of {name: value} blocks; grab the one we printed."""
    for block in res:
        if key in block:
            return block[key]
    return []
