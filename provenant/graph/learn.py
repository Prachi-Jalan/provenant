"""Attack memory: every attack Provenant stops is written back into the graph. Owner: Person 3.

After a BLOCK / REPAIRED caused by an address from untrusted content:
    Wallet(attacker)  -> flagged = True                     (reputation check now fails on it)
    AttackPattern     -> the scam text + its embedding      (attack-pattern vector search finds it)
    USES              AttackPattern -> Wallet
    Source            who sent it, MENTIONS -> Wallet
So the next attack with the same wallet, or the same wording with a new wallet, is recognised.
One agent's blocked attack protects every agent that shares the graph.

Safety rails: never flag an address that is in the owner's records, only learn when the
provenance check failed (the address really came from untrusted content), and never learn
from a payee's own "my wallet changed" message (that ESCALATEs, it isn't an attack verdict).
A local copy is kept in logs/learned_attacks.jsonl so it survives restarts and TigerGraph naps.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import networkx as nx

from .. import records
from ..schemas import ContentItem, Decision, Verdict

ROOT = Path(__file__).resolve().parents[2]
LEARNED_FILE = ROOT / "logs" / "learned_attacks.jsonl"
CATEGORY = "previously blocked"


def load() -> list[dict]:
    if not LEARNED_FILE.exists():
        return []
    return [json.loads(line) for line in LEARNED_FILE.read_text().splitlines() if line.strip()]


def lookup(address: str) -> dict | None:
    addr = (address or "").lower()
    return next((r for r in load() if r["wallet"] == addr), None)


def _known_addresses() -> set[str]:
    known = {a.lower() for a in records.contacts().values()}
    known |= {v["address"].lower() for v in records.vendors().values()}
    return known


def learn_from(verdict: Verdict, contents: list[ContentItem], request: str = "") -> dict | None:
    """Write this attack into the graph. Returns the record ({"new": bool, ...}) or None."""
    if verdict is None or verdict.decision not in (Decision.BLOCK, Decision.REPAIRED):
        return None
    pol = next((c for c in verdict.checks if c.name == "policy"), None)
    if pol is not None and "not in trusted contacts or vendors" in pol.reason:
        return None  # an unknown payee isn't proof of an attack; flagging them would poison the graph
    prov = next((c for c in verdict.checks if c.name == "provenance"), None)
    if prov is None or prov.passed:
        return None
    wallet = (verdict.original.to or "").lower()
    if not wallet or wallet in _known_addresses():
        return None

    from .taint import trace
    culprits = [t.culprit for t in trace(wallet, contents) if t.culprit is not None]
    if not culprits:
        return None
    src = culprits[0]
    text = (src.text or "")[:1500]
    rid = "learned_" + hashlib.sha1(f"{wallet}|{text}".encode()).hexdigest()[:10]

    for r in load():
        if r["id"] == rid:
            return {**r, "new": False}

    from .reputation import get_backend, hops_to_flagged
    rec = {"id": rid, "wallet": wallet, "text": text, "source": src.source, "category": CATEGORY,
           "request": request, "ts": time.strftime("%H:%M:%S"), "date": time.strftime("%Y-%m-%d"),
           "was_flagged": hops_to_flagged(wallet, 0) == 0}
    LEARNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LEARNED_FILE.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    get_backend().learn(rec)
    return {**rec, "new": True}


def forget_all() -> int:
    """Demo reset: unflag learned wallets and delete learned attacks (local + TigerGraph)."""
    recs = load()
    if recs:
        from . import reputation
        backend = reputation.get_backend()
        if hasattr(backend, "forget"):
            backend.forget(recs)
    if LEARNED_FILE.exists():
        LEARNED_FILE.unlink()
    from . import reputation
    reputation._backend = None  # rebuild the local copy without them
    return len(recs)


def campaigns() -> list[dict]:
    """Group learned attacks that share a wallet or a sender into one campaign
    (connected components of attack - wallet - sender)."""
    g = nx.Graph()
    for r in load():
        a, w, s = f"a:{r['id']}", f"w:{r['wallet']}", f"s:{r['source']}"
        g.add_edge(a, w)
        g.add_edge(a, s)
    out = []
    for comp in nx.connected_components(g):
        out.append({"attacks": sorted(n[2:] for n in comp if n.startswith("a:")),
                    "wallets": sorted(n[2:] for n in comp if n.startswith("w:")),
                    "sources": sorted(n[2:] for n in comp if n.startswith("s:"))})
    return sorted(out, key=lambda c: -len(c["attacks"]))
