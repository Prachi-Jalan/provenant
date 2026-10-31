"""Turn a vague owner request ("pay the guy who catered for my birthday") into a named payee.

Rules (so parsing never grants authority):
- Only the OWNER'S OWN records are searched: contacts, vendors, payment history, calendar
  (data/history.json). Never the inbox, never anything the agent read. An attacker can't
  plant "I'm the caterer" and become the payee.
- Deterministic keyword matching, no LLM, so the same words always resolve the same way.
- If two payees tie, nothing is resolved: the owner is asked which one they meant.
- The output is a plain, explicit request ("Pay Tanishk 45 USDC") that goes through the
  normal make_policy. The address still comes only from records.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import records

STOP = {"pay", "the", "guy", "girl", "who", "for", "my", "me", "a", "an", "to", "of", "that", "person",
        "please", "send", "money", "him", "her", "them", "our", "did", "was", "and", "from", "at", "on",
        "in", "his", "lady", "man", "people", "usdc", "settle", "transfer", "give", "with", "last", "this"}
# words that mean the same thing for matching (compared on their first 5 letters)
SYNONYMS = {"birth": {"party", "bday"}, "bday": {"birth", "party"}, "cater": {"food", "lunch"},
            "food": {"cater"}, "cook": {"cater", "food"}}


def _stems(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return {w[:5] for w in words if w not in STOP and len(w) > 2}


def _expand(stems: set[str]) -> set[str]:
    out = set(stems)
    for s in stems:
        out |= SYNONYMS.get(s, set())
    return out


def history() -> list[dict]:
    path = records.DATA_DIR / "history.json"
    return json.loads(path.read_text()) if path.exists() else []


@dataclass
class Resolution:
    payee: str | None                     # "Tanishk" or None
    amount: float | None
    address: str | None
    evidence: list[dict] = field(default_factory=list)   # the records that matched
    candidates: list[str] = field(default_factory=list)  # when ambiguous
    how: str = ""

    @property
    def explicit_request(self) -> str:
        return f"Pay {self.payee} {self.amount:g} USDC" if self.payee and self.amount else ""


def _amount_in(text: str) -> float | None:
    from .firewall.policy import _find_amount
    return _find_amount(text)


def resolve(request: str) -> Resolution:
    from .firewall.policy import _find_payee
    amount = _amount_in(request)

    named = _find_payee(request)                       # "pay Rahul 500": named directly
    if named:
        kind, name, info = named
        addr = info if kind == "contact" else info["address"]
        if amount is None and kind == "vendor":
            amount = info.get("balance_due")
        return Resolution(name.title(), amount, addr, [], [], "named in your request")

    want = _expand(_stems(request))
    if not want:
        return Resolution(None, amount, None, [], [], "no description to match")

    scores: dict[str, float] = {}
    evidence: dict[str, list[dict]] = {}
    for rec in history():
        hit = want & _expand(_stems(rec["what"]))
        if hit:
            name = rec["payee"].lower()
            scores[name] = scores.get(name, 0) + len(hit)
            evidence.setdefault(name, []).append({**rec, "matched": sorted(hit)})
    for name, v in records.vendors().items():
        hit = want & _expand(_stems(f"{v.get('role', '')} {v.get('business', '')}"))
        if hit:
            scores[name] = scores.get(name, 0) + len(hit)
            evidence.setdefault(name, []).append({"kind": "vendor record", "date": "",
                                                  "what": f"{v.get('business', name.title())}, {v.get('role', '')}",
                                                  "payee": name.title(), "matched": sorted(hit)})
    if not scores:
        return Resolution(None, amount, None, [], [], "nothing in your records matches")

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return Resolution(None, amount, None, [], [n.title() for n, _ in ranked[:3]], "more than one match")

    name = ranked[0][0]
    ev = sorted(evidence[name], key=lambda e: -len(e["matched"]))
    if amount is None:
        due = [e.get("balance_due") for e in ev if e.get("balance_due")]
        amount = due[0] if due else records.vendors().get(name, {}).get("balance_due")
    return Resolution(name.title(), float(amount) if amount else None, records.lookup_payee(name), ev, [],
                      "matched your own records")
