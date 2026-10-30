"""Known attack messages (data/attacks.jsonl) and the match format both backends return."""
from __future__ import annotations

import json
from typing import NamedTuple

from .. import records

ATTACKS_FILE = records.DATA_DIR / "attacks.jsonl"


class AttackMatch(NamedTuple):
    attack_id: str
    similarity: float        # cosine similarity, 1.0 = identical meaning
    category: str            # wallet_change, unlimited_approval, ...
    wallets: frozenset[str]  # wallets this known attack pointed people to (graph edges)


def load_attacks() -> list[dict]:
    return [json.loads(line) for line in ATTACKS_FILE.read_text().splitlines() if line.strip()]
