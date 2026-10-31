"""One-time TigerGraph setup for the taint graph (#1) and attack memory (#2), plus the
dashboard's wallet_neighbourhood query.

Run (workspace must be RUNNING in Savanna):
    python -m provenant.graph.setup_taint_and_memory

Adds to the existing graph:
    Content       one thing the agent read (local_id, sess, src, trust, body)
    POSTED        Source  -> Content        who sent it
    DERIVED_FROM  Content -> Content        forwarded / quoted / summarised from
    CONTAINS      Content -> Wallet         an address written in it
Re-run safe: "already exists" messages are fine.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from .tg_graph import connect

HERE = Path(__file__).resolve().parent

# Plan A: create global types (like schema.gsql did), then add them to the graph.
PLAN_A = """
USE GLOBAL
CREATE VERTEX Content (PRIMARY_ID cid STRING, local_id STRING, sess STRING, src STRING, trust STRING, body STRING) WITH primary_id_as_attribute="true"
CREATE DIRECTED EDGE POSTED (FROM Source, TO Content)
CREATE DIRECTED EDGE DERIVED_FROM (FROM Content, TO Content)
CREATE DIRECTED EDGE CONTAINS (FROM Content, TO Wallet)
CREATE GLOBAL SCHEMA_CHANGE JOB add_taint_to_graph {
  ADD VERTEX Content TO GRAPH provenant;
  ADD EDGE POSTED, DERIVED_FROM, CONTAINS TO GRAPH provenant;
}
RUN GLOBAL SCHEMA_CHANGE JOB add_taint_to_graph
DROP JOB add_taint_to_graph
"""

# Plan B (only if A didn't work): define the types locally inside graph provenant.
PLAN_B = """
USE GRAPH provenant
CREATE SCHEMA_CHANGE JOB add_taint_local FOR GRAPH provenant {
  ADD VERTEX Content (PRIMARY_ID cid STRING, local_id STRING, sess STRING, src STRING, trust STRING, body STRING) WITH primary_id_as_attribute="true";
  ADD DIRECTED EDGE POSTED (FROM Source, TO Content);
  ADD DIRECTED EDGE DERIVED_FROM (FROM Content, TO Content);
  ADD DIRECTED EDGE CONTAINS (FROM Content, TO Wallet);
}
RUN SCHEMA_CHANGE JOB add_taint_local
DROP JOB add_taint_local
"""

NEEDED = {"POSTED", "DERIVED_FROM", "CONTAINS"}


def _ready(conn) -> bool:
    try:
        return "Content" in conn.getVertexTypes(force=True) and NEEDED <= set(conn.getEdgeTypes(force=True))
    except TypeError:  # older pyTigerGraph has no force=
        return "Content" in conn.getVertexTypes() and NEEDED <= set(conn.getEdgeTypes())


def main() -> None:
    load_dotenv(".env")
    conn = connect()
    print("--- 1/3 new vertex + edge types ---")
    if _ready(conn):
        print("already there, skipping")
    else:
        print(conn.gsql(PLAN_A))
        if not _ready(conn):
            print("plan A didn't add them, trying plan B ...")
            print(conn.gsql(PLAN_B))
    if not _ready(conn):
        print("\nCould not create the Content types. Paste the messages above to Claude.")
        print("The demo still works: Provenant traces taint in Python when TigerGraph can't.")
        return
    print("types OK: Content, POSTED, DERIVED_FROM, CONTAINS")
    print("--- 2/3 install trace_origin (taint) ---")
    print(conn.gsql((HERE / "taint_queries.gsql").read_text()))
    print("--- 3/3 install wallet_neighbourhood (dashboard picture) ---")
    print(conn.gsql((HERE / "viz_queries.gsql").read_text()))

    # smoke test: owner forwards a scam email; the address must trace back to the scammer
    from ..schemas import ContentItem
    from .tg_graph import TigerGraphBackend
    from .taint import traces_from_rows
    bad = "0xe0110000000000000000000000000000000000e1"
    items = [ContentItem("orig", "email:accounts@acme-supplies-billing.com", "untrusted", f"new wallet {bad}"),
             ContentItem("fwd", "telegram:owner", "trusted", f"Fwd: new wallet {bad}", derived_from="orig")]
    try:
        rows = TigerGraphBackend().trace_rows(bad, items)
    except Exception as e:
        print("smoke test failed:", e)
        return
    for t in traces_from_rows(rows, items):
        print("trace:", " -> ".join(f"{c.source} ({c.trust})" for c in reversed(t.chain)),
              "| trusted all the way back:", t.trusted)
    print("\nDone. If the trace line shows the email source, TigerGraph taint tracking works.")


if __name__ == "__main__":
    main()
