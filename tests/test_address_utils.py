"""Address validation + formatter helpers."""

from rugmunchbot.formatters._base import detect_chain, is_evm, is_sol, short_addr


def test_is_evm_checksum():
    assert is_evm("0x95222290DD7278Aa3Ddd389Cc1E1d165CC4BAfe5")
    assert is_evm("0x000000000000000000000000000000000000dead")


def test_is_evm_rejects_garbage():
    assert not is_evm("not-an-address")
    assert not is_evm("0x123")  # too short


def test_is_sol():
    assert is_sol("So11111111111111111111111111111111111111112")
    assert not is_sol("0x95222290DD7278Aa3Ddd389Cc1E1d165CC4BAfe5")


def test_detect_chain():
    assert detect_chain("0x95222290DD7278Aa3Ddd389Cc1E1d165CC4BAfe5") == "ethereum"
    assert detect_chain("So11111111111111111111111111111111111111112") == "solana"
    assert detect_chain("garbage") == "unknown"


def test_short_addr():
    assert (
        short_addr("0x95222290DD7278Aa3Ddd389Cc1E1d165CC4BAfe5")
        != "0x95222290DD7278Aa3Ddd389Cc1E1d165CC4BAfe5"
    )
    assert len(short_addr("0x95222290DD7278Aa3Ddd389Cc1E1d165CC4BAfe5")) <= 13
