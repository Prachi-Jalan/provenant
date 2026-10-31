"""Shared test setup. Tests must never touch the network, spend an API key or send real money.

A developer's real .env is loaded when the modules are imported, so every variable that could
change a test's outcome is cleared (or pinned) before each test.
- P1: wallet keys, chain settings and LLM keys cleared; DRY_RUN on; verdict log in a temp dir.
- P3: always the local in-memory graph (never live TigerGraph), rebuilt per test so a test that
  swaps in fake contacts/vendors gets a graph built from those; offline hash embedder.
"""
import pytest

from provenant import verdict_log
from provenant.graph import learn, reputation

_CLEARED = (
    "OWNER_PRIVATE_KEY", "VENDOR_ADDRESS", "ATTACKER_ADDRESS",   # wallet keys / addresses
    "RPC_URL", "USDC_ADDRESS",                                    # chain settings
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER",        # LLM access
    "TG_HOST", "TG_SECRET",                                       # live TigerGraph
)


@pytest.fixture(autouse=True)
def _safe_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN", "1")            # tests that need the live path turn this off themselves
    monkeypatch.setenv("INTENT_MODE", "offline")  # no LLM / embedding calls from the firewall
    monkeypatch.setenv("EMBEDDER", "hash")        # offline attack-pattern embeddings
    for var in _CLEARED:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(verdict_log, "LOG_FILE", tmp_path / "logs" / "verdicts.jsonl")
    monkeypatch.setattr(learn, "LEARNED_FILE", tmp_path / "logs" / "learned_attacks.jsonl")
    reputation._backend = None
    yield
    reputation._backend = None
