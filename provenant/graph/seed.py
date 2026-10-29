"""Builds the seed data ONCE, so TigerGraph and the local fallback load identical data.

Real-looking story:
  - 2 flagged scam wallets that both pay a "mule" wallet (0xe011...e1).
  - The mule is the address the scammer puts in fake invoices / chat messages.
  - Rahul, Priya and Acme each have a verified address we've paid before.
  - ~40 random "background" wallets with random transfers, so the graph isn't trivially small.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

from .. import records

SEED = 7
N_BACKGROUND = 40


@dataclass
class SeedData:
    wallets: dict[str, dict] = field(default_factory=dict)        # address -> {flagged, label}
    sent_to: list[tuple[str, str]] = field(default_factory=list)  # (a, b)
    payees: dict[str, str] = field(default_factory=dict)          # name -> kind
    paid_at: list[tuple[str, str, int]] = field(default_factory=list)  # (payee, address, times)


def _addr(rng: random.Random) -> str:
    return "0x" + "".join(rng.choice("0123456789abcdef") for _ in range(40))


def build() -> SeedData:
    rng = random.Random(SEED)
    s = SeedData()
    flagged_file = json.loads((records.DATA_DIR / "flagged.json").read_text())

    # scam cluster
    for a in flagged_file["flagged"]:
        s.wallets[a.lower()] = {"flagged": True, "label": "known scam"}
    for a, b in flagged_file["edges"]:
        for x in (a, b):
            s.wallets.setdefault(x.lower(), {"flagged": False, "label": ""})
        s.sent_to.append((a.lower(), b.lower()))

    # payees with history
    for name, addr in records.contacts().items():
        s.wallets[addr] = {"flagged": False, "label": f"contact:{name}"}
        s.payees[name] = "contact"
        s.paid_at.append((name, addr, rng.randint(1, 6)))
    for name, v in records.vendors().items():
        s.wallets[v["address"]] = {"flagged": False, "label": f"vendor:{name}"}
        s.payees[name] = "vendor"
        s.paid_at.append((name, v["address"], v["paid_count"]))

    # background noise: random wallets + random transfers, never touching the scam cluster
    background = [_addr(rng) for _ in range(N_BACKGROUND)]
    for a in background:
        s.wallets[a] = {"flagged": False, "label": ""}
    clean = background + list(records.contacts().values()) + [v["address"] for v in records.vendors().values()]
    for _ in range(60):
        a, b = rng.sample(clean, 2)
        s.sent_to.append((a, b))
    return s
