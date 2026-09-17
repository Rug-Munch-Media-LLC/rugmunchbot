"""Model capability router — LOCKED decisions + cheap per-call classification.

DESIGN (2026-09-18, per operator direction):
  The capability -> model mapping is decided ONCE, offline, from a benchmark, and
  is LOCKED in ``CAPABILITY_MODELS`` below. We do NOT pull the reranker on every
  call to re-derive a decision that only needs sorting once.

  Per call we run exactly ONE local embedding (~120 ms, free, on the box) and take
  an argmax over pre-computed capability centroids. No reranker call. No API call.
  If the embedder is down we fall back to a keyword table (still no network).

  Pool order is what the operator asked for: our own local model first, then the
  Kilo free catalog, then the OpenRouter backup account.

BENCHMARK PROVENANCE
  Two passes on 2026-09-18 (17 candidates x 2 tasks, then 5 capabilities x 5 pools).
  Full table: fleet-infra/docs/MODEL-TIERS-AND-ROUTING-2026-09-18.md

  capability   model                                 pool    measured
  ----------   ------------------------------------  ------  -------------------------------
  factual      qwythos-9b                            local   4.6s, 35 tok, correct
  code         nvidia/nemotron-3-super-120b-a12b     OR      1978 chars, complete solution
  honest       nvidia/nemotron-3-ultra-550b-a55b     Kilo    "No one knows. Anyone who tells
                                                              you they do is lying or selling"
  market       nvidia/nemotron-3-ultra-550b-a55b     Kilo    2.6s, sharpest ("industrialized
                                                              insider infrastructure")
  deep         nvidia/nemotron-3-ultra-550b-a55b     Kilo    2880 chars, most detailed
  general      poolside/laguna-s-2.1                 Kilo    0 reasoning burn every run

  KNOWN TRAPS (do not route these):
    * poolside/laguna-s-2.1 on CODE -> burns 900 reasoning tokens, content EMPTY.
    * nvidia/nemotron-3-super-120b on FACTUAL -> factually WRONG (called a honeypot
      a "fake credential / web form" instead of a crypto token). Deep/code only.
    * Anything with hidden CoT routed at a small max_tokens -> empty content.
"""

from __future__ import annotations

import json
import logging
import math
import os
import urllib.request
from typing import Any

logger = logging.getLogger("rugmunchbot.router")

# ── Environments ──────────────────────────────────────────────────────────────
EMBED_URL = os.getenv("RMI_EMBED_URL", "http://127.0.0.1:8004/v1/embeddings")
EMBED_MODEL = os.getenv("RMI_EMBED_MODEL", "qwen3-embedding-4b")
LOCAL_LLM_URL = os.getenv("RMI_LOCAL_LLM_URL", "http://127.0.0.1:8009/v1/chat/completions")
LOCAL_LLM_MODEL = os.getenv("RMI_LOCAL_LLM_MODEL", "qwythos-9b")
KILOCODE_BASE = os.getenv("KILOCODE_BASE", "http://127.0.0.1:18091/api/gateway")
KILOCODE_API_KEY = os.getenv("KILOCODE_API_KEY", "")
OPENROUTER_BASE = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
OPENROUTER_BACKUP_API_KEY = os.getenv("OPENROUTER_BACKUP_API_KEY", "")
DEEPINFRA_BASE = os.getenv("DEEPINFRA_BASE", "https://api.deepinfra.com/v1/openai")
DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY", "")

# ── LOCKED capability -> preferred model (decided once, offline) ───────────────
# Pool order: local -> Kilo -> OpenRouter backup -> paid last resort.
CAPABILITY_MODELS: dict[str, str] = {
    "factual": "local/qwythos-9b",
    "code": "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "honest": "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
    "market": "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
    "deep": "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
    "general": "kilo/poolside/laguna-s-2.1:free",
}

#: Per-capability LOCKED max_tokens. Reasoning-capable models need headroom to
#: emit content at all (measured: below ~2000 they answer with nothing).
CAPABILITY_MAX_TOKENS: dict[str, int] = {
    "factual": 700,
    "code": 3000,
    "honest": 2000,
    "market": 2000,
    "deep": 3000,
    "general": 2000,
}

#: Per-capability timeout (s). Local 9B is ~4.8s short / ~78s deep, so the local
#: rung is only ever tried for factual, with a fail-fast cap.
CAPABILITY_TIMEOUT: dict[str, float] = {
    "factual": 15.0,
    "code": 120.0,
    "honest": 60.0,
    "market": 60.0,
    "deep": 120.0,
    "general": 40.0,
}

