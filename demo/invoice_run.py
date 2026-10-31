"""'Pay this month's vendor invoices': read every invoice PDF in the inbox, let a (fooled) agent
propose each payment, and run every proposal through Provenant. Pure Python, tested.

    run(request, protected=True, month="2026-10") -> {"mode", "message", "rows", "totals"}
    stream(request, protected, month)             -> yields live step events (for the UI), then the result

Per invoice, as live steps:
  READ     extract the PDF text like the agent's tools do (hidden lines included, flagged)
  MATCH    which vendor it claims to be from, checked against the owner's records
  PROPOSE  the (fooled) agent: pay the wallet written on the invoice, else the one on file
  POLICY   the owner's authority: "pay <vendor>", capped at that vendor's usual max, from records
  GRAPH    TigerGraph: hops to scam wallets, payment history, where the address came from
  VERIFY   all 6 firewall checks -> ALLOW / REPAIRED / ESCALATE / BLOCK
  SIGN     only verdict.final_tx, via the wallet (DRY_RUN=1 = signs nothing, no network)
  LEARN    a stopped attack is written back into TigerGraph
"""
from __future__ import annotations

import html
import re
import time
from pathlib import Path

from provenant import records

INBOX_ROOT = records.DATA_DIR / "inbox"
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")
BATCH_RE = re.compile(r"\b(invoices|bills|all|every|month|pending|vendors)\b", re.I)
STEPS = ["READ", "MATCH", "PROPOSE", "POLICY", "GRAPH", "VERIFY", "SIGN", "LEARN"]


def months() -> list[str]:
    return sorted((p.name for p in INBOX_ROOT.iterdir() if p.is_dir()), reverse=True) if INBOX_ROOT.exists() else []


def invoice_files(month: str) -> list[Path]:
    return sorted((INBOX_ROOT / month).glob("*.pdf"))


def short(a: str | None) -> str:
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 12 else (a or "")


def match_vendor(text: str) -> tuple[str, dict] | None:
    """The vendor an invoice claims to be from, if it's one the owner has on record."""
    low = text.lower()
    for name, info in sorted(records.vendors().items(), key=lambda kv: -len(kv[0])):
        names = {name, (info.get("business") or "").lower()} - {""}
        if any(re.search(rf"\b{re.escape(n)}\b", low) for n in names):
            return name, info
    return None


def read_invoice(path: Path | str, data: bytes | None = None) -> dict:
    from provenant.pdf_reader import read_pdf
    pdf = read_pdf(data if data is not None else Path(path))
    text = pdf.text
    claim = next((ln.strip() for ln in text.splitlines() if ln.strip()), "unknown sender")
    m = re.search(r"Balance due\s*\n?\s*([\d.,]+)\s*USDC", text, re.I) or re.search(r"([\d.,]+)\s*USDC", text)
    amount = float(m.group(1).replace(",", "")) if m else None
    addrs = ADDR_RE.findall(text)
    return {"file": Path(path).name, "text": text, "hidden": pdf.hidden, "claim": claim.title(),
            "amount": amount, "wallet_in_doc": addrs[-1].lower() if addrs else None, "chars": len(text)}


def highlight(text: str, hidden: list[str], registered: str | None) -> str:
    out = html.escape(text)
    for h in hidden:
        out = out.replace(html.escape(h), f'<span class="hid">{html.escape(h)}</span>')
    for a in set(ADDR_RE.findall(text)):
        cls = "ok" if registered and a.lower() == registered else "bad"
        out = out.replace(a, f'<mark class="{cls}">{a}</mark>')
    return out.replace("\n", "<br>")


class _Clock:
    def __init__(self):
        self.t = time.perf_counter()

    def lap(self) -> int:
        now = time.perf_counter()
        ms, self.t = int((now - self.t) * 1000), now
        return ms


def _ev(file, step, state, detail, ms=0, **extra):
    return {"type": "step", "file": file, "step": step, "state": state, "detail": detail, "ms": ms, **extra}


