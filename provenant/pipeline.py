"""End-to-end flow: owner request -> policy -> agent proposes -> firewall -> (re-plan) -> wallet.

The Telegram bot, the demo runner and the eval can all call handle(). It follows the flow in
bot/telegram_bot.py:  make_policy -> agent.act -> firewall.check -> wallet.send(final_tx) -> log_verdict

Repair order (build plan): the firewall's deterministic repair runs first (inside check()).
If the verdict is still BLOCK/ESCALATE and the owner did ask for a payment, the agent gets ONE
re-plan with the firewall's reasons, and the new proposal goes back through the firewall.
Only ALLOW / REPAIRED verdicts reach the wallet, and only verdict.final_tx is signed.
"""
from __future__ import annotations

from typing import Callable, Optional

from provenant import verdict_log
from provenant.firewall.firewall import check
from provenant.firewall.policy import make_policy
from provenant.schemas import Decision, Policy, ProposedTx, Verdict


def handle(
    request: str,
    requester: str,
    session,
    agent,
    owner_id: str = "owner",
    channel: str = "demo",
    send: Optional[Callable[[ProposedTx], str]] = None,
    log: bool = True,
    max_replans: int = 1,
    policy: Optional[Policy] = None,
) -> Optional[Verdict]:
    """Run one request through agent + firewall and sign anything that is allowed.

    `session` is the Session whose .log holds everything read so far (log the incoming
    messages with session.chat_message first). Returns the final Verdict, or None if the
    agent proposed nothing (so there is nothing to check or sign).
    """
    if send is None:
        from provenant.chain.wallet import send  # lazy: keeps web3 out of dry tests

    policy = policy or make_policy(request, requester, owner_id)

    tx = agent.act(request, session)
    if tx is None:
        return None
    verdict = check(tx, policy, session.log)

    replans = 0
    while (
        verdict.decision in (Decision.BLOCK, Decision.ESCALATE)
        and policy.allowed_recipients  # owner asked for a payment, so a retry can help
        and replans < max_replans
    ):
        replans += 1
        new_tx = agent.replan(request, session, verdict)
        if new_tx is None:
            break
        if log:  # the rejected attempt is a real decision too
            verdict_log.log_verdict(verdict, request, requester, channel)
        verdict = check(new_tx, policy, session.log)

    if verdict.decision in (Decision.ALLOW, Decision.REPAIRED):
        try:
            verdict.tx_hash = send(verdict.final_tx)
        except Exception as e:  # WalletError, RPC down, no gas...
            verdict.tx_hash = None
            verdict.message = f"{verdict.message} (Approved but NOT sent: {e})".strip()

    if log:
        verdict_log.log_verdict(verdict, request, requester, channel)
    return verdict
