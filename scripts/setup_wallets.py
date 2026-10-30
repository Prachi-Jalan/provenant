"""Create 3 testnet wallets (owner, vendor, attacker) and write them into .env.

    python scripts/setup_wallets.py

- Fresh keys from eth_account. TESTNET ONLY: never reuse a real wallet.
- Writes ONLY .env (copied from .env.example). It never touches data/, which holds the
  team's shared seed addresses used by the eval cases and the graph.
- Refuses to overwrite an existing .env (that would lose the keys of a funded wallet).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from eth_account import Account

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"

NEXT_STEPS = """
NEXT STEPS (about 5 minutes). Fund the OWNER address only; vendor and attacker just receive.

1. Base Sepolia ETH (pays gas):  https://portal.cdp.coinbase.com/onchain-tools/faucet
   Network = Base Sepolia, token = ETH.   Backup: https://www.alchemy.com/faucets/base-sepolia
2. Test USDC:                    https://faucet.circle.com
   Network = Base Sepolia (NOT Ethereum Sepolia), token = USDC.
   The confirmation link should say sepolia.basescan.org.
3. Check, then send your first USDC:
   python -m provenant.chain.wallet --balances
   python -m provenant.chain.wallet
"""


def _key_hex(account) -> str:
    h = account.key.hex()
    return h if h.startswith("0x") else "0x" + h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing .env (LOSES the old keys)")
    args = ap.parse_args()

    if ENV_PATH.exists() and not args.force:
        print(f".env already exists at {ENV_PATH}, so I did not touch it.\n"
              "Making new wallets would lose the keys of any wallet you already funded.\n"
              "Use --force only if you really want brand-new wallets.")
        return 1

    owner, vendor, attacker = (Account.create() for _ in range(3))
    values = {
        "OWNER_PRIVATE_KEY": _key_hex(owner), "OWNER_ADDRESS": owner.address,
        "VENDOR_PRIVATE_KEY": _key_hex(vendor), "VENDOR_ADDRESS": vendor.address,
        "ATTACKER_PRIVATE_KEY": _key_hex(attacker), "ATTACKER_ADDRESS": attacker.address,
    }

    template = ENV_EXAMPLE.read_text(encoding="utf-8") if ENV_EXAMPLE.exists() else ""
    lines, seen = [], set()
    for line in template.splitlines():
        m = re.match(r"^([A-Z_]+)=", line)
        if m and m.group(1) in values:
            lines.append(f"{m.group(1)}={values[m.group(1)]}")
            seen.add(m.group(1))
        else:
            lines.append(line)
    lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("Created .env with 3 fresh testnet wallets (the keys are in .env, which git ignores).\n")
    print(f"  owner    {owner.address}   <- fund this one (it pays)")
    print(f"  vendor   {vendor.address}   (destination of your first 1 USDC test send)")
    print(f"  attacker {attacker.address}   (for your own experiments; the demo uses the fixed 0xEVIL in data/)")
    print(NEXT_STEPS)
    print("NEVER commit .env. NEVER paste these keys into chat. Testnet only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