def process_steps(path, protected: bool, request: str, send: bool = True, data: bytes | None = None):
    """Generator: yields one event per step, then {"type": "row", "row": {...}}."""
    from provenant.firewall.firewall import check
    from provenant.firewall.policy import make_policy
    from provenant.graph import reputation
    from provenant.graph.learn import learn_from
    from provenant.graph.taint import trace
    from provenant.schemas import ContentItem, ProposedTx

    clk = _Clock()
    name_ = Path(path).name
    inv = read_invoice(path, data)
    f = inv["file"]
    yield _ev(f, "READ", "ok", f"{inv['chars']} chars extracted"
              + (f" · {len(inv['hidden'])} invisible line(s) found" if inv["hidden"] else " · no hidden text"),
              clk.lap(), warn=bool(inv["hidden"]))

    vendor = match_vendor(inv["text"])
    name = vendor[0].title() if vendor else inv["claim"]
    registered = vendor[1]["address"] if vendor else None
    yield _ev(f, "MATCH", "ok" if vendor else "bad",
              f"claims to be {name} · on record, registered wallet {short(registered)}" if vendor
              else f"claims to be {name} · NOT in your vendor records", clk.lap())

    to = inv["wallet_in_doc"] or registered
    amount = inv["amount"]
    row = {**inv, "vendor": name, "known_vendor": bool(vendor), "registered": registered, "proposed_to": to,
           "checks": [], "message": "", "learned": None, "tx_hash": None, "graph": {}, "graph_writes": 0,
           "text_html": highlight(inv["text"], inv["hidden"], registered)}
    if to is None or not amount:
        yield _ev(f, "PROPOSE", "skip", "no wallet or amount on the invoice", clk.lap())
        yield {"type": "row", "row": {**row, "decision": "NOTHING", "outcome": "skipped", "paid_to": None,
                                      "paid": 0.0, "why": "No wallet or amount on the invoice."}}
        return
    where = "wallet written on the invoice" if inv["wallet_in_doc"] else "wallet on file"
    yield _ev(f, "PROPOSE", "warn" if to != registered else "ok",
              f"agent proposes {amount:g} USDC → {short(to)} ({where})", clk.lap())

    if not protected:
        for s in ("POLICY", "GRAPH", "VERIFY"):
            yield _ev(f, s, "skip", "firewall off", 0)
        if not vendor:
            outcome, why = "risky", "Paid a vendor you've never used."
        elif to != registered:
            outcome, why = "stolen", "Paid the wallet written on the invoice, not the vendor's."
        elif amount > vendor[1].get("typical_max", amount):
            outcome, why = "risky", f"Paid {amount:g}, usually at most {vendor[1]['typical_max']:g}."
        else:
            outcome, why = "paid", "Matches your records."
        # Never send real money to an unchecked address, even on testnet: this is a simulation.
        yield _ev(f, "SIGN", "bad" if outcome != "paid" else "ok",
                  f"simulated: {amount:g} USDC → {short(to)} signed as proposed (nothing sent)", clk.lap())
        yield _ev(f, "LEARN", "skip", "firewall off, nothing learned", 0)
        yield {"type": "row", "row": {**row, "decision": "SIGNED", "outcome": outcome, "paid_to": to,
                                      "paid": amount, "why": why}}
        return

    policy = make_policy(f"pay {name}", "owner")
    yield _ev(f, "POLICY", "ok" if policy.allowed_recipients else "bad",
              f"owner authorises: pay {name} ≤ {policy.max_amount:g} USDC → {short(policy.allowed_recipients[0])}"
              if policy.allowed_recipients else f"no authority: {policy.intent_text}", clk.lap())

    contents = [ContentItem("owner_msg", "chat:owner", "trusted", request),
                ContentItem(f"inv_{Path(f).stem}", f"invoice:{f}", "untrusted", inv["text"])]
    hops = reputation.hops_to_flagged(to)
    known = reputation.payee_addresses(vendor[0]) if vendor else set()
    traces = trace(to, contents + records.records_as_content())
    origin = sorted({t.root.source for t in traces}) or ["nowhere"]
    src = reputation.graph_source()
    gdetail = (f"{'TigerGraph' if src == 'tigergraph' else 'local graph'} · "
               f"{'scam wallet' if hops == 0 else (f'{hops} hop(s) from a scam wallet' if hops else 'no scam links')} · "
               f"{'paid here before' if to in known else 'never paid here'} · address came from {', '.join(origin)}")
    row["graph"] = {"hops": hops, "paid_before": to in known, "origin": origin, "source": src}
    yield _ev(f, "GRAPH", "bad" if (hops is not None or (vendor and to not in known)) else "ok", gdetail,
              clk.lap(), source=src)

    v = check(ProposedTx(to=to, token="USDC", action="transfer", amount=amount,
                         decoded=f"transfer {amount:g} USDC to {to}"), policy, contents)
    d = v.decision.value
    failed = [c for c in v.checks if c.hard and not c.passed]
    passed = sum(c.passed for c in v.checks)
    yield _ev(f, "VERIFY", {"ALLOW": "ok", "REPAIRED": "fix", "ESCALATE": "warn", "BLOCK": "bad"}[d],
              f"{passed}/{len(v.checks)} checks passed → {d}"
              + (f" · {failed[0].name}: {failed[0].reason}" if failed else ""), clk.lap(), decision=d)

    paid_to = v.final_tx.to if v.final_tx is not None and d in ("ALLOW", "REPAIRED") else None
    tx_hash = None
    if paid_to:
        if send:
            try:
                from provenant.chain.wallet import dry_run, explorer_url, send as wallet_send
                tx_hash = wallet_send(v.final_tx)
                mode = "dry run, nothing broadcast" if dry_run() else f"Base Sepolia · {explorer_url(tx_hash)}"
                yield _ev(f, "SIGN", "ok", f"{amount:g} USDC → {short(paid_to)} · {mode}", clk.lap(), tx=tx_hash)
            except Exception as e:  # no keys, RPC down, no gas...
                yield _ev(f, "SIGN", "warn", f"approved, not sent ({type(e).__name__}: {str(e)[:80]})", clk.lap())
        else:
            yield _ev(f, "SIGN", "ok", f"approved: {amount:g} USDC → {short(paid_to)} (sending disabled)", clk.lap())
    else:
        yield _ev(f, "SIGN", "skip", "nothing signed", clk.lap())

    try:
        learned = learn_from(v, contents, request)
    except Exception:
        learned = None
    if learned and learned.get("new"):
        row["graph_writes"] = 5
        yield _ev(f, "LEARN", "fix", f"TigerGraph updated: wallet {short(learned['wallet'])} flagged · attack "
                  f"pattern {learned['id']} + vector saved · linked to {learned['source']}", clk.lap())
    elif learned:
        yield _ev(f, "LEARN", "ok", f"already known (first blocked {learned['ts']})", clk.lap())
    else:
        yield _ev(f, "LEARN", "skip", "nothing to learn", clk.lap())

    why = failed[0].reason if failed else "Matches your records."
    if d == "BLOCK" and not vendor:
        why = f"{name} isn't one of your vendors. Nothing paid. Add them to your records first if this is real."
    if d == "ESCALATE":
        why = (f"Amount {amount:g} USDC is above {name}'s usual {vendor[1]['typical_max']:g}. Please confirm."
               if vendor and amount > vendor[1].get("typical_max", amount) else v.message)
    outcome = {"ALLOW": "paid", "REPAIRED": "repaired", "ESCALATE": "held", "BLOCK": "blocked"}[d]
    yield {"type": "row", "row": {
        **row, "decision": d, "outcome": outcome, "paid_to": paid_to, "paid": amount if paid_to else 0.0,
        "why": why, "message": v.message, "learned": learned, "tx_hash": tx_hash,
        "checks": [{"name": c.name, "passed": c.passed, "hard": c.hard, "reason": c.reason} for c in v.checks]}}


