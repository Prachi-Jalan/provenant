"""PaymentEngine: the assistant's only path to the wallet, and it always goes through the firewall.

    make_policy(owner's raw text) -> agent proposes -> firewall.check -> (ALLOW | REPAIRED) -> wallet.send
                                                                       -> (BLOCK | ESCALATE) -> nothing signed

Invariants (covered by tests/test_assistant.py):
  * Only Verdict.final_tx is ever sent, and only when the decision is ALLOW or REPAIRED.
  * The agent's own tx / calldata is never sent.
  * One request = one policy = at most one payment (max_uses=1).
  * After a BLOCK on an owner request the agent may re-plan ONCE; the new proposal goes through
    ALL checks again.
Only frozen boundary functions are used: make_policy, check, log_verdict, wallet.send.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from provenant.schemas import ContentItem, Decision, ProposedTx

from .offline_agent import ContractAgentAdapter, OfflineNaiveAgent

SEND_DECISIONS = (Decision.ALLOW, Decision.REPAIRED)


@dataclass
class SendOutcome:
    tx_hash: str | None = None
    link: str | None = None
    dry_run: bool = False
    error: str | None = None


@dataclass
class RunResult:
    request: str
    requester: str
    policy: Any = None
    verdicts: list = field(default_factory=list)
    outcomes: dict = field(default_factory=dict)  # verdict index -> SendOutcome
    proposals: int = 0
    replan_from: int | None = None  # index of the first verdict that came from the re-plan
    note: str = ""
    log_errors: list = field(default_factory=list)

    @property
    def decision(self) -> str | None:
        if not self.verdicts:
            return None
        d = self.verdicts[-1].decision
        return getattr(d, "value", d)

    @property
    def paid(self) -> bool:
        return any(o.tx_hash and not o.error for o in self.outcomes.values())


def load_agent(name: str | None = None):
    """'naive' (default, offline) | 'p1-naive' | 'p1-llm' (Person 1's classes via the contract adapter)."""
    name = (name or os.environ.get("ASSISTANT_AGENT") or "naive").lower()
    if name == "naive":
        return OfflineNaiveAgent()
    if name in ("p1-naive", "p1-llm"):
        cls_name = "NaiveAgent" if name == "p1-naive" else "LLMAgent"
        cls = None
        for mod in ("provenant.agent", "provenant.agent.agent"):
            try:
                cls = getattr(importlib.import_module(mod), cls_name, None)
            except Exception:
                cls = None
            if cls:
                break
        if cls is None:
            raise RuntimeError(f"Could not import {cls_name} from provenant.agent. Use ASSISTANT_AGENT=naive.")
        return ContractAgentAdapter(cls())
    raise ValueError(f"Unknown agent '{name}'. Use naive, p1-naive or p1-llm.")


class PaymentEngine:
    def __init__(
        self,
        agent=None,
        owner_id: str = "owner",
        channel: str | None = None,
        send_fn: Callable[[ProposedTx], str] | None = None,
        log_fn: Callable[..., None] | None = None,
        policy_fn: Callable[..., Any] | None = None,
        check_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.agent = agent or load_agent()
        self.owner_id = owner_id
        self.channel = channel or os.environ.get("ASSISTANT_CHANNEL", "demo")  # "demo" is in the contract
        self._send_fn = send_fn
        self._log_fn = log_fn
        self._policy_fn = policy_fn
        self._check_fn = check_fn

    # ------------------------------------------------------------------ public

    def run(self, request: str, requester: str, contents: list[ContentItem]) -> RunResult:
        policy = self._make_policy(request, requester)
        result = RunResult(request=request, requester=requester, policy=policy)

        proposals = list(self.agent.propose(request, requester, contents) or [])
        result.proposals = len(proposals)
        sent = self._process(proposals, policy, contents, result, request, requester)

        owner_asked = requester == self.owner_id and bool(policy.allowed_recipients)
        blocked = [v for v in result.verdicts if v.decision == Decision.BLOCK]
        if not sent and owner_asked and blocked:
            feedback = " ".join(v.message for v in blocked)
            retry = list(self.agent.replan(request, requester, contents, feedback) or [])
            result.replan_from = len(result.verdicts)
            result.proposals += len(retry)
            self._process(retry, policy, contents, result, request, requester)

        if not result.verdicts:
            result.note = (
                policy.intent_text
                if not policy.allowed_recipients
                else "The agent did not propose a transaction (it could not work out a payee and amount)."
            )
        return result

    def run_confirmed(
        self, request: str, requester: str, contents: list[ContentItem], amount: float
    ) -> RunResult:
        """Owner said 'yes' to an ESCALATE. Rebuild the payment ONLY from the owner's request + trusted
        records, then run it through every check again. Nothing from the agent or the inbox is used."""
        policy = self._make_policy(request, requester)
        result = RunResult(request=request, requester=requester, policy=policy)
        if not policy.allowed_recipients:
            result.note = policy.intent_text
            return result
        to = policy.allowed_recipients[0]
        tx = ProposedTx(
            to=to, token=policy.token, action="transfer", amount=float(amount),
            decoded=f"transfer {amount:g} {policy.token} to {to} ({policy.payee_label})",
            raw_calldata="", context_ids=[c.id for c in contents if c.trust == "trusted"],
        )
        result.proposals = 1
        self._process([tx], policy, contents, result, f"{request} (owner confirmed)", requester)
        return result

    # ------------------------------------------------------------------ internals

    def _make_policy(self, request: str, requester: str):
        fn = self._policy_fn
        if fn is None:
            from provenant.firewall.policy import make_policy as fn  # frozen signature (P2)
        return fn(request, requester, self.owner_id)

    def _check(self, tx, policy, contents):
        fn = self._check_fn
        if fn is None:
            from provenant.firewall.firewall import check as fn  # frozen signature (P2)
        return fn(tx, policy, contents)

    def _process(self, proposals, policy, contents, result: RunResult, request: str, requester: str) -> bool:
        sent = False
        for tx in proposals:
            if sent:  # policy.max_uses == 1: one request can only ever pay once
                break
            verdict = self._check(tx, policy, contents)
            idx = len(result.verdicts)
            result.verdicts.append(verdict)
            if verdict.decision in SEND_DECISIONS and verdict.final_tx is not None:
                outcome = self._send(verdict.final_tx)  # ONLY the firewall's final_tx
                result.outcomes[idx] = outcome
                if outcome.tx_hash and not outcome.error:
                    verdict.tx_hash = outcome.tx_hash
                    sent = True
            self._log(verdict, request, requester, result)
        return sent

    def _send(self, tx: ProposedTx) -> SendOutcome:
        dry_env = os.environ.get("DRY_RUN", "1") != "0"
        try:
            tx_hash = self._send_fn(tx) if self._send_fn else _wallet_send(tx)
        except NotImplementedError:
            if not dry_env:
                return SendOutcome(error="chain.wallet.send is not implemented yet (Person 1). Nothing was sent.")
            tx_hash = _fake_hash(tx)  # wallet not built yet: simulate, as DRY_RUN=1 would
        except Exception as e:  # WalletError etc: report it, never crash the chat
            return SendOutcome(error=f"{type(e).__name__}: {e}")
        dry = dry_env or str(tx_hash).lower().startswith("0xdryrun")
        return SendOutcome(tx_hash=str(tx_hash), link=None if dry else _explorer(str(tx_hash)), dry_run=dry)

    def _log(self, verdict, request: str, requester: str, result: RunResult) -> None:
        try:
            fn = self._log_fn
            if fn is None:
                from provenant.verdict_log import log_verdict as fn
            fn(verdict, request, requester, self.channel)
        except Exception as e:  # logging must never break a payment decision
            result.log_errors.append(f"{type(e).__name__}: {e}")


def _wallet_send(tx: ProposedTx) -> str:
    from provenant.chain import wallet

    return wallet.send(tx)


def _explorer(tx_hash: str) -> str:
    try:
        from provenant.chain import wallet

        fn = getattr(wallet, "explorer_url", None)
        if callable(fn):
            return fn(tx_hash)
    except Exception:
        pass
    return "https://sepolia.basescan.org/tx/" + tx_hash


def _fake_hash(tx: ProposedTx) -> str:
    amount = "inf" if math.isinf(tx.amount) else tx.amount
    digest = hashlib.sha1(json.dumps([tx.to, tx.action, amount]).encode()).hexdigest()[:8]
    return "0xDRYRUN" + digest