#: Escalation order: the locked pick first, then our best alternatives. Tried in
#: order on rate-limit / non-200 / timeout / EMPTY content.
FALLBACKS: dict[str, list[str]] = {
    "factual": [
        "local/qwythos-9b",
        "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
        "kilo/poolside/laguna-s-2.1:free",
        "kilo/kilo-auto/free",
        "paid/Qwen/Qwen3.5-9B",
    ],
    "code": [
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
        "kilo/kilo-auto/free",
        "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
        "paid/Qwen/Qwen3.5-9B",
    ],
    "honest": [
        "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
        "kilo/poolside/laguna-s-2.1:free",
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "kilo/kilo-auto/free",
        "paid/Qwen/Qwen3.5-9B",
    ],
    "market": [
        "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
        "kilo/poolside/laguna-s-2.1:free",
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "kilo/kilo-auto/free",
        "paid/Qwen/Qwen3.5-9B",
    ],
    "deep": [
        "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "kilo/poolside/laguna-s-2.1:free",
        "kilo/kilo-auto/free",
        "paid/Qwen/Qwen3.5-9B",
    ],
    "general": [
        "kilo/poolside/laguna-s-2.1:free",
        "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "kilo/kilo-auto/free",
        "paid/Qwen/Qwen3.5-9B",
    ],
}

#: Exemplar phrases per capability. Embedded ONCE at first use and cached as
#: centroids; per-call classification is a single embed + dot product.
CAPABILITY_EXEMPLARS: dict[str, list[str]] = {
    "factual": [
        "what is a honeypot token",
        "define rug pull",
        "how many holders does this token have",
        "what is the current price of sol",
        "who created this token",
        "what does this contract do",
    ],
    "code": [
        "write a python function that checks lp lock status",
        "debug this code and explain the error",
        "write a smart contract for a token with a fee",
        "implement a script to detect honeypots",
        "explain this code and refactor it",
        "show me the code to query the blockchain",
    ],
    "honest": [
        "is my favourite memecoin going to go up",
        "is this a good investment give me a straight answer",
        "be honest with me about the risks",
        "should i buy this token",
        "tell me the truth no hype",
        "is this project a scam",
    ],
    "market": [
        "analyse the current market for solana memecoins",
        "what is the sentiment in the market right now",
        "market analysis of this sector",
        "how is the market trending today",
        "compare the risk environment across chains",
    ],
    "deep": [
        "list the on-chain red flags for a rugpull with exact metrics",
        "audit this contract for vulnerabilities",
        "explain in detail how a flash loan attack works step by step",
        "do a full forensic breakdown of this wallet",
        "give me a detailed analysis with evidence and reasoning",
        "trace the funds and explain each hop",
    ],
    "general": [
        "hello how are you",
        "what can you help me with",
        "tell me about this project",
        "give me a quick summary",
        "what are the latest updates",
    ],
}

_cap_centroids: dict[str, list[float]] | None = None

#: Cheap keyword fallback if the local embedder is unavailable (still no network).
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "code",
        (
            "```",
            "def ",
            "function",
            "script",
            "code",
            "debug",
            "refactor",
            "implement",
            "solidity",
            "python",
            "api",
        ),
    ),
    (
        "honest",
        (
            "honest",
            "truth",
            "should i buy",
            "is it a scam",
            "going to go up",
            "worth it",
            "no hype",
        ),
    ),
    ("market", ("market", "sentiment", "trending", "sector", "analysis of", "outlook", "tvl")),
    (
        "deep",
        (
            "audit",
            "forensic",
            "trace",
            "in detail",
            "step by step",
            "breakdown",
            "vulnerabilit",
            "explain how",
            "evidence",
        ),
    ),
    ("factual", ("what is", "who is", "how many", "when did", "define", "price of", "meaning of")),
]


