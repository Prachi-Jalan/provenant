"""Firewall checks. Owner: Person 2.
reputation_check lives in provenant.graph (Person 3) and is imported by firewall.py.
"""
from ..schemas import CheckResult, ContentItem, Policy, ProposedTx


def policy_check(tx: ProposedTx, policy: Policy) -> CheckResult:
    """name="policy", hard=True."""
    raise NotImplementedError("Person 2")


def provenance_check(tx: ProposedTx, contents: list[ContentItem]) -> CheckResult:
    """name="provenance", hard=True. Pass if tx.to appears in any trusted item."""
    raise NotImplementedError("Person 2")


def intent_check(tx: ProposedTx, policy: Policy) -> CheckResult:
    """name="intent", hard=False, score in [0,1]. Embeddings + LLM judge."""
    raise NotImplementedError("Person 2")
