"""Token DNA fingerprint tests — behavioral similarity matching."""

from rugmunchbot.tools.token_dna import classify, extract_dna


def _hard_rug_scan():
    return {
        "holder_analysis": {"top10_pct": 85, "new_wallets_pct": 72,
                            "exchange_funded_pct": 18},
        "wash_trading": {"score": 65},
        "bundle_analysis": {"launch_cluster_score": 88},
        "flow_analysis": {"top_mover_share_pct": 48},
        "lp_locked_pct": 55,
        "honeypot": True, "mint_auth": True,
    }


def test_hard_rug_matches_hard_centroid():
    dna = extract_dna(_hard_rug_scan())
    assert dna is not None and len(dna) == 8
    result = classify(dna)
    assert result["best_match"]["pattern"] == "HARD_RUG"
    assert result["threat_level"] in ("CRITICAL_MATCH", "HIGH_SIMILARITY")


def test_insufficient_data_returns_none():
    scan = {"holder_analysis": {"top10_pct": 50}}  # only 1 dimension
    assert extract_dna(scan) is None


def test_clean_token_low_similarity():
    scan = {
        "holder_analysis": {"top10_pct": 15, "new_wallets_pct": 5,
                            "exchange_funded_pct": 60},
        "wash_trading": {"score": 95},
        "lp_locked_pct": 100,
    }
    dna = extract_dna(scan)
    result = classify(dna)
    assert result["threat_level"] in ("LOW_RESEMBLANCE", "MODERATE_RESEMBLANCE")


def test_all_8_dimensions_extracted():
    scan = _hard_rug_scan()
    scan["flow_analysis"] = {"top_mover_share_pct": 48}
    dna = extract_dna(scan)
    assert len(dna) == 8
