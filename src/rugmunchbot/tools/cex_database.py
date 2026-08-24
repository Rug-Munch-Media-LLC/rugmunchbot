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
from typing import Any

logger = logging.getLogger(__name__)

# ── Curated known CEX addresses (EVM) ──────────────────────────────────────
# This is a starting set. Load additional from cex_addresses.json.
CEX_ADDRESSES: dict[str, dict[str, Any]] = {}

# Load the verified dataset (376 addresses across 20 exchanges)
# Source: DataDr69/labeled_ethereum_addresses_dataset (Etherscan labels)
_DATASET_PATH = os.path.join(
    os.path.dirname(__file__), "cex_addresses_verified.json"
)


def _load_cex_database() -> None:
    """Load CEX addresses from the verified JSON dataset."""
    try:
        with open(_DATASET_PATH) as f:
            data = json.load(f)
        for addr, info in data.items():
            CEX_ADDRESSES[addr.lower()] = info
    except FileNotFoundError:
        logger.warning("cex_database file not found: %s", _DATASET_PATH)
    except json.JSONDecodeError as e:
        logger.warning("cex_database JSON parse error: %s", str(e)[:100])


_load_cex_database()

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
