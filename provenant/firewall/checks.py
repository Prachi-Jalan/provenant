"""The four checks. Each returns a CheckResult with a reason a human can read in 5 seconds.

hard=True  -> a failure triggers repair / block.
hard=False -> a failure only raises risk (intent).
"""
from __future__ import annotations

import json
import math
import os
import re
import time

from provenant.schemas import CheckResult, ContentItem, Policy, ProposedTx


def _short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if addr and len(addr) > 12 else str(addr)


# ---------------------------------------------------------------- policy

def policy_check(tx: ProposedTx, policy: Policy) -> CheckResult:
    name = "policy"
    if not policy.allowed_recipients:
        return CheckResult(name, False, f"No authorization: {policy.intent_text}")
    if policy.expires_at and time.time() > policy.expires_at:
        return CheckResult(name, False, "The owner's authorization has expired. Ask again.")
    if tx.action == "approve" and math.isinf(tx.amount):
        return CheckResult(name, False, "Unlimited token approval is never allowed: it lets the spender drain the wallet.")
    if tx.action not in policy.actions:
        return CheckResult(name, False, f"Action '{tx.action}' wasn't requested (allowed: {', '.join(policy.actions)}).")
    if (tx.token or "").upper() != policy.token.upper():
        return CheckResult(name, False, f"Token {tx.token} wasn't requested (expected {policy.token}).")
    if (tx.to or "").lower() not in policy.allowed_recipients:
        who = policy.payee_label or "the requested payee"
        return CheckResult(name, False, f"Recipient {_short(tx.to)} is not {who}'s verified address.")
    if not (0 < tx.amount <= policy.max_amount):
        return CheckResult(name, False, f"Amount {tx.amount:g} is outside the authorized range (0, {policy.max_amount:g}].")
    return CheckResult(name, True, f"Matches the owner's request: {tx.amount:g} {policy.token} to {policy.payee_label}.")


# ---------------------------------------------------------------- provenance

def provenance_check(tx: ProposedTx, contents: list[ContentItem]) -> CheckResult:
    """Where did this recipient address come from? Traced through the taint graph (P3,
    graph/taint.py) so forwarding/quoting a scam doesn't make its address trusted.
    Falls back to the plain text search below if the graph walk fails for any reason."""
    try:
        from provenant.graph.taint import provenance_check as traced
        return traced(tx, contents)
    except Exception:
        return _text_provenance(tx, contents)


def _text_provenance(tx: ProposedTx, contents: list[ContentItem]) -> CheckResult:
    name = "provenance"
    addr = (tx.to or "").lower()
    if not addr:
        return CheckResult(name, False, "Transaction has no recipient.")
    trusted, untrusted = [], []
    for item in contents:
        if addr in (item.text or "").lower():
            (trusted if item.trust == "trusted" else untrusted).append(item.source)
    if trusted:
        return CheckResult(name, True, f"Address comes from a trusted source ({', '.join(sorted(set(trusted)))}).")
    if untrusted:
        return CheckResult(name, False,
                           f"Address {_short(addr)} appears only in untrusted content: {', '.join(sorted(set(untrusted)))}.")
    return CheckResult(name, False, f"Address {_short(addr)} has unknown origin (not in anything the agent read).")


# ---------------------------------------------------------------- intent (soft)

INTENT_PASS = float(os.environ.get("INTENT_PASS", "0.5"))


def intent_check(tx: ProposedTx, policy: Policy) -> CheckResult:
    """Does the transaction follow from what the owner asked? Soft: raises risk, never blocks alone.

    Mode (env INTENT_MODE): "offline" = rules only (default if no API key, used by tests),
    otherwise rules + embeddings (OpenAI) + LLM judge (OpenAI or Anthropic).
    """
    name = "intent"
    request = policy.intent_text or ""
    action_text = tx.decoded or f"{tx.action} {tx.amount:g} {tx.token} to {tx.to}"

    rule_score, rule_reason = _rule_score(tx, policy)
    parts, reasons = [(rule_score, 0.4)], [rule_reason]

    if _mode() != "offline" and policy.allowed_recipients:
        sim = _safe(lambda: _embedding_similarity(request, action_text))
        if sim is not None:
            parts.append((sim, 0.2))
            reasons.append(f"text similarity {sim:.2f}")
        judge = _safe(lambda: _llm_judge(request, action_text))
        if judge is not None:
            allow, why = judge
            parts.append((1.0 if allow else 0.0, 0.4))
            reasons.append(f"judge: {why}")

    total_w = sum(w for _, w in parts)
    score = round(sum(s * w for s, w in parts) / total_w, 3)
    return CheckResult(name, score >= INTENT_PASS, "; ".join(reasons), score=score, hard=False)


