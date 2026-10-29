"""check -> decide -> repair. Owner: Person 2.
THE integration point: agent, bot, eval and dashboard all go through check().
"""
from ..schemas import ContentItem, Policy, ProposedTx, Verdict


def check(tx: ProposedTx, policy: Policy, contents: list[ContentItem]) -> Verdict:
    """Runs policy, provenance, intent and reputation checks.
    ALLOW | REPAIRED (final_tx = verified recipient) | ESCALATE | BLOCK.
    Repairs only ever use addresses from trusted records."""
    raise NotImplementedError("Person 2")
