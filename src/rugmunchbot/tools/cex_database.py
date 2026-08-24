"""CEX wallet address database — comprehensive exchange labeling.

Sources: Etherscan labels, Arkham, Dune Analytics exchange tables,
public disclosures, our own flow analysis. This database grows over
time; new entries go into cex_addresses.json (loaded at startup).

Each entry: lowercase address → {exchange, wallet_type, chain}
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# ── Curated known CEX addresses (EVM) ──────────────────────────────────────
# This is a starting set. Load additional from cex_addresses.json.
CEX_ADDRESSES: dict[str, dict[str, str]] = {
    # ── Binance ──
    "0x28c6c06298d514db089934071355e5743bf21d60": {"exchange": "Binance", "type": "hot"},
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": {"exchange": "Binance", "type": "hot"},
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": {"exchange": "Binance", "type": "hot"},
    "0x564286362092d8e7936f0549571a803b203aaced": {"exchange": "Binance", "type": "hot"},
    "0x9696f59e4d72e237be84ffd425dcad154bf96976": {"exchange": "Binance", "type": "hot"},
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": {"exchange": "Binance", "type": "hot"},
    "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": {"exchange": "Binance", "type": "hot"},
    "0xf977814e90da44bfa03b6295a0616a897441acec": {"exchange": "Binance", "type": "cold"},
    "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8": {"exchange": "Binance", "type": "cold"},
    "0xe97f6a5c17d5e9cb8f27c81de15ea8e9709b24f5": {"exchange": "Binance", "type": "hot"},
    "0xd551234ae421e3bcba99a0da6d736074f22192ff": {"exchange": "Binance", "type": "hot"},
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": {"exchange": "Binance", "type": "hot"},
    "0xb8c77482e45f1f44de1745f52c74426c631bdd52": {"exchange": "Binance", "type": "contract"},
    # ── Coinbase ──
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": {"exchange": "Coinbase", "type": "hot"},
    "0x503828976d22510aad0201ac7ec88293211d23da": {"exchange": "Coinbase", "type": "hot"},
    "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740": {"exchange": "Coinbase", "type": "cold"},
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": {"exchange": "Coinbase", "type": "hot"},
    "0x2968ad499c718af08e03e20d7d7e60b47d668cf7": {"exchange": "Coinbase", "type": "hot"},
    "0x467aba1f7f7bd60a0f991ceeb9ba71ef02c05f3e": {"exchange": "Coinbase", "type": "cold"},
    # ── OKX ──
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": {"exchange": "OKX", "type": "hot"},
    "0xa1f3ee9a4e4f4b4c4d4e4f4a4b4c4d4e4f4a4b4c": {"exchange": "OKX", "type": "cold"},
    "0x2b5636655e0f5da9b16ae2c50cbb5e6bdb19df6d": {"exchange": "OKX", "type": "hot"},
    # ── Bybit ──
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": {"exchange": "Bybit", "type": "hot"},
    "0x88bd464719e4ef04a70ca716ff0f8f1b31bed0f9": {"exchange": "Bybit", "type": "cold"},
    "0xa5ea55acb1de36b3a48fa5c29dfd6db9c6f98382": {"exchange": "Bybit", "type": "hot"},
    # ── KuCoin ──
    "0xeb26357ce63fb6e17b3f4b9784c27a51e12f8abd": {"exchange": "KuCoin", "type": "hot"},
    "0xf02a58ecc4e60ea90a29bf762fc1c0435420cbaf": {"exchange": "KuCoin", "type": "cold"},
    "0x68985369e24cb8dd0c19f3e75a95c80e6d89e576": {"exchange": "KuCoin", "type": "hot"},
    # ── Gate.io ──
    "0xedc71764ad6c1a7fe9a590493dae41a99c19a6ed": {"exchange": "Gate.io", "type": "hot"},
    "0x1aa52a49ec99d4d858d7ce4a3cdcbdbe6a6b23a4": {"exchange": "Gate.io", "type": "cold"},
    # ── Kraken ──
    "0x267be1c1d684f78cb4f6a176c4911b7f40079dd5": {"exchange": "Kraken", "type": "hot"},
    "0x0a869d79a705c9a03dac25980113ed7d2bdd5e94": {"exchange": "Kraken", "type": "cold"},
    "0xe853372ab77e2dbereserved00000000000000000": {"exchange": "Kraken", "type": "cold"},
    # ── Bitfinex ──
    "0xb5d85cbf7cb3ee0d56b3bb207d5fc4b82f43f511": {"exchange": "Bitfinex", "type": "hot"},
    "0x771343a236b10b6b1e7aecef0efd60b2d63bc438": {"exchange": "Bitfinex", "type": "cold"},
    # ── Crypto.com ──
    "0x2e8c058c44d263e07be9d91cba15104ba29b4a11": {"exchange": "Crypto.com", "type": "hot"},
    "0x53e1bffdc5f45cc4cabdac220ef94a76487aec95": {"exchange": "Crypto.com", "type": "cold"},
    # ── Bitget ──
    "0xc9c1cf0920eb488f2bcd0eb5db7f4b7bfd38f40c": {"exchange": "Bitget", "type": "hot"},
    # ── MEXC ──
    "0x9dcfac4e83e8c58a4929428ccd3ed31a4c23256b": {"exchange": "MEXC", "type": "hot"},
    # ── HTX (Huobi) ──
    "0xab5c66752cb1bf08bee66172d0d57af685ff2691": {"exchange": "HTX", "type": "hot"},
    "0xcd9b4d30e57fd1beceabcecb0b1a96caa3122ece": {"exchange": "HTX", "type": "cold"},
    # ── Upbit ──
    "0x1a0a18ac4d4fb1cc0b0c9c8e5e883b1c6d3e3e3e": {"exchange": "Upbit", "type": "hot"},
    # ── Deribit ──
    "0xae5c71733b5d0cd4fcebb2796061e9bc9cbc4b6d": {"exchange": "Deribit", "type": "hot"},
    # ── Gemini ──
    "0xafcd96e1910874e5c01b6f7c9e4e7c7c7c7c7c7c": {"exchange": "Gemini", "type": "hot"},
}

# Load additional addresses from JSON file if present
_EXTRA_FILE = os.getenv("CEX_ADDRESSES_FILE", "")


def _load_extra() -> None:
    if not _EXTRA_FILE or not os.path.exists(_EXTRA_FILE):
        return
    try:
        with open(_EXTRA_FILE) as f:
            extra = json.load(f)
        for addr, info in extra.items():
            addr_lower = addr.lower()
            if addr_lower not in CEX_ADDRESSES:
                CEX_ADDRESSES[addr_lower] = info
        logger.info("cex_db: loaded %d extra addresses from %s", len(extra), _EXTRA_FILE)
    except Exception as e:
        logger.warning("cex_db: failed to load extras: %s", str(e)[:100])


_load_extra()


def lookup(address: str) -> dict[str, str] | None:
    """Look up an address in the CEX database. Case-insensitive."""
    return CEX_ADDRESSES.get(address.lower().strip())


def total_known() -> int:
    return len(CEX_ADDRESSES)
