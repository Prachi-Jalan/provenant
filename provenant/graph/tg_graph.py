"""TigerGraph backend. Same two methods as LocalGraph, answered by installed GSQL queries."""
from __future__ import annotations

import os


def connect():
    """Open a pyTigerGraph connection using .env values (see .env.example)."""
    import pyTigerGraph as tg

    conn = tg.TigerGraphConnection(
        host=os.environ["TG_HOST"],                 # e.g. https://<id>.i.tgcloud.io
        graphname=os.environ.get("TG_GRAPH", "provenant"),
        gsqlSecret=os.environ["TG_SECRET"],         # created in Savanna: workspace -> Connect from API
    )
    try:
        conn.getToken(os.environ["TG_SECRET"])
    except Exception as e:
        print("getToken skipped:", e)
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