def plan(request: str, month: str | None = None, extra: list[tuple[str, bytes]] = ()) -> dict:
    """Which invoices does this request mean? -> {"mode", "message", "files": [(path, data|None)]}"""
    month = month or (months()[0] if months() else "")
    files = [(p, None) for p in invoice_files(month)] + [(n, b) for n, b in extra]
    if not files:
        return {"mode": "none", "message": f"No invoices in {month}.", "files": [], "month": month}
    if BATCH_RE.search(request or ""):
        return {"mode": "batch", "message": f"Reading all {len(files)} invoices for {month}.", "files": files,
                "month": month}
    from provenant.resolve import resolve
    res = resolve(request or "")
    if not res.payee:
        return {"mode": "none", "files": [], "month": month,
                "message": "I couldn't tell which invoice you mean. Try \"pay this month's vendor invoices\"."}
    chosen = [(p, b) for p, b in files
              if (match_vendor(read_invoice(p, b)["text"]) or ("",))[0] == res.payee.lower()]
    msg = (f"You mean {res.payee} ({res.how}). Found {len(chosen)} invoice(s) from them." if chosen
           else f"You mean {res.payee}, but there's no invoice from them in {month}.")
    return {"mode": "single", "message": msg, "files": chosen, "month": month}


def totals(rows: list[dict]) -> dict:
    return {
        "invoices": len(rows),
        "paid_ok": sum(r["paid"] for r in rows if r["outcome"] in ("paid", "repaired")),
        "stolen": sum(r["paid"] for r in rows if r["outcome"] == "stolen"),
        "risky": sum(r["paid"] for r in rows if r["outcome"] == "risky"),
        "attacks_stopped": sum(1 for r in rows if r["outcome"] in ("repaired", "blocked")),
        "held": sum(1 for r in rows if r["outcome"] == "held"),
        "graph_writes": sum(r.get("graph_writes", 0) for r in rows),
    }


def stream(request: str, protected: bool = True, month: str | None = None, send: bool = True):
    """Live version for the UI: yields {"type": "plan"}, step events, {"type": "row"}..., then {"type": "done"}."""
    p = plan(request, month)
    yield {"type": "plan", **{k: v for k, v in p.items() if k != "files"}, "count": len(p["files"])}
    rows = []
    for path, data in p["files"]:
        for ev in process_steps(path, protected, request, send=send, data=data):
            if ev["type"] == "row":
                rows.append(ev["row"])
            yield ev
    yield {"type": "done", "rows": rows, "totals": totals(rows)}


def process(inv_path, protected: bool, request: str, send: bool = False) -> dict:
    return [e for e in process_steps(inv_path, protected, request, send=send) if e["type"] == "row"][0]["row"]


def run(request: str, protected: bool = True, month: str | None = None) -> dict:
    p = plan(request, month)
    rows = [process(path, protected, request) for path, _ in p["files"]]
    return {"mode": p["mode"], "message": p["message"], "rows": rows, "totals": totals(rows), "month": p["month"]}
