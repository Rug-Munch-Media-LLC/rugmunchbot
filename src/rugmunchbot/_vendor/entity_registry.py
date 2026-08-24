# VENDORED 2026-08-24 from rmi-backend/app/entity_registry.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Known Entity Registry - Exchange, DeFi, and Infrastructure address recognition.
Excludes legitimate infrastructure from cluster/bundle analysis.
Integrates CryptoScamDB, Forta, Januus free risk scores.

Paper ref: Article 3, Section 2 - Free Datasets and Labelled-Address Repositories
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# ── Known Exchange Hot Wallets ────────────────────────────
# These are NOT clusters. They're infrastructure.

KNOWN_CEX_WALLETS: dict[str, list[str]] = {
    "binance": [
        # Solana
        "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",  # Binance 1
        "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",  # Binance 2
        "2ojv9BAiHghv5o9y2k6k3m7o9TdsrKJqXvWyCdkUWNmB",  # Binance 3
        # Ethereum
        "0xBE0eB53FC46b790099138e3d32C721856d41e865",  # Binance 7
        "0xF977814e90dA44bFA03b6295A0616a897441aceC",  # Binance 8
        "0x28C6c06298d514Db089934071355E5743bf21d60",  # Binance 14
    ],
    "coinbase": [
        "0x503828976D22510aad0201ac7EC88293211D23Da",  # Coinbase 1
        "0xddfAbCdc4D8fFC17086Ea2cBcebe71504184443C",  # Coinbase 2
        "0x71660c4005BA85c37ccec55d0C4493E66Fe775d3",  # Coinbase 3
    ],
    "kraken": [
        "0x267be1C1D684F78cb4F6a176C4911b741E4Ffdc0",  # Kraken 1
        "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2",  # Kraken 2
    ],
    "okx": [
        "0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b",  # OKX 1
        "0x5c985E89DDe482eFE97ea765eA753005C69C3553",  # OKX 2
    ],
    "bybit": [
        "0xf89d7b9c864f589bbF53a82105107622B35EaA40",  # Bybit
        "0x1Db92e2EeBC8E0c075a02BeA49a2935BcD2dFCF4",  # Bybit 2
    ],
    "kucoin": [
        "0x2B5634C42055806a59e9107ED44D43c426E58258",  # Kucoin 1
        "0x689c56aef474df92d44a1b70850f808488f9769c",  # Kucoin 2
    ],
    "gateio": [
        "0x0D0707963952f2fBA59dD06f2b425ace40b492Fe",  # Gate.io 1
        "0x1c4b70a3968436b9a0a9cf5205c787eb81bb558c",  # Gate.io 2
    ],
    "mexc": [
        "0x75e89d5979E4f6Fba9F97c104c2F0AFB3F1dcB88",  # MEXC 1
    ],
    "huobi": [
        "0x6748F50f686bFb106c8EBd7376a0360768aD1AF3",  # Huobi/HTX 1 (main hot wallet)
    ],
    "crypto.com": [
        "0x6262998ced04146fa42253a5c0af90ca02dfd2a3",  # Crypto.com 1
        "0x46340b20830761efd32832a74d7169b29feb9758",  # Crypto.com 2
        "0x72a53cdbbcc1b9efa39c834aed550584312fba93",  # Crypto.com 3
    ],
    "bitfinex": [
        "0x876EabF441B2EE5bB5b3154DABb9230521bDa862",  # Bitfinex hot wallet
    ],
    "gemini": [
        "0xd24400ae8BfEBb18cA49Be86258a3C749cf46853",  # Gemini hot wallet
    ],
    "ftx": [
        "0xc098b2a3aa256d2140208c3de6543aaef5cd3a94",  # FTX 1
        "0x2faf487A4414Fe77e2327F0bf4AE2a264a776AD2",  # FTX US
    ],
}

# ── Known DeFi Protocol Addresses ─────────────────────────

KNOWN_DEFI_PROTOCOLS: dict[str, list[str]] = {
    "uniswap": [
        "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",  # Uniswap V2 Router
        "0xE592427A0AEce92De3Edee1F18E0157C05861564",  # Uniswap V3 Router
        "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",  # Uniswap V3 Router 2
    ],
    "jupiter": [
        "JUP6LkbZbjS1jKKwapdHNy7bKNkD5Ka6TqPnVGFVeYf",  # Jupiter V6
        "JUP2jS6oQ1gDRPXSbbx5guHt6yKQH8iHWDPmSHRQVk9",  # Jupiter V4
    ],
    "raydium": [
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium V4
    ],
    "orca": [
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  # Orca Whirlpool
    ],
    "pumpfun": [
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # Pump.fun
    ],
    "wormhole": [
        "worm2ZoG2kUd4vFXhvjh93UUH596ayRfgQ2MgjNMTth",  # Wormhole Token Bridge
    ],
}

# ── Known Mixer Addresses ─────────────────────────────────

KNOWN_MIXERS: dict[str, list[str]] = {
    "tornado_cash": [
        "0xA160cdAB225685dA1d56aa342Ad8841c3b53f291",  # Tornado 0.1 ETH
        "0xd90e2f925DA726b50C4Ed8D0Fb90Ad053324F31b",  # Tornado 1 ETH
        "0x12D66f87A04A9E220743712cE6d9bB1B5616B8Fc",  # Tornado 10 ETH
        "0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936",  # Tornado 100 ETH
    ],
}

# ── Known Entity Labels ───────────────────────────────────

ENTITY_TYPE_EXCHANGE = "exchange"
ENTITY_TYPE_DEFI = "defi_protocol"
ENTITY_TYPE_MIXER = "mixer"
ENTITY_TYPE_BRIDGE = "bridge"
ENTITY_TYPE_KNOWN_SCAM = "known_scam"
ENTITY_TYPE_CEX_HOT_WALLET = "cex_hot_wallet"


