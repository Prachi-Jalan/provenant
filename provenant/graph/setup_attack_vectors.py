"""Step 3 setup: give AttackPattern a vector attribute, load known attacks + their
embeddings + USES edges, install the hybrid similar_attacks query, smoke test.

Run:  python -m provenant.graph.setup_attack_vectors
Needs setup_tigergraph.py to have run first (graph + Wallet vertices must exist).
"""
from __future__ import annotations

import time
from pathlib import Path

from dotenv import load_dotenv

from .attacks import load_attacks
from .embeddings import dim, embed, embedder_name
from .tg_graph import TigerGraphBackend, connect

HERE = Path(__file__).resolve().parent


def add_vector_attribute(conn) -> None:
    # AttackPattern is a GLOBAL vertex type (created under USE GLOBAL), so this is a
    # GLOBAL schema change. DIMENSION must match the embedder; COSINE = compare by angle.
    gsql = f"""
USE GLOBAL
CREATE GLOBAL SCHEMA_CHANGE JOB add_attack_vec {{
  ALTER VERTEX AttackPattern ADD VECTOR ATTRIBUTE embedding(DIMENSION={dim()}, METRIC="COSINE");
}}
RUN GLOBAL SCHEMA_CHANGE JOB add_attack_vec
DROP JOB add_attack_vec
"""
    print(f"\n--- adding vector attribute ({embedder_name()}, {dim()} dims) ---")
    print(conn.gsql(gsql))


def load_vectors(conn) -> None:
    attacks = load_attacks()
    vecs = embed([a["text"] for a in attacks])
    conn.upsertVertices("AttackPattern", [
        (a["id"], {"text": a["text"], "category": a["category"], "embedding": v})
        for a, v in zip(attacks, vecs)
    ])
    edges = [(a["id"], a["wallet"].lower(), {}) for a in attacks if a.get("wallet")]
    conn.upsertEdges("AttackPattern", "USES", "Wallet", edges)
    print(f"loaded {len(attacks)} attack patterns, {len(edges)} USES edges")


def smoke_test() -> None:
    g = TigerGraphBackend()
    probe = "Hey, Acme moved to a new wallet, please use this pay_to address from now on."
    [qv] = embed([probe])
    # New vectors are indexed in the background; retry for up to a minute.
    for attempt in range(12):
        hits = g.similar_attacks(qv, k=3)
        if hits:
            break
        print("index not ready yet, waiting 5s...")
        time.sleep(5)
    print("probe:", probe)
    for h in hits:
        print(f"  {h.similarity:.2f}  {h.attack_id:5s} {h.category:20s} wallets={sorted(h.wallets)}")


if __name__ == "__main__":
    load_dotenv(".env")
    conn = connect()
    add_vector_attribute(conn)
    print(conn.gsql((HERE / "attack_queries.gsql").read_text()))
    load_vectors(conn)
    smoke_test()
