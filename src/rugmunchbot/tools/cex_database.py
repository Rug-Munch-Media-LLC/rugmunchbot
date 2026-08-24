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
    # ── ON-CHAIN VERIFIED 2026-08-24 ──
    # Each address confirmed via eth_getBalance + eth_getTransactionCount
    # against live blockchain. Only addresses with >100 txs kept.
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": {"exchange": "OKX", "type": "hot"},
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": {"exchange": "Bybit", "type": "hot"},
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": {"exchange": "Binance", "type": "hot"},
    "0x3cd751e6b0078be393132286c442345e5dc49699": {"exchange": "Coinbase", "type": "hot"},
    "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740": {"exchange": "Coinbase", "type": "cold"},
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": {"exchange": "Coinbase", "type": "hot"},
    "0x503828976d22510aad0201ac7ec88293211d23da": {"exchange": "Coinbase", "type": "hot"},
    "0x564286362092d8e7936f0549571a803b203aaced": {"exchange": "Binance", "type": "hot"},
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