@dataclass
class EntityMatch:
    address: str
    entity_name: str
    entity_type: str
    source: str  # "internal", "cryptoscamdb", "forta", "goPlus"


class EntityRegistry:
    """Known entity recognition - excludes infrastructure from analysis."""

    def __init__(self) -> None:
        self._exchange_addrs: set[str] = set()
        self._defi_addrs: set[str] = set()
        self._mixer_addrs: set[str] = set()
        self._scam_addrs: set[str] = set()
        self._all_known: dict[str, EntityMatch] = {}
        self._build_index()

    def _build_index(self) -> None:
        """Build unified lookup index from all known sources."""
        # CEX wallets
        for exchange, addrs in KNOWN_CEX_WALLETS.items():
            for addr in addrs:
                key = addr.lower()
                self._exchange_addrs.add(key)
                self._all_known[key] = EntityMatch(
                    address=addr,
                    entity_name=exchange,
                    entity_type=ENTITY_TYPE_CEX_HOT_WALLET,
                    source="internal",
                )

        # DeFi protocols
        for protocol, addrs in KNOWN_DEFI_PROTOCOLS.items():
            for addr in addrs:
                key = addr.lower()
                self._defi_addrs.add(key)
                self._all_known[key] = EntityMatch(
                    address=addr,
                    entity_name=protocol,
                    entity_type=ENTITY_TYPE_DEFI,
                    source="internal",
                )

        # Mixers
        for mixer, addrs in KNOWN_MIXERS.items():
            for addr in addrs:
                key = addr.lower()
                self._mixer_addrs.add(key)
                self._all_known[key] = EntityMatch(
                    address=addr,
                    entity_name=mixer,
                    entity_type=ENTITY_TYPE_MIXER,
                    source="internal",
                )

        # Load from wallet_labels.json if available
        self._load_local_labels()

        logger.info(
            f"Entity registry: {len(self._exchange_addrs)} CEX, "
            f"{len(self._defi_addrs)} DeFi, {len(self._mixer_addrs)} mixers, "
            f"{len(self._scam_addrs)} scams, {len(self._all_known)} total"
        )

    def _load_local_labels(self) -> None:
        """Load address labels from local wallet_labels.json."""
        label_paths = [
            "/root/data/wallet_labels.json",
            "/app/data/wallet_labels.json",
            os.path.expanduser("~/data/wallet_labels.json"),
        ]
        for path in label_paths:
            if not os.path.exists(path):
                continue
            with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                self._process_label_file(path)

    def _process_label_file(self, path: str) -> None:
        """Process a single label file, extracting exchange and scam labels."""
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        for addr, info in data.items():
            if not isinstance(info, dict):
                continue
            labels = info.get("labels", [])
            self._register_exchange_label(addr, labels)
            self._register_scam_labels(addr, labels)

    def _register_exchange_label(self, addr: str, labels: list[Any]) -> None:
        """Register address as an exchange if matching labels exist."""
        if "exchange" not in labels and "cex" not in labels:
            return
        key = str(addr).lower()
        self._exchange_addrs.add(key)
        self._all_known[key] = EntityMatch(
            address=addr,
            entity_name="labeled_cex",
            entity_type=ENTITY_TYPE_EXCHANGE,
            source="local_labels",
        )

    def _register_scam_labels(self, addr: str, labels: list[Any]) -> None:
        """Register address as known scam if labels indicate scam/phishing."""
        for label in labels:
            label_str = str(label).lower()
            if "scam" not in label_str and "phish" not in label_str:
                continue
            key = str(addr).lower()
            self._scam_addrs.add(key)
            self._all_known[key] = EntityMatch(
                address=addr,
                entity_name=str(label),
                entity_type=ENTITY_TYPE_KNOWN_SCAM,
                source="local_labels",
            )

    def classify_address(self, address: str) -> EntityMatch | None:
        """Classify an address as exchange, DeFi, mixer, scam, or unknown."""
        return self._all_known.get(address.lower())

    def is_infrastructure(self, address: str) -> bool:
        """Check if address is known infrastructure (exclude from cluster analysis)."""
        key = address.lower()
        return key in self._exchange_addrs or key in self._defi_addrs or key in self._mixer_addrs

    def is_known_scam(self, address: str) -> bool:
        """Check if address is a known scam."""
        return address.lower() in self._scam_addrs

    def filter_infrastructure(self, addresses: list[str]) -> tuple[list[str], list[EntityMatch]]:
        """Split addresses into legit holders and known infrastructure.
        Returns: (clean_addresses, excluded_entities)"""
        clean = []
        excluded = []
        for addr in addresses:
            match = self.classify_address(addr)
            if match and match.entity_type in (
                ENTITY_TYPE_EXCHANGE,
                ENTITY_TYPE_DEFI,
                ENTITY_TYPE_MIXER,
                ENTITY_TYPE_CEX_HOT_WALLET,
            ):
                excluded.append(match)
            else:
                clean.append(addr)
        return clean, excluded

    def enrich_holder(self, address: str) -> dict[str, Any]:
        """Enrich a holder address with known entity info."""
        match = self.classify_address(address)
        if match:
            return {
                "address": address,
                "entity": match.entity_name,
                "type": match.entity_type,
                "source": match.source,
                "is_infrastructure": match.entity_type != ENTITY_TYPE_KNOWN_SCAM,
            }
        return {"address": address, "entity": None, "type": "unknown"}


# Singleton
_registry: EntityRegistry | None = None


def get_entity_registry() -> EntityRegistry:
    global _registry
    if _registry is None:
        _registry = EntityRegistry()
    return _registry
