"""Shared test setup. Tests must never touch the network, spend an API key or send real money.

A developer's real .env is loaded when the modules are imported, so every variable that could
change a test's outcome is cleared (or pinned) before each test.
"""
import pytest

from provenant import verdict_log

_CLEARED = (
    "OWNER_PRIVATE_KEY", "VENDOR_ADDRESS", "ATTACKER_ADDRESS",   # wallet keys / addresses
    "RPC_URL", "USDC_ADDRESS",                                    # chain settings
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER",        # LLM access
)


@pytest.fixture(autouse=True)
def _safe_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN", "1")            # tests that need the live path turn this off themselves
    monkeypatch.setenv("INTENT_MODE", "offline")  # no LLM / embedding calls from the firewall
    for var in _CLEARED:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(verdict_log, "LOG_FILE", tmp_path / "logs" / "verdicts.jsonl")
