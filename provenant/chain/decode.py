"""ERC-20 calldata <-> ProposedTx. Owner: Person 1.
transfer(address,uint256) = 0xa9059cbb, approve(address,uint256) = 0x095ea7b3.
"""
from ..schemas import ProposedTx


def decode_erc20(calldata: str, token: str = "USDC", decimals: int = 6,
                 context_ids: list[str] | None = None) -> ProposedTx:
    """Huge approvals -> amount = float('inf'), decoded = "approve UNLIMITED ..."."""
    raise NotImplementedError("Person 1")


def encode_erc20(action: str, to: str, amount: float, decimals: int = 6) -> str:
    raise NotImplementedError("Person 1")
