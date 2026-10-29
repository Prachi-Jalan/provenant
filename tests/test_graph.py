"""Graph queries on the local fallback. The same answers must come back from TigerGraph."""

import pytest

from provenant.graph import reputation

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
