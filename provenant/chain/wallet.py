"""Signing + sending on Base Sepolia. Owner: Person 1.

This is the ONLY file that ever reads OWNER_PRIVATE_KEY. Only the firewall's approved
Verdict.final_tx should reach send(). The agent package never imports this module.

    python -m provenant.chain.wallet              # P1 milestone: send 1 test USDC to VENDOR_ADDRESS
    python -m provenant.chain.wallet --balances   # ETH + USDC of every wallet in .env
    DRY_RUN=1 python -m provenant.chain.wallet    # sign nothing, touch no network
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from provenant.chain.decode import decode_erc20, encode_erc20, from_raw, normalize_address
from provenant.schemas import ProposedTx

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

EXPLORER = "https://sepolia.basescan.org/tx/"
BASE_SEPOLIA_CHAIN_ID = 84532
DEFAULT_RPC_URL = "https://sepolia.base.org"
# Circle's official test USDC on Base Sepolia (6 decimals)
DEFAULT_USDC_ADDRESS = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
DRY_RUN_HASH = "0xDRYRUN"
_BALANCE_OF_SELECTOR = "0x70a08231"


class WalletError(RuntimeError):
    pass


def explorer_url(tx_hash: str) -> str:
    return EXPLORER + tx_hash


def dry_run() -> bool:
    return os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")


def _rpc_url() -> str:
    return os.getenv("RPC_URL", DEFAULT_RPC_URL)


def _usdc_address() -> str:
    return os.getenv("USDC_ADDRESS", DEFAULT_USDC_ADDRESS).lower()


def _need_env(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise WalletError(f"{name} is missing. Add it to your .env file (see .env.example).")
    return val


def _web3():
    try:
        from web3 import Web3
    except ImportError as e:  # pragma: no cover
        raise WalletError("web3 is not installed. Run: pip install -r requirements.txt") from e
    w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise WalletError(f"Cannot reach RPC at {_rpc_url()}. Check RPC_URL / your internet.")
    return w3


def owner_account():
    """eth_account object for the owner wallet (key from OWNER_PRIVATE_KEY)."""
    from eth_account import Account

    key = _need_env("OWNER_PRIVATE_KEY")
    if not key.startswith("0x"):
        key = "0x" + key
    return Account.from_key(key)


def _validate(tx: ProposedTx) -> str:
    """Return calldata for tx, refusing anything this wallet will not sign.

    Calldata is ALWAYS rebuilt from tx.to / tx.amount / tx.action, never taken from
    tx.raw_calldata, so a repaired transaction (new `to`) is exactly what gets signed.
    """
    if tx.action not in ("transfer", "approve"):
        raise WalletError(f"wallet only signs 'transfer' or 'approve', got {tx.action!r}")
    if (tx.token or "").upper() != "USDC":
        raise WalletError(f"wallet only handles USDC, got {tx.token!r}")
    normalize_address(tx.to)
    return encode_erc20(tx.action, tx.to, tx.amount)


def send(tx: ProposedTx, wait: bool = True) -> str:
    """Sign + broadcast an ERC-20 transfer/approve from the OWNER wallet. Returns the tx hash.

    DRY_RUN=1 -> signs nothing, touches no network, returns "0xDRYRUN".
    """
    calldata = _validate(tx)

    if dry_run():
        return DRY_RUN_HASH

    w3 = _web3()
    account = owner_account()
    fees = _fees(w3)
    base_tx = {
        "chainId": BASE_SEPOLIA_CHAIN_ID,
        "from": account.address,
        "to": w3.to_checksum_address(_usdc_address()),
        "data": calldata,
        "value": 0,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        **fees,
    }
    try:
        base_tx["gas"] = int(w3.eth.estimate_gas(base_tx) * 1.3)
    except Exception as e:  # revert, no ETH for gas, not enough USDC...
        raise WalletError(
            f"Transaction would fail: {e}. Check the owner wallet has Base Sepolia ETH "
            f"(for gas) and enough test USDC."
        ) from e

    signed = account.sign_transaction(base_tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction  # web3 v7 / v6
    tx_hash = w3.eth.send_raw_transaction(raw).hex()
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash

    if wait:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status != 1:
            raise WalletError(f"Transaction reverted on chain: {explorer_url(tx_hash)}")
    return tx_hash


def send_usdc(to: str, amount: float) -> str:
    """First milestone: send test USDC from Python."""
    tx = decode_erc20(encode_erc20("transfer", to, amount))
    return send(tx)


def _fees(w3) -> dict:
    """EIP-1559 fee fields (Base supports them)."""
    base = w3.eth.get_block("latest").get("baseFeePerGas")
    if base is None:
        return {"gasPrice": w3.eth.gas_price}
    tip = w3.to_wei(0.01, "gwei")
    return {"maxFeePerGas": int(base * 2 + tip), "maxPriorityFeePerGas": tip}


# --------------------------------------------------------------------------- #
# Read helpers (used by --balances and the dashboard)
# --------------------------------------------------------------------------- #
def get_eth_balance(address: str) -> float:
    w3 = _web3()
    return float(w3.from_wei(w3.eth.get_balance(w3.to_checksum_address(address)), "ether"))


def get_usdc_balance(address: str) -> float:
    w3 = _web3()
    addr = normalize_address(address)
    data = _BALANCE_OF_SELECTOR + addr[2:].rjust(64, "0")
    out = w3.eth.call({"to": w3.to_checksum_address(_usdc_address()), "data": data})
    return from_raw(int.from_bytes(out, "big"))


def _wallet_addresses() -> dict[str, str]:
    out = {}
    try:
        out["owner"] = owner_account().address
    except WalletError:
        pass
    for label, var in (("vendor", "VENDOR_ADDRESS"), ("attacker", "ATTACKER_ADDRESS")):
        if os.getenv(var):
            out[label] = os.getenv(var)
    return out


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Send test USDC from the owner wallet (milestone 1).")
    p.add_argument("--to", help="recipient address (default: VENDOR_ADDRESS from .env)")
    p.add_argument("--amount", type=float, default=1.0, help="USDC amount (default 1)")
    p.add_argument("--balances", action="store_true", help="print balances and exit")
    args = p.parse_args(argv)

    try:
        if args.balances:
            for label, addr in _wallet_addresses().items():
                print(f"{label:9s} {addr}  ETH={get_eth_balance(addr):.6f}  USDC={get_usdc_balance(addr):.2f}")
            return 0

        to = args.to or _need_env("VENDOR_ADDRESS")
        mode = "DRY RUN (nothing is sent)" if dry_run() else "LIVE on Base Sepolia"
        print(f"[{mode}] send {args.amount:g} USDC to {to.lower()}")
        tx_hash = send_usdc(to, args.amount)
        print(f"tx hash : {tx_hash}")
        if dry_run():
            print("(dry run: no real transaction exists, so there is no BaseScan page)")
        else:
            print(f"BaseScan: {explorer_url(tx_hash)}")
        return 0
    except WalletError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_main())