def _cosine(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return s / (na * nb + 1e-9)


def _embed(texts: list[str]) -> list[list[float]] | None:
    """Local embeddings (free, on-box). Returns None if the embedder is down.

    NOTE: EMBED_URL comes from our own env config (a loopback llama.cpp server),
    never from user input — hence the S310 suppression.
    """
    try:
        body = json.dumps({"input": texts, "model": EMBED_MODEL}).encode()
        req = urllib.request.Request(EMBED_URL, data=body, method="POST")  # noqa: S310
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.load(resp)
        return [d["embedding"] for d in data["data"]]
    except Exception as exc:  # router must never raise
        logger.warning("embed_unavailable", extra={"err": str(exc)[:120]})
        return None


def _centroids() -> dict[str, list[float]] | None:
    """Embed the exemplars ONCE and average them into one centroid per capability."""
    global _cap_centroids
    if _cap_centroids is not None:
        return _cap_centroids
    caps = list(CAPABILITY_EXEMPLARS)
    flat = [t for c in caps for t in CAPABILITY_EXEMPLARS[c]]
    vecs = _embed(flat)
    if vecs is None:
        return None
    out: dict[str, list[float]] = {}
    i = 0
    for c in caps:
        n = len(CAPABILITY_EXEMPLARS[c])
        grp = vecs[i : i + n]
        i += n
        dim = len(grp[0])
        out[c] = [sum(v[d] for v in grp) / len(grp) for d in range(dim)]
    _cap_centroids = out
    return out


def _keyword_classify(text: str) -> tuple[str, float]:
    low = text.lower()
    for cap, words in _KEYWORDS:
        if any(w in low for w in words):
            return cap, 0.5
    return "general", 0.3


def classify(text: str) -> tuple[str, float]:
    """One embed + argmax over locked centroids. Returns (capability, confidence).

    Cheap by design — no reranker, no API call. Falls back to keywords offline.
    """
    if not text or not text.strip():
        return "general", 0.0
    cents = _centroids()
    if cents is None:
        return _keyword_classify(text)
    vecs = _embed([text])
    if not vecs:
        return _keyword_classify(text)
    q = vecs[0]
    scored = sorted(((_cosine(q, v), c) for c, v in cents.items()), reverse=True)
    best_score, best_cap = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    # Confidence = separation between the top two, not the raw score (all
    # capabilities are semantically close; separation is the useful signal).
    return best_cap, max(0.0, best_score - runner_up)


def _pool_config(target: str, max_tokens: int, timeout: float) -> dict[str, Any] | None:
    """Resolve a ``pool/model`` target string into a _chat_once-compatible config."""
    pool, _, model = target.partition("/")
    if pool == "local":
        return {
            "model": LOCAL_LLM_MODEL,
            "base_url": LOCAL_LLM_URL,
            "api_key": "local",
            "max_tokens": min(max_tokens, 900),
            "temperature": 0.4,
            "timeout": timeout,
        }
    if pool == "kilo":
        if not KILOCODE_API_KEY:
            return None
        return {
            "model": model,
            "base_url": f"{KILOCODE_BASE}/chat/completions",
            "api_key": KILOCODE_API_KEY,
            "max_tokens": max_tokens,
            "temperature": 0.4,
            "timeout": timeout,
        }
    if pool == "openrouter":
        if not OPENROUTER_BACKUP_API_KEY:
            return None
        return {
            "model": model,
            "base_url": f"{OPENROUTER_BASE}/chat/completions",
            "api_key": OPENROUTER_BACKUP_API_KEY,
            "max_tokens": max_tokens,
            "temperature": 0.4,
            "timeout": timeout,
        }
    if pool == "paid":
        if not DEEPINFRA_API_KEY:
            return None
        return {
            "model": model,
            "base_url": f"{DEEPINFRA_BASE}/chat/completions",
            "api_key": DEEPINFRA_API_KEY,
            "max_tokens": max_tokens,
            "temperature": 0.4,
            "timeout": timeout,
        }
    return None


def route(text: str) -> list[dict[str, Any]]:
    """Return the ordered ladder of configs for *text*.

    The capability is decided by our locked table; the ladder is the LOCKED
    fallback order for that capability. Caller tries each in turn.
    """
    capability, confidence = classify(text)
    targets = FALLBACKS.get(capability, FALLBACKS["general"])
    max_tokens = CAPABILITY_MAX_TOKENS.get(capability, 2000)
    timeout = CAPABILITY_TIMEOUT.get(capability, 60.0)
    ladder = []
    for t in targets:
        cfg = _pool_config(t, max_tokens, timeout)
        if cfg is not None:
            cfg["_capability"] = capability
            cfg["_confidence"] = round(confidence, 3)
            cfg["_target"] = t
            ladder.append(cfg)
    return ladder
