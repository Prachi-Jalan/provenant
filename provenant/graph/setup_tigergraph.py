"""One-time TigerGraph setup: create schema -> install queries -> load seed data -> smoke test.

Run:  python -m provenant.graph.setup_tigergraph
Re-run safe: schema/query steps just report "already exists"; upserts overwrite.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from .seed import build
from .tg_graph import TigerGraphBackend, connect

HERE = Path(__file__).resolve().parent


def run_gsql(conn, file: str) -> None:
    print(f"\n--- running {file} ---")
    print(conn.gsql((HERE / file).read_text()))


def load_data(conn) -> None:
    data = build()
    # upsert = insert, or update if it already exists
    conn.upsertVertices("Wallet", [(a, {"flagged": v["flagged"], "label": v["label"]})
                                   for a, v in data.wallets.items()])
    conn.upsertVertices("Payee", [(name, {"kind": kind}) for name, kind in data.payees.items()])
    conn.upsertEdges("Wallet", "SENT_TO", "Wallet", [(a, b, {}) for a, b in data.sent_to])
    conn.upsertEdges("Payee", "PAID_AT", "Wallet",
                     [(p, a, {"times": t}) for p, a, t in data.paid_at])
    print(f"loaded {len(data.wallets)} wallets, {len(data.sent_to)} transfers, "
          f"{len(data.payees)} payees")


def smoke_test() -> None:
    g = TigerGraphBackend()
    mule = "0xe0110000000000000000000000000000000000e1"
    print("mule hops_to_flagged:", g.hops_to_flagged(mule))   # expect 1
    print("acme paid at:", g.payee_addresses("acme"))          # expect {0xacce...01}


if __name__ == "__main__":
    load_dotenv()
    conn = connect()
    print("connected:", conn.echo())
    run_gsql(conn, "schema.gsql")
    run_gsql(conn, "queries.gsql")   # installing can take a minute or two
    load_data(conn)
    smoke_test()
