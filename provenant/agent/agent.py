"""The agent. Owner: Person 1.
It can only PROPOSE. It never holds a key and never calls chain.wallet.
"""
from ..schemas import ProposedTx, Verdict
from .tools import Session


def propose_tx(action: str, to: str, amount: float, context_ids: list[str]) -> ProposedTx:
    """The agent's only money tool: encode -> decode -> ProposedTx."""
    raise NotImplementedError("Person 1")


class NaiveAgent:
    """Scripted stand-in: trusts the most recent address it read. For demos + eval."""

    def act(self, session: Session) -> ProposedTx | None:
        raise NotImplementedError("Person 1")


class LLMAgent:
    """Real tool-calling agent (OpenAI/Anthropic)."""

    def act(self, request: str, session: Session) -> ProposedTx | None:
        raise NotImplementedError("Person 1")

    def replan(self, request: str, session: Session, blocked: Verdict) -> ProposedTx | None:
        """One retry with the firewall's reasons; result goes through the firewall again."""
        raise NotImplementedError("Person 1")