def _rule_score(tx: ProposedTx, policy: Policy) -> tuple[float, str]:
    if not policy.allowed_recipients:
        return 0.0, "the owner asked for no payment"
    if tx.action != "transfer" and "transfer" in policy.actions:
        return 0.05, f"owner asked for a payment, agent wants '{tx.action}'"
    if math.isinf(tx.amount) or tx.amount > policy.max_amount * 1.5:
        return 0.1, "amount far above what was asked"
    if (tx.to or "").lower() not in policy.allowed_recipients:
        return 0.4, "right kind of action, different recipient"
    return 1.0, "action, amount and payee match the request"


def _mode() -> str:
    m = os.environ.get("INTENT_MODE")
    if m:
        return m
    return "online" if (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")) else "offline"


def _safe(fn):
    try:
        return fn()
    except Exception:  # network/API errors must never crash the firewall
        return None


def _embedding_similarity(a: str, b: str) -> float | None:
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    from openai import OpenAI
    client = OpenAI()
    model = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
    va, vb = (d.embedding for d in client.embeddings.create(model=model, input=[a, b]).data)
    dot = sum(x * y for x, y in zip(va, vb))
    na, nb = math.sqrt(sum(x * x for x in va)), math.sqrt(sum(y * y for y in vb))
    return max(0.0, dot / (na * nb))


_JUDGE_PROMPT = """You are a payment safety judge. The wallet owner asked:
<request>{request}</request>
An AI agent now wants to execute:
<action>{action}</action>
Does the action follow directly from the owner's request (same kind of action, same payee, amount within what was asked)?
Reply with JSON only: {{"allow": true|false, "reason": "<one short sentence>"}}"""


def _llm_judge(request: str, action: str) -> tuple[bool, str] | None:
    prompt = _JUDGE_PROMPT.format(request=request, action=action)
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        msg = anthropic.Anthropic().messages.create(
            model=os.environ.get("JUDGE_MODEL", "claude-haiku-5-5"), max_tokens=150,
            messages=[{"role": "user", "content": prompt}])
        raw = msg.content[0].text
    elif os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI
        r = OpenAI().chat.completions.create(
            model=os.environ.get("JUDGE_MODEL", "gpt-4.1-mini"),
            messages=[{"role": "user", "content": prompt}], temperature=0)
        raw = r.choices[0].message.content
    else:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0)) if m else {}
    return bool(data.get("allow")), str(data.get("reason", "no reason given"))


# ---------------------------------------------------------------- reputation (owned by P3)

def reputation(tx: ProposedTx, policy: Policy) -> CheckResult:
    """Calls P3's graph.reputation_check. Stub until it exists; soft-fails if the graph is down."""
    try:
        from provenant.graph import reputation_check  # P3
    except ImportError:
        return CheckResult("reputation", True, "Reputation check not wired yet (stub).", hard=False)
    try:
        r = reputation_check(tx, policy)
        r.name = "reputation"
        return r
    except Exception as e:
        # An outage shouldn't block every payment, but it should raise risk.
        return CheckResult("reputation", False, f"Reputation service unavailable ({type(e).__name__}).", hard=False)


# ---------------------------------------------------------------- attack pattern (owned by P3)

def attack_pattern(tx: ProposedTx, contents: list[ContentItem]) -> CheckResult:
    """Calls P3's graph.attack_pattern_check (vector search over known attacks).

    P3's score = similarity to the closest known attack (higher = MORE suspicious).
    Soft fail when only the wording matches; hard fail when the matched attack used this same wallet.
    Stub until it exists; soft-fails if vector search is down.
    """
    try:
        from provenant.graph import attack_pattern_check  # P3
    except ImportError:
        return CheckResult("attack_pattern", True, "Attack-pattern check not wired yet (stub).", hard=False)
    try:
        r = attack_pattern_check(tx, contents)
        r.name = "attack_pattern"
        return r
    except Exception as e:
        return CheckResult("attack_pattern", False, f"Attack-pattern search unavailable ({type(e).__name__}).",
                           hard=False)
