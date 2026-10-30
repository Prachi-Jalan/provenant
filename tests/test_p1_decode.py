import pytest

from provenant.chain.decode import (
    MAX_UINT256, decode_erc20, encode_erc20, from_raw, normalize_address, to_raw,
)

RAHUL = "0x62233554069469dbe51c5c09e855f8c9c56570a7"


def test_encode_transfer_known_vector():
    assert encode_erc20("transfer", RAHUL, 1) == (
        "0xa9059cbb"
        + "000000000000000000000000" + RAHUL[2:]
        + "00000000000000000000000000000000000000000000000000000000000f4240"  # 1_000_000
    )


def test_encode_approve_selector():
    assert encode_erc20("approve", RAHUL, 5).startswith("0x095ea7b3")


@pytest.mark.parametrize("action", ["transfer", "approve"])
def test_roundtrip(action):
    tx = decode_erc20(encode_erc20(action, RAHUL, 123.45))
    assert (tx.action, tx.to, tx.token) == (action, RAHUL, "USDC")
    assert tx.amount == pytest.approx(123.45)
    assert RAHUL in tx.decoded


def test_decoded_text_is_human_readable():
    assert decode_erc20(encode_erc20("transfer", RAHUL, 500)).decoded == f"send 500 USDC to {RAHUL}"


def test_unlimited_approve_is_inf():
    tx = decode_erc20(encode_erc20("approve", RAHUL, float("inf")))
    assert tx.action == "approve" and tx.amount == float("inf")
    assert "UNLIMITED" in tx.decoded


def test_huge_but_not_max_approve_is_still_unlimited():
    data = "0x095ea7b3" + RAHUL[2:].rjust(64, "0") + format(2**255, "x").rjust(64, "0")
    assert decode_erc20(data).amount == float("inf")


def test_big_normal_approve_is_not_unlimited():
    assert decode_erc20(encode_erc20("approve", RAHUL, 1_000_000)).amount == 1_000_000


def test_huge_transfer_is_not_flagged_unlimited():
    data = "0xa9059cbb" + RAHUL[2:].rjust(64, "0") + format(2**200, "x").rjust(64, "0")
    tx = decode_erc20(data)
    assert tx.action == "transfer" and tx.amount != float("inf")


def test_unknown_selector_is_other():
    tx = decode_erc20("0xdeadbeef" + "00" * 32)
    assert tx.action == "other" and "unknown" in tx.decoded


def test_transfer_from_is_other_with_destination():
    src = "11" * 20
    data = "0x23b872dd" + src.rjust(64, "0") + RAHUL[2:].rjust(64, "0") + format(5_000_000, "x").rjust(64, "0")
    tx = decode_erc20(data)
    assert (tx.action, tx.to, tx.amount) == ("other", RAHUL, 5)


def test_decode_keeps_context_ids_and_raw():
    data = encode_erc20("transfer", RAHUL, 2)
    tx = decode_erc20(data, context_ids=["tg_1", "tg_2"])
    assert tx.context_ids == ["tg_1", "tg_2"] and tx.raw_calldata == data


def test_decimals_parameter():
    data = encode_erc20("transfer", RAHUL, 1, decimals=18)
    assert int(data[-64:], 16) == 10**18
    assert decode_erc20(data, decimals=18).amount == 1


@pytest.mark.parametrize("bad", ["", "0x", "0xzz", "0xa9059cbb", "0xa9059cbb" + "00" * 10])
def test_decode_rejects_garbage(bad):
    with pytest.raises(ValueError):
        decode_erc20(bad)


def test_decode_accepts_uppercase_and_no_prefix():
    data = encode_erc20("transfer", RAHUL, 3)
    assert decode_erc20(data[2:].upper()).amount == 3


def test_encode_rejects_bad_input():
    for args in [("transfer", RAHUL, float("inf")), ("swap", RAHUL, 1),
                 ("transfer", RAHUL, -1), ("transfer", RAHUL, float("nan"))]:
        with pytest.raises(ValueError):
            encode_erc20(*args)


def test_unlimited_approve_encodes_max_uint():
    assert int(encode_erc20("approve", RAHUL, float("inf"))[-64:], 16) == MAX_UINT256


def test_raw_conversions():
    assert to_raw(1.1) == 1_100_000 and to_raw(0.000001) == 1 and from_raw(2_500_000) == 2.5


@pytest.mark.parametrize("bad", ["0xEVIL", "0x123", "", "hello", "0x" + "g" * 40])
def test_normalize_address_rejects_bad(bad):
    with pytest.raises(ValueError):
        normalize_address(bad)


def test_normalize_address_lowercases():
    assert normalize_address(" 0x" + "AB" * 20 + " ") == "0x" + "ab" * 20
