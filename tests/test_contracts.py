"""Sanity checks on the shared formats. Everyone: keep these green."""
import json
from pathlib import Path

from provenant import records
from provenant.graph import reputation_check
from provenant.schemas import ContentItem, Decision, Policy, ProposedTx, Verdict

ROOT = Path(__file__).resolve().parent.parent


def test_seed_data_loads_and_addresses_are_lowercase():
    for addr in list(records.contacts().values()) + [v["address"] for v in records.vendors().values()]:
        assert addr == addr.lower() and addr.startswith("0x") and len(addr) == 42


def test_verdict_serialises():
    tx = ProposedTx(to="0x" + "1" * 40, token="USDC", action="transfer", amount=500)
    v = Verdict(Decision.ALLOW, 0.0, [], original=tx, final_tx=tx)
    assert json.loads(json.dumps(v.to_dict(), default=str))["decision"] == "ALLOW"


def test_deny_all_policy_allows_nothing():
    p = Policy.deny_all("stranger")
    assert p.actions == [] and p.allowed_recipients == []


def test_reputation_stub_is_importable():
    tx = ProposedTx(to="0x" + "1" * 40, token="USDC", action="transfer", amount=1)
    assert reputation_check(tx, Policy.deny_all("x")).name == "reputation"


def test_eval_cases_match_contract():
    for line in (ROOT / "eval" / "cases.jsonl").read_text().splitlines():
        case = json.loads(line)
        assert case["expected"] in {d.value for d in Decision}
        for c in case["contents"]:
            ContentItem(**c)
