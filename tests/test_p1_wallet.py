"""Wallet tests. No network: the live path runs against a fake web3 object."""
from types import SimpleNamespace

import pytest
from eth_account import Account
from web3 import Web3

from provenant.chain import wallet
from provenant.chain.decode import decode_erc20, encode_erc20
from provenant.schemas import ProposedTx

VENDOR = "0x4efaff65b976ee595870b294356295989fb04949"
OTHER = "0x14398951265e89ec25f2744a40ed29f871eabf68"
TEST_KEY = "0x" + "11" * 32  # throwaway key, funds nothing


def tx_to(addr=VENDOR, amount=1.0, action="transfer"):
    return decode_erc20(encode_erc20(action, addr, amount))


# ---- dry run ---------------------------------------------------------------
def test_dry_run_returns_marker_and_needs_no_key():
    assert wallet.send(tx_to()) == "0xDRYRUN"
    assert wallet.send_usdc(VENDOR, 1) == "0xDRYRUN"


def test_explorer_constants():
    assert wallet.EXPLORER == "https://sepolia.basescan.org/tx/"
    assert wallet.explorer_url("0xabc") == "https://sepolia.basescan.org/tx/0xabc"


# ---- what the wallet refuses to sign -----------------------------------------
def test_refuses_other_actions_non_usdc_and_bad_address():
    with pytest.raises(wallet.WalletError):
        wallet.send(ProposedTx(to=VENDOR, token="USDC", action="other", amount=1))
    with pytest.raises(wallet.WalletError):
        wallet.send(ProposedTx(to=VENDOR, token="WETH", action="transfer", amount=1))
    with pytest.raises(ValueError):
        wallet.send(ProposedTx(to="0xEVIL", token="USDC", action="transfer", amount=1))


def test_repaired_tx_with_empty_calldata_is_fine():
    """The firewall's repair sets raw_calldata="" and relies on the wallet to re-encode."""
    tx = ProposedTx(to=VENDOR, token="USDC", action="transfer", amount=3, raw_calldata="")
    assert wallet.send(tx) == "0xDRYRUN"


# ---- live path with a fake web3 -------------------------------------------------
class FakeWeb3:
    class _Eth:
        def __init__(self, outer):
            self.o = outer

        def get_block(self, _):
            return {"baseFeePerGas": 1_000_000}

        def get_transaction_count(self, addr, _state):
            return 3

        def estimate_gas(self, tx):
            self.o.captured["estimate"] = tx
            return 50_000

        def send_raw_transaction(self, raw):
            self.o.captured["raw"] = bytes(raw)
            return SimpleNamespace(hex=lambda: "ab" * 32)  # no 0x prefix, like web3 v6

        def wait_for_transaction_receipt(self, h, timeout):
            return SimpleNamespace(status=self.o.receipt_status)

    def __init__(self, captured, receipt_status=1):
        self.captured = captured
        self.receipt_status = receipt_status
        self.eth = self._Eth(self)

    to_checksum_address = staticmethod(Web3.to_checksum_address)
    to_wei = staticmethod(Web3.to_wei)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    monkeypatch.setenv("OWNER_PRIVATE_KEY", TEST_KEY)
    captured = {}
    w3 = FakeWeb3(captured)
    monkeypatch.setattr(wallet, "_web3", lambda: w3)
    return captured, w3


def test_live_send_signs_with_owner_key_to_usdc_contract(live):
    captured, _ = live
    assert wallet.send(tx_to(amount=2.5)) == "0x" + "ab" * 32
    est = captured["estimate"]
    assert est["to"] == Web3.to_checksum_address(wallet.DEFAULT_USDC_ADDRESS)
    assert (est["value"], est["chainId"], est["nonce"]) == (0, 84532, 3)
    assert decode_erc20(est["data"]).amount == 2.5
    assert Account.recover_transaction(captured["raw"]) == Account.from_key(TEST_KEY).address


def test_wallet_signs_the_fields_not_stale_calldata(live):
    """If raw_calldata disagrees with to/amount (e.g. after a repair), the FIELDS win."""
    captured, _ = live
    tx = tx_to(addr=VENDOR, amount=7)
    tx.raw_calldata = encode_erc20("transfer", OTHER, 999)  # stale / evil
    wallet.send(tx)
    sent = decode_erc20(captured["estimate"]["data"])
    assert sent.to == VENDOR and sent.amount == 7


def test_live_send_reports_reverted_tx(live):
    _, w3 = live
    w3.receipt_status = 0
    with pytest.raises(wallet.WalletError, match="reverted"):
        wallet.send(tx_to())


def test_live_send_without_key_gives_clear_error(live, monkeypatch):
    monkeypatch.delenv("OWNER_PRIVATE_KEY", raising=False)
    with pytest.raises(wallet.WalletError, match="OWNER_PRIVATE_KEY"):
        wallet.send(tx_to())


def test_live_gas_estimate_failure_is_explained(live):
    _, w3 = live

    def boom(tx):
        raise Exception("insufficient funds")

    w3.eth.estimate_gas = boom
    with pytest.raises(wallet.WalletError, match="would fail"):
        wallet.send(tx_to())


# ---- python -m provenant.chain.wallet ------------------------------------------
def test_main_dry_run(monkeypatch, capsys):
    monkeypatch.setenv("VENDOR_ADDRESS", VENDOR)
    assert wallet._main([]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "send 1 USDC" in out and "0xDRYRUN" in out


def test_main_live_prints_basescan_link(monkeypatch, capsys, live):
    monkeypatch.setenv("VENDOR_ADDRESS", VENDOR)
    assert wallet._main([]) == 0
    out = capsys.readouterr().out
    assert "LIVE" in out and "https://sepolia.basescan.org/tx/0x" + "ab" * 32 in out


def test_main_without_vendor_address_fails_cleanly(capsys):
    assert wallet._main([]) == 1
    assert "VENDOR_ADDRESS" in capsys.readouterr().err


def test_main_custom_amount_and_recipient(capsys):
    assert wallet._main(["--to", VENDOR, "--amount", "2.5"]) == 0
    assert "send 2.5 USDC" in capsys.readouterr().out
