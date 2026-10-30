"""check(tx, policy, contents) -> Verdict. The only function the bot, eval and demo call.

Flow: run 4 checks -> no hard fail = ALLOW
      hard fail -> dangerous action or no owner request = BLOCK
                -> safe repair possible = rebuild to the verified address, re-run ALL checks = REPAIRED
                -> otherwise = ESCALATE (owner asked for something, but we can't make it safe alone)
Only verdict.final_tx is ever signed, and only for ALLOW / REPAIRED.
"""
from __future__ import annotations

import math

from provenant import records
from provenant.firewall.checks import intent_check, policy_check, provenance_check, reputation
from provenant.schemas import CheckResult, ContentItem, Decision, Policy, ProposedTx, Verdict

# Risk weights for a failed check. Hand-set for now; P3's eval can learn these later.
WEIGHTS = {"policy": 0.35, "provenance": 0.35, "reputation": 0.2, "intent": 0.3}


def check(tx: ProposedTx, policy: Policy, contents: list[ContentItem]) -> Verdict:
    contents = list(contents) + records.records_as_content()
    checks = run_checks(tx, policy, contents)
    risk = risk_score(checks)

    if not _hard_failures(checks):
        soft = [c for c in checks if not c.passed]
        note = f" (note: {soft[0].reason})" if soft else ""
        return Verdict(Decision.ALLOW, risk, checks, tx, tx,
                       f"Approved: {_describe(tx)}.{note}")

    reasons = " ".join(c.reason for c in _hard_failures(checks))

    # 1. Nothing to repair toward: the owner didn't ask for a payment.
    if not policy.allowed_recipients:
        return Verdict(Decision.BLOCK, max(risk, 0.9), checks, tx, None,
                       f"Blocked: {reasons}")

    # 2. Dangerous action types are never "fixed" into something else.
    if _is_dangerous(tx, policy):
        return Verdict(Decision.BLOCK, max(risk, 0.9), checks, tx, None,
                       f"Blocked: {reasons}")

    # 3. Deterministic repair from trusted records.
    repaired = repair(tx, policy)
    if repaired is not None:
        rechecks = run_checks(repaired, policy, contents)
        if not _hard_failures(rechecks):
            return Verdict(Decision.REPAIRED, risk, checks, tx, repaired,
                           f"Repaired: the agent tried to {_describe(tx)}. {reasons} "
                           f"Paying {policy.payee_label}'s verified address instead: {_describe(repaired)}.")

    # 4. Owner asked for something, but we can't make it safe on our own.
    return Verdict(Decision.ESCALATE, risk, checks, tx, None,
                   f"Needs your confirmation: {reasons} "
                   f"Please confirm with {policy.payee_label or 'the payee'} directly.")


def run_checks(tx: ProposedTx, policy: Policy, contents: list[ContentItem]) -> list[CheckResult]:
    return [
        policy_check(tx, policy),
        provenance_check(tx, contents),
        intent_check(tx, policy),
        reputation(tx, policy),
    ]


def repair(tx: ProposedTx, policy: Policy) -> ProposedTx | None:
    """Rebuild the tx from trusted records. Never uses an address from untrusted content."""
    if not policy.repair_allowed or len(policy.allowed_recipients) != 1:
        return None
    if tx.action != "transfer" or "transfer" not in policy.actions:
        return None
    if math.isinf(tx.amount) or not (0 < tx.amount <= policy.max_amount):
        return None
    to = policy.allowed_recipients[0]  # came from records via make_policy
    return ProposedTx(
        to=to, token=policy.token, action="transfer", amount=tx.amount,
        decoded=f"transfer {tx.amount:g} {policy.token} to {to} ({policy.payee_label})",
        raw_calldata="",  # wallet.send() re-encodes from fields; never reuse the agent's calldata
        context_ids=list(tx.context_ids),
    )


def risk_score(checks: list[CheckResult]) -> float:
    risk = 0.0
    for c in checks:
        w = WEIGHTS.get(c.name, 0.2)
        if c.score is not None:
            risk += w * (1.0 - c.score)
        elif not c.passed:
            risk += w
    return round(min(risk, 1.0), 3)


def _hard_failures(checks: list[CheckResult]) -> list[CheckResult]:
    return [c for c in checks if c.hard and not c.passed]


def _is_dangerous(tx: ProposedTx, policy: Policy) -> bool:
    if tx.action == "approve" and math.isinf(tx.amount):
        return True
    return tx.action not in policy.actions


def _describe(tx: ProposedTx) -> str:
    amt = "UNLIMITED" if math.isinf(tx.amount) else f"{tx.amount:g}"
    return f"{tx.action} {amt} {tx.token} to {tx.to[:6]}…{tx.to[-4:]}"
