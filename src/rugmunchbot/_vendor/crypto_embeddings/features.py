# VENDORED 2026-08-23 from rmi-backend/app/crypto_embeddings/features.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Crypto-aware feature extractors for smart contracts, transactions, and wallets."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Any

import numpy as np


def extract_contract_features(code: str) -> np.ndarray:
    """
    Extract structural features from smart contract code.
    Returns a 128-dim vector with rug pattern indicators, complexity metrics, etc.
    """
    vec = np.zeros(128, dtype=np.float32)
    if not code or len(code) < 10:
        return vec

    code_lower = code.lower()

    # ── Rug pull keyword patterns (first 32 dims) ──
    rug_patterns = [
        "maxTx",
        "maxtransaction",
        "_maxTxAmount",
        "maxSell",
        "maxBuy",
        "tradingEnabled",
        "tradingActive",
        "enableTrading",
        "isTradingEnabled",
        "blacklist",
        "isBlacklisted",
        "_blacklist",
        "addToBlacklist",
        "mint",
        "onlyOwner",
        "excludeFromFees",
        "excludeFromReward",
        "setTax",
        "updateTax",
        "setFee",
        "taxFee",
        "liquidityFee",
        "sweep",
        "drain",
        "rug",
        "pullLiquidity",
        "removeLiquidity",
        "manualSwap",
        "manualSend",
        "swapAndLiquify",
        "upgradeTo",
        "upgradeToAndCall",
        "changeAdmin",
        "selfdestruct",
        "suicide",
        "delegatecall",
        "transferOwnership",
        "renounceOwnership",
        "lock",
    ]
    for i, pattern in enumerate(rug_patterns):
        if pattern in code_lower:
            vec[i] = 1.0

    # ── Structural complexity (dims 32-40) ──
    lines = code.count("\n") + 1
    vec[32] = min(lines / 5000.0, 1.0)
    vec[33] = min(code.count("function ") / 100.0, 1.0)
    vec[34] = min(code.count("require(") / 50.0, 1.0)
    vec[35] = min(code.count("modifier ") / 20.0, 1.0)
    vec[36] = min(code.count("event ") / 30.0, 1.0)
    vec[37] = min(code.count("assembly") / 10.0, 1.0)
    vec[38] = min(code.count("for (") + code.count("while ("), 1.0) / 20.0
    vec[39] = min(code.count("if (") / 100.0, 1.0)

    # ── Library/import fingerprint (dims 40-50) ──
    imports = [
        "openzeppelin",
        "uniswap",
        "pancake",
        "sushiswap",
        "chainlink",
        "@openzeppelin",
        "@uniswap",
        "erc20",
        "erc721",
        "ownable",
    ]
    for i, imp in enumerate(imports):
        vec[40 + i] = 1.0 if imp in code_lower else 0.0

    # ── Language fingerprint (dims 50-55) ──
    lang_markers = {
        "solidity": ["pragma solidity", "contract ", "address public"],
        "rust": ["fn ", "pub fn", "struct ", "impl ", "#[account]"],
        "move": ["module ", "public fun", "struct ", "acquires"],
    }
    idx = 50
    for _lang, markers in lang_markers.items():
        score = sum(1 for m in markers if m in code)
        vec[idx] = min(score / len(markers), 1.0)
        idx += 1

    # ── Hash-based fingerprint (dims 56-127) ──
    code_hash = hashlib.sha256(code.encode()).digest()
    for i in range(72):
        vec[56 + i] = (code_hash[i % 32] / 255.0) if i < 72 else 0.0

    return vec


