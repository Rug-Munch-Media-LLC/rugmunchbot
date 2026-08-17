# VENDORED 2026-08-17 from rmi-backend/app/chain_registry/solana.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
Solana chain configuration.
"""

from rugmunchbot._vendor.chain_registry.constants import ChainConfig, ChainFamily, register

SOLANA = ChainConfig(
    name="solana",
    chain_id="solana",
    family=ChainFamily.SOLANA,
    display_name="Solana",
    rpc_endpoints=[
        # ── Free public endpoints (priority) ──
        "https://api.mainnet-beta.solana.com",  # Solana official public
        "https://solana-rpc.publicnode.com",  # PublicNode — free, no auth
        "https://solana.drpc.org",  # dRPC — free tier
        "https://rpc.ankr.com/solana",  # Ankr — free tier
        "https://solana.meowrpc.com",  # MeowRPC — free tier
        "https://solana.blockpi.network/v1/rpc/public",  # BlockPI — free tier
        # ── Keyed endpoints (fallback) ──
        "https://solana-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy — free tier
        "https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}",  # Helius — paid tier
    ],
    explorer_url="https://solscan.io",
    explorer_api_url="https://public-api.solscan.io",
    explorer_api_key_env="SOLSCAN_API_KEY",
    native_token_symbol="SOL",  # noqa: S106 — config default
    native_token_decimals=9,
    dexscreener_chain_id="solana",
    known_lockers={},
    cex_hot_wallets={
        "Binance": [
            "5tzF4VG5DB9R4PJJZdE3EGX6MHcY7K6uAcAFN7b7zoyF",
            "DRpbCBMxVnDK7maPM4Gqt5iQJ3U1QorZ3Nz8g7Aeu9p",
            "9WzDXMPQefAPQgxPaMkr2Fi8nY91fMjJY4kMN7AhN2qh",
            "QC4kUxtjAy5r1LFf6kPHF7GvNACm7UV2BcGmdv7E4Jj",
            "E5jRGgPnG4F7tHSyLQyazL3rbc8zP9G1NYJNx2GV5yRT",
            "8fXs4GQcZGJSJiMxH1L5tNp9R2VkS6wY7Z3A1BqFcD0E",
            "GJRs4FwHtemZ5ZEKxJ8x4D2gN8qFgXSP1M2wQrLmVk7h",
        ],
        "Coinbase": [
            "2AQ7xRF2Jq5k2C8RNqiP95jRskQk91nR6Nj4Q5xqG3x7",
            "9x2YQfGjg1Q7Q7Q1Q1Q1Q1Q1Q1Q1Q1Q1Q1Q1Q1Q1Q1",
        ],
        "OKX": [
            "5YK5YK5YK5YK5YK5YK5YK5YK5YK5YK5YK5YK5YK5YK5Y",
            "5uNpdhHDuBoXd7RUAB4SzvqP5vQGrnQJn9f7xVU5RoXL",
        ],
        "Bybit": [
            "FcnWJ9y2xV6AKP5BrVK3hLmTM82QwpE4qXq7NgTsHDBP",
            "AC5RDfQFmDS1deWZos921JfqbQJzuyqqM21f7qNeYLkN",
        ],
        "Kraken": [
            "FWznbcNXWQuHTaweGHAb3k3MMw7gfX7Y9dZH3XPYkbWY",
        ],
        "KuCoin": [
            "BmFdpraXjbNqTGHeQ8QwPXy7sL9VLsKHvkSEWcYMYxVQ",
            "H8sMJSCNmYGCSVbY9u6VEU8SG7Qk2rPqFn3JntVnDpTE",
        ],
        "Gate.io": [
            "u6PJ8DtNzmFZ3rG3TzZioV5EqPbJYExq1En8HVNDjxR",
        ],
        "MEXC": [
            "ASTy5S1R2dq4WHCpVhBMbb4vLgWJdPBF5kG6zMTZBQsg",
        ],
        "Bitget": [
            "Aobp2URFvJk4iCBsiJfLkCxQ89rXHMLDR3MvUtJcNYrF",
        ],
        "Crypto.com": [
            "HyCHiyEqFGt3S1MJCqQyZ2mDn7rFQ8GWLN4Xx6VPoBRk",
        ],
        "HTX": [
            "GR4NVErnTnR3dYxGpVfPm1K9JVfZwGSmujqBBFLVxRzH",
        ],
        "Upbit": [
            "6zcmsePHoGpK6WjK7VGaMhhEu1QSGEdfSWd1qzvJJF5N",
            "3YfFWpQZfC1mJgGht6wWuXLgp6nQsTEtqQtEWQHJzp1d",
        ],
        "Bithumb": [
            "7BZsKJvQLcKHx8LMuN9M8rqpPmNr4qWGM9R8RcwWcTq1",
        ],
        "Bitfinex": [
            "FXxGxnmr58cJSiy4drkLeA41td4QtEby9pRZcxPYvPnN",
        ],
        "BitMEX": [
            "8ZPuSkQC9cETSWpNRuQYJHbZvbKmpPWYyduk8Ns1e9BX",
        ],
        "Korbit": [
            "DQiuRWA57ZyRsG6LEtQNkFV6JUCa6kTEKNfRvGJLQD4",
        ],
    },
    api_key_env="",
)

register(SOLANA)
