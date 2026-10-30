"""Graph queries on the local fallback. The same answers must come back from TigerGraph."""

import pytest

from provenant.graph import reputation
from provenant.schemas import Policy, ProposedTx

MULE = "0xe0110000000000000000000000000000000000e1"
CASH_OUT = "0xd4a1000000000000000000000000000000000d01"
RAHUL = "0x1111111111111111111111111111111111111111"
ACME = "0xacce000000000000000000000000000000000001"

@pytest.fixture(autouse=True)
def local_backend(monkeypatch):
    monkeypatch.delenv("TG_HOST", raising=False)
    reputation._backend = None
    yield
    reputation._backend = None

def test_flagged_wallet_is_zero_hops():
    assert reputation.hops_to_flagged("0xbad0000000000000000000000000000000000001") == 0

def test_mule_is_one_hop_from_scammers():
    assert reputation.hops_to_flagged(MULE) == 1

def test_cash_out_wallet_is_two_hops():
    assert reputation.hops_to_flagged(CASH_OUT) == 2

def test_clean_contact_has_no_flagged_neighbours():
    assert reputation.hops_to_flagged(RAHUL) is None

def test_unknown_wallet_returns_none():
    assert reputation.hops_to_flagged("0x" + "9" * 40) is None

def test_known_vendor_address():
    assert reputation.known_vendor_address("Acme") == ACME


# --- reputation_check: the function the firewall calls -----------------------

def _tx(to):
    return ProposedTx(to=to, token="USDC", action="transfer", amount=500)


def _policy(payee, addr):
    return Policy(intent_text=f"pay {payee}", allowed_recipients=[addr],
                  max_amount=500, payee_label=payee.lower())


def test_scammer_wallet_fails():
    r = reputation.reputation_check(_tx("0xbad0000000000000000000000000000000000001"),
                                    Policy.deny_all("stranger"))
    assert not r.passed and "flagged scam wallet" in r.reason


def test_rahul_swap_attack_fails():
    r = reputation.reputation_check(_tx(MULE), _policy("Rahul", RAHUL))
    assert not r.passed and "1 hop" in r.reason


def test_fake_invoice_new_address_fails():
    fresh = "0x" + "7" * 40  # not near any scammer, but not Acme's usual address
    r = reputation.reputation_check(_tx(fresh), _policy("Acme", ACME))
    assert not r.passed and "new address for acme" in r.reason


def test_legit_payment_passes():
    r = reputation.reputation_check(_tx(RAHUL), _policy("Rahul", RAHUL))
    assert r.passed and "usual address" in r.reason


def test_graph_down_is_soft_pass(monkeypatch):
    class Broken:
        def hops_to_flagged(self, *a): raise ConnectionError
    monkeypatch.setattr(reputation, "_backend", Broken())
    r = reputation.reputation_check(_tx(RAHUL), _policy("Rahul", RAHUL))
    assert r.passed and not r.hard and "unavailable" in r.reason