def extract_transaction_features(tx_data: dict[str, Any]) -> np.ndarray:
    """Extract behavioral features from transaction history. Returns 64-dim vector."""
    vec = np.zeros(64, dtype=np.float32)
    txs = tx_data.get("transactions", []) if isinstance(tx_data, dict) else []
    if not txs:
        return vec

    amounts = []
    timestamps = []
    counterparties = set()
    programs = set()
    for tx in txs:
        amt = float(tx.get("amount", 0) or 0)
        ts = tx.get("timestamp", 0) or tx.get("block_time", 0)
        if amt > 0:
            amounts.append(amt)
        if ts:
            timestamps.append(int(ts))
        cp = tx.get("counterparty") or tx.get("from") or tx.get("to")
        if cp:
            counterparties.add(str(cp))
        prog = tx.get("program") or tx.get("instruction")
        if prog:
            programs.add(str(prog))

    if amounts:
        arr = np.array(amounts, dtype=np.float32)
        vec[0] = np.log1p(float(np.mean(arr))) / 20.0
        vec[1] = min(float(np.std(arr)) / (float(np.mean(arr)) + 1), 1.0)
        vec[2] = min(float(np.max(arr)) / (float(np.sum(arr)) + 1), 1.0)
        vec[3] = min(len(amounts) / 1000.0, 1.0)
        if len(amounts) > 2:
            vec[4] = min(float(np.sum(arr[:5])) / (float(np.sum(arr)) + 1), 1.0)

    if len(timestamps) > 1:
        ts_sorted = sorted(timestamps)
        gaps = np.diff(ts_sorted)
        vec[10] = min(float(np.mean(gaps)) / 3600.0, 1.0)
        vec[11] = min(float(np.std(gaps)) / 3600.0, 1.0)
        hours = [datetime.fromtimestamp(t).hour for t in ts_sorted]
        hour_counts = np.bincount(hours, minlength=24)
        hour_probs = hour_counts / (hour_counts.sum() + 1)
        entropy = -np.sum(hour_probs * np.log(hour_probs + 1e-10))
        vec[12] = min(entropy / 3.2, 1.0)
        window = max(1, (ts_sorted[-1] - ts_sorted[0]) // 10)
        burst_txs = sum(1 for i in range(len(ts_sorted) - 1) if ts_sorted[i + 1] - ts_sorted[i] < max(60, window))
        vec[13] = min(burst_txs / max(1, len(ts_sorted)), 1.0)

    vec[20] = min(len(counterparties) / 200.0, 1.0)
    vec[21] = min(len(programs) / 50.0, 1.0)
    vec[22] = min(len(counterparties) / max(1, len(txs)), 1.0)
    tokens_seen = set()
    for tx in txs:
        for field in ["token", "mint", "token_address", "contract"]:
            if field in tx:
                tokens_seen.add(str(tx[field]))
    vec[23] = min(len(tokens_seen) / 100.0, 1.0)

    risk = tx_data.get("risk_signals", {}) if isinstance(tx_data, dict) else {}
    vec[30] = 1.0 if risk.get("mixer_interaction") else 0.0
    vec[31] = 1.0 if risk.get("sanctioned_counterparty") else 0.0
    vec[32] = 1.0 if risk.get("flash_loan_usage") else 0.0
    vec[33] = min(risk.get("wash_trading_score", 0) / 100.0, 1.0)
    vec[34] = 1.0 if risk.get("sandwich_attack_target") else 0.0
    vec[35] = 1.0 if risk.get("new_wallet", True) else 0.0
    vec[36] = min(risk.get("failed_tx_ratio", 0), 1.0)
    vec[37] = min(risk.get("rug_pull_pattern_score", 0) / 100.0, 1.0)

    cp_sorted = sorted(counterparties)[:50]
    cp_hash = hashlib.sha256(",".join(cp_sorted).encode()).digest()
    for i in range(24):
        vec[40 + i] = cp_hash[i] / 255.0

    return vec


def extract_wallet_features(wallet_data: dict[str, Any]) -> np.ndarray:
    """Extract wallet entity features for clustering/scam detection. Returns 64-dim vector."""
    vec = np.zeros(64, dtype=np.float32)
    if not wallet_data:
        return vec

    created = wallet_data.get("created_at") or wallet_data.get("first_seen")
    if created:
        try:
            if isinstance(created, str):
                created_ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            else:
                created_ts = float(created)
            age_days = max(0, (time.time() - created_ts) / 86400)
            vec[0] = min(age_days / 365.0, 1.0)
            vec[1] = 1.0 if age_days < 7 else 0.0
            vec[2] = 1.0 if age_days < 30 else 0.0
        except (ValueError, TypeError):
            vec[0] = 0.1

    balance = float(wallet_data.get("balance_usd", 0) or 0)
    vec[10] = min(np.log1p(balance) / 15.0, 1.0)
    txn_count = int(wallet_data.get("transaction_count", 0) or 0)
    vec[11] = min(txn_count / 10000.0, 1.0)
    vec[12] = min(float(wallet_data.get("avg_txn_value_usd", 0) or 0) / 100000.0, 1.0)

    labels = wallet_data.get("labels", []) or []
    risk_tags = {
        "scammer": 20,
        "rug_puller": 21,
        "honeypot_creator": 22,
        "wash_trader": 23,
        "mixer_user": 24,
        "exploiter": 25,
        "sanctioned": 26,
        "phisher": 27,
        "insider": 28,
        "bot": 29,
    }
    for label in labels:
        label_lower = str(label).lower().replace(" ", "_")
        for tag, dim in risk_tags.items():
            if tag in label_lower:
                vec[dim] = 1.0

    cluster = str(wallet_data.get("cluster_id", ""))
    if cluster:
        ch = hashlib.sha256(cluster.encode()).digest()
        for i in range(10):
            vec[30 + i] = ch[i] / 255.0

    chains = wallet_data.get("active_chains", []) or []
    chain_list = ["ethereum", "solana", "base", "bsc", "polygon", "arbitrum", "optimism", "avalanche", "fantom", "tron"]
    for i, chain_name in enumerate(chain_list):
        if chain_name in [c.lower() for c in chains]:
            vec[40 + i] = 1.0

    assoc = str(wallet_data.get("associated_addresses", ""))
    ah = hashlib.sha256(assoc.encode()).digest()
    for i in range(14):
        vec[50 + i] = ah[i] / 255.0

    return vec
