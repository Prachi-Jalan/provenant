"""Taint graph: follow the INFORMATION, not just the money. Owner: Person 3.

Every piece of content the agent read becomes part of a small graph:

    Source --POSTED--> Content --DERIVED_FROM--> Content      (forwarded / quoted / summarised)
                       Content --CONTAINS-----> Wallet        (an address written in it)

To decide whether an address can be trusted we trace it back: for every piece of content that
contains it, walk DERIVED_FROM to the very first item. Taint is sticky: an item is only as
trusted as EVERYTHING it came from. So when the owner forwards a scammer's email, the forward
comes from the owner (trusted), but the address in it still traces back to the scammer.

Backends: TigerGraph (installed query trace_origin, see taint_queries.gsql) when TG_HOST is
set, otherwise the same walk in Python. Both return the same rows, and one function decides.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from ..schemas import CheckResult, ContentItem, ProposedTx

ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")


def _short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 12 else str(a)


def session_id(contents: list[ContentItem]) -> str:
    """Same contents -> same id, so writing a session to TigerGraph twice is harmless."""
    h = hashlib.sha1()
    for c in contents:
        h.update(f"{c.id}|{c.source}|{c.trust}|{c.derived_from}|{c.text}\n".encode())
    return h.hexdigest()[:12]


@dataclass
class Trace:
    """Where one copy of the address came from: the item containing it, then its ancestors."""
    chain: list[ContentItem]          # [item containing the address, its parent, grandparent, ...]

    @property
    def holder(self) -> ContentItem:
        return self.chain[0]

    @property
    def root(self) -> ContentItem:
        return self.chain[-1]

    @property
    def trusted(self) -> bool:
        return all(c.trust == "trusted" for c in self.chain)

    @property
    def culprit(self) -> ContentItem | None:
        """The earliest untrusted item in the chain: where the taint entered."""
        bad = [c for c in self.chain if c.trust != "trusted"]
        return bad[-1] if bad else None


# ---------------------------------------------------------------- the walk (local)

def local_rows(address: str, contents: list[ContentItem]) -> list[dict]:
    """Rows in the same shape the TigerGraph query prints:
    {cid, depth, child}: depth 0 = contains the address; child = the item it was derived INTO."""
    addr = address.lower()
    by_id = {c.id: c for c in contents}
    rows, seen = [], set()
    frontier = [c for c in contents if addr in (c.text or "").lower()]
    for c in frontier:
        rows.append({"cid": c.id, "depth": 0, "child": ""})
        seen.add(c.id)
    depth = 0
    while frontier and depth < 10:
        depth += 1
        nxt = []
        for c in frontier:
            p = by_id.get(c.derived_from or "")
            if p is not None:
                rows.append({"cid": p.id, "depth": depth, "child": c.id})
                if p.id not in seen:
                    seen.add(p.id)
                    nxt.append(p)
        frontier = nxt
    return rows


def traces_from_rows(rows: list[dict], contents: list[ContentItem]) -> list[Trace]:
    by_id = {c.id: c for c in contents}
    parent_of = {r["child"]: r["cid"] for r in rows if r.get("child")}
    out = []
    for r in rows:
        if r["depth"] != 0 or r["cid"] not in by_id:
            continue
        chain, cur = [by_id[r["cid"]]], r["cid"]
        while cur in parent_of and parent_of[cur] in by_id and len(chain) < 12:
            cur = parent_of[cur]
            chain.append(by_id[cur])
        out.append(Trace(chain))
    return out


def trace(address: str, contents: list[ContentItem]) -> list[Trace]:
    """Every place this address appears, each traced back to where it first came from."""
    from .reputation import get_backend
    backend = get_backend()
    if hasattr(backend, "trace_rows"):
        rows = backend.trace_rows(address.lower(), contents)
    else:
        rows = local_rows(address, contents)
    return traces_from_rows(rows, contents)


def effective(contents: list[ContentItem]) -> list[ContentItem]:
    """Contents with taint applied: anything derived from untrusted content is untrusted too."""
    by_id = {c.id: c for c in contents}
    out = []
    for c in contents:
        cur, tainted, hops = c, c.trust != "trusted", 0
        while cur.derived_from and cur.derived_from in by_id and hops < 12:
            cur = by_id[cur.derived_from]
            tainted = tainted or cur.trust != "trusted"
            hops += 1
        out.append(replace(c, trust="untrusted") if tainted and c.trust == "trusted" else c)
    return out


# ---------------------------------------------------------------- the check

def provenance_check(tx: ProposedTx, contents: list[ContentItem]) -> CheckResult:
    """Trace the recipient back to where it first came from. Pass only if some copy of it
    comes, all the way back, from trusted sources."""
    name = "provenance"
    addr = (tx.to or "").lower()
    if not addr:
        return CheckResult(name, False, "Transaction has no recipient.")
    traces = trace(addr, contents)
    clean = [t for t in traces if t.trusted]
    if clean:
        srcs = ", ".join(sorted({t.holder.source for t in clean}))
        return CheckResult(name, True, f"Address comes from a trusted source ({srcs}).")
    laundered = [t for t in traces if t.holder.trust == "trusted"]
    if laundered:
        t = laundered[0]
        via = " → ".join(c.source for c in reversed(t.chain))
        return CheckResult(name, False,
                           f"Address {_short(addr)} was introduced by {t.culprit.source} (untrusted) and reached the "
                           f"agent via {t.holder.source} ({via}). Forwarding doesn't make it trusted.")
    if traces:
        srcs = ", ".join(sorted({t.holder.source for t in traces}))
        return CheckResult(name, False, f"Address {_short(addr)} appears only in untrusted content: {srcs}.")
    return CheckResult(name, False, f"Address {_short(addr)} has unknown origin (not in anything the agent read).")


def taint_picture(address: str, contents: list[ContentItem]) -> list[dict]:
    """For the dashboard: each trace as [{source, trust, id}] from origin to the holder."""
    return [[{"id": c.id, "source": c.source, "trust": c.trust} for c in reversed(t.chain)]
            for t in trace(address, contents)]
