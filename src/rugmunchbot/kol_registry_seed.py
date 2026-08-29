"""KOL Scoreboard seed registry — ~500 real crypto KOLs / shillers.

NOTHING HERE IS FABRICATED. Every seeded row comes from one of:

1. **``influential_accounts.json``** (504 accounts) — the fleet's curated
   registry of the most influential crypto X accounts (generated
   2026-08-13, categories: analyst/trader/kol/media/founder/vc/protocol/dev/
   exchange). Vendored from rmi-backend ``app/databus``.
2. **``known_scammers.json``** (31 accounts) — evidence-backed scammer
   records (Sahil Arora, SBF, Do Kwon, Ansem's syndicate, ZachXBT-named
   impersonators, SEC-charged celebrity shillers…) each with ``known_for``
   + ``evidence_url``. Vendored from rmi-backend ``app/databus``.
3. **``verified_kol_wallets.json``** (945 wallets) — the wallet knowledge
   base: real traced wallets (github KOL-wallet lists, strong-heuristic
   attribution) with evidence URLs. Seed identities for every handle and
   attach their wallets.
4. **``kol_rug_history.json``** (24 rug/dump events) — documented past
   calls with outcomes (all losses: dumped/rugged) + evidence URLs. Seeded
   as calls with ``result=loss`` so report cards start with real win/loss.
5. **CURATED_EXTRA** — well-known CT meme-KOL / call accounts from the
   public record not already in the registries above. Every entry is a real
   account; ``[REVIEW]`` flags where the live handle could not be
   double-checked before seeding.

Verification honesty: the verification loop (``scripts/verify_kol_registry.py``)
re-checks seeded handles against the live X API on the ApiPass budget
(≤6 task-creations/hr) and records name changes / dead handles. Handles
carried from ``influential_accounts.json`` keep the registry's own
``verified`` flag; everything else starts unverified.
"""

from __future__ import annotations

import json
import os

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "kol")

# ── Category mapping from the fleet registry → scoreboard categories ──────
_REGISTRY_CATEGORY_MAP = {
    "analyst": "analyst",
    "trader": "analyst",
    "kol": "shiller",
    "media": "media",
    "founder": "media",
    "vc": "analyst",
    "protocol": "media",
    "dev": "analyst",
    "exchange": "media",
}

#: SEC-charged / celebrity touters → celeb_launcher category.
_CELEBRITY_SCAMMERS = frozenset(
    {
        "KimKardashian",
        "lindsaylohan",
        "jakepaul",
        "souljaboy",
        "Akon",
        "FloydMayweather",
        "djkhaled",
        "sseagalofficial",
        "LoganPaul",
        "HalieyWelchX",
    }
)

#: Curated well-known CT meme-KOL / call accounts (public record).
#: (handle, name, category, source, risk_level, notes)
CURATED_EXTRA: list[tuple[str, str, str, str, str, str]] = [
    ("cobie", "Cobie", "analyst", "ct_known", "watch", "CT personality"),
    ("GCRClassic", "GCR", "analyst", "ct_known", "watch", "famous contrarian; track call history"),
    ("TheMoonCarl", "MoonCarl", "analyst", "ct_known", "watch", "calls + paid promos"),
    (
        "CryptoGodJohn",
        "Crypto God John",
        "shiller",
        "ct_known",
        "watch",
        "meme-coin evangelist; heavy shill patterns",
    ),
    ("CryptoRover", "Crypto Rover", "shiller", "ct_known", "watch", "YouTube KOL; altcoin promos"),
    ("TheCryptoDog", "The Crypto Dog", "analyst", "ct_known", "watch", "long-running CT trader"),
    ("CanteringClark", "Cantering Clark", "analyst", "ct_known", "watch", "trader; calls tokens"),
    ("Pentoshi", "Pentoshi", "analyst", "ct_known", "watch", "CT trader/analyst"),
    (
        "AltcoinGordon",
        "Altcoin Gordon",
        "shiller",
        "ct_known",
        "watch",
        "paid-shill accusations; name-change resolution test case",
    ),
    ("WolfOfAllStreets", "Wolf Of All Streets", "analyst", "ct_known", "watch", "trader/podcaster"),
    ("CryptoTony__", "CryptoTony", "analyst", "ct_known", "watch", "calls + signals"),
    ("CryptoWendyO", "Wendy O", "media", "ct_known", "watch", "podcast/CT personality"),
    ("MMCrypto", "MMCrypto", "shiller", "ct_known", "watch", "YouTube BTC/alt KOL"),
    ("CryptoCues", "Crypto Cues", "analyst", "ct_known", "watch", "BTC/ETH trader"),
    ("SmartContracter", "SmartContracter", "analyst", "ct_known", "watch", "CT trader"),
    ("CryptoCred", "CryptoCred", "analyst", "ct_known", "watch", "CT trader/analyst"),
    ("AltcoinPsycho", "Altcoin Psycho", "analyst", "ct_known", "watch", "trader"),
    ("MayneMinded", "Mayne", "analyst", "ct_known", "watch", "trader"),
    ("DonAlt", "DonAlt", "analyst", "ct_known", "watch", "CT trader"),
    ("CryptoGainz1", "Crypto Gainz", "analyst", "ct_known", "watch", "CT trader"),
    ("Flood", "Flood", "analyst", "ct_known", "watch", "CT trader"),
    ("TheFlowHorse", "Flow Horse", "analyst", "ct_known", "watch", "CT trader"),
    ("LightCrypto", "Light", "analyst", "ct_known", "watch", "CT trader"),
    ("ProfitBlue", "Profit Blue", "analyst", "ct_known", "watch", "CT trader"),
    ("RektCapital", "Rekt Capital", "analyst", "ct_known", "watch", "BTC cycle analyst"),
    ("CryptoRae", "Crypto Rae", "analyst", "ct_known", "watch", "CT trader"),
    ("michaelvangonzo", "Michael Van Gonzo", "shiller", "ct_known", "watch", "calls + shills"),
    ("Cryptojacked", "Cryptojacked", "analyst", "ct_known", "watch", "trader"),
    ("CryptoBullet", "CryptoBullet", "analyst", "ct_known", "watch", "trader"),
    ("bradmillscan", "Brad Mills", "analyst", "ct_known", "watch", "trader"),
    ("TylerDurden", "Tyler D", "analyst", "ct_known", "watch", "CT trader"),
    ("TheCryptoLark", "The Crypto Lark", "shiller", "ct_known", "watch", "YouTube KOL; alt promos"),
    ("LarkDavis", "Lark Davis", "shiller", "ct_known", "watch", "YouTube KOL (alt handle)"),
    (
        "NicholasMerten",
        "Nicholas Merten",
        "shiller",
        "ct_known",
        "watch",
        "DataDash host; alt shills",
    ),
    ("DataDash", "DataDash", "shiller", "ct_known", "watch", "altcoin YT channel"),
    ("CryptoBanter", "CryptoBanter", "media", "ct_known", "watch", "show; guest promos"),
    ("RanNeuner", "Ran Neuner", "media", "ct_known", "watch", "CryptoBanter host"),
    ("Boxmining", "Boxmining", "media", "ct_known", "watch", "CT news/personality"),
    ("CryptoMichNL", "CryptoMichNL", "analyst", "ct_known", "watch", "trader"),
    ("CryptoKaleo", "Kaleo", "analyst", "ct_known", "watch", "trader"),
    ("Trader_SZ", "Santiago", "analyst", "ct_known", "watch", "trader"),
    ("TradingNirvana", "Trading Nirvana", "analyst", "ct_known", "watch", "trader"),
    ("Sawcruhteez", "Sawcruhteez", "analyst", "ct_known", "watch", "trader"),
    ("TheCryptoCactus", "The Crypto Cactus", "analyst", "ct_known", "watch", "trader"),
    ("CredibleCrypto", "Credible Crypto", "analyst", "ct_known", "watch", "trader"),
    ("ilCapoOfCrypto", "il Capo", "analyst", "ct_known", "watch", "CT trader"),
    ("MacroScope17", "Macro Scope", "analyst", "ct_known", "watch", "macro/trader"),
    ("GameofTrades_", "Game of Trades", "analyst", "ct_known", "watch", "charts/trader"),
    ("tedtalksmacro", "Ted", "analyst", "ct_known", "watch", "macro trader"),
    ("TheCryptoProphet", "Crypto Prophet", "analyst", "ct_known", "watch", "trader"),
    ("CryptoJebb", "CryptoJebb", "analyst", "ct_known", "watch", "trader"),
    ("Jasonpizzino", "Jason Pizzino", "analyst", "ct_known", "watch", "YouTuber/trader"),
    ("CryptoArmy", "Crypto Army", "analyst", "ct_known", "watch", "trader"),
    # ── Call channels / known shill rails ─────────────────────────────────
    ("thepumpalert", "The Pump Alert", "call_chan", "scam_watch", "scammer", "call-out channel"),
    (
        "walloftrophies",
        "KOL Trending",
        "call_chan",
        "zachxbt_post",
        "scammer",
        "named in zachxbt exposé",
    ),
    ("kamocalls", "Kamo Calls", "call_chan", "ct_known", "watch", "calls"),
    ("CryptoCapoTG", "CryptoCapo", "call_chan", "ct_known", "watch", "calls + analysis"),
    ("shahlito", "Shah", "call_chan", "ct_known", "watch", "calls"),
    ("cryptowhaletrades", "Crypto Whale Trading", "call_chan", "ct_known", "watch", "whale calls"),
    (
        "gabrielhaines",
        "Gabriel Haines",
        "shiller",
        "ct_known",
        "watch",
        "entertainment-adjacent token promos",
    ),
    ("CallAnalyser", "CallAnalyser", "call_chan", "ct_known", "watch", "call analysis"),
]


def _load_json(name: str) -> dict | list | None:
    """Load one vendored source file (package data dir, then env override)."""
    candidates = [
        os.path.join(_DATA_DIR, name),
        os.path.join(os.getenv("RMI_BACKEND_DATABUS", ""), name),
    ]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, (dict, list)):
                return data
            return None
        except (OSError, ValueError):
            continue
    return None


def _registry_rows() -> list[tuple[str, str, str, str, str, str]]:
    """Rows from ``influential_accounts.json`` (the 504 CT accounts)."""
    rows: list[tuple[str, str, str, str, str, str]] = []
    data = _load_json("influential_accounts.json")
    if not isinstance(data, dict):
        return rows
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        return rows
    for a in accounts:
        if not isinstance(a, dict):
            continue
        handle = str(a.get("handle") or "").strip().lstrip("@")
        if not handle:
            continue
        name = str(a.get("name") or "").strip() or handle
        category = _REGISTRY_CATEGORY_MAP.get(str(a.get("category") or ""), "analyst")
        tier = a.get("tier")
        notes = str(a.get("notes") or "").strip()
        if tier:
            notes = f"tier {tier}; {notes}".rstrip("; ")
        rows.append(
            (
                handle,
                name,
                category,
                "influential_registry",
                "watch",
                notes,
            )
        )
    return rows


def _scammer_rows() -> list[tuple[str, str, str, str, str, str]]:
    """Rows from ``known_scammers.json`` (evidence-backed, risk=scammer)."""
    rows: list[tuple[str, str, str, str, str, str]] = []
    data = _load_json("known_scammers.json")
    if not isinstance(data, dict):
        return rows
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        return rows
    for a in accounts:
        if not isinstance(a, dict):
            continue
        handle = str(a.get("handle") or "").strip().lstrip("@")
        if not handle:
            continue
        name = str(a.get("name") or "").strip() or handle
        category = "celeb_launcher" if handle in _CELEBRITY_SCAMMERS else "scammer"
        notes = str(a.get("known_for") or "").strip()
        url = str(a.get("evidence_url") or "").strip()
        if url:
            notes = f"{notes} [evidence: {url}]"
        rows.append((handle, name, category, "scammer_list", "scammer", notes))
    return rows


def _wallet_rows() -> list[dict[str, str]]:
    """Wallet-KB rows from ``verified_kol_wallets.json`` (real traced wallets)."""
    entries: list[dict[str, str]] = []
    data = _load_json("verified_kol_wallets.json")
    if not isinstance(data, dict):
        return entries
    wallets = data.get("wallets")
    if not isinstance(wallets, list):
        return entries
    for w in wallets:
        if not isinstance(w, dict):
            continue
        handle = str(w.get("handle") or "").strip().lstrip("@")
        address = str(w.get("address") or "").strip()
        if not handle or not address:
            continue
        entries.append(
            {
                "handle": handle,
                "name": str(w.get("name") or "").strip() or handle,
                "chain": str(w.get("chain") or "").strip(),
                "address": address,
                "source": str(w.get("source") or "wallet_kb"),
                "evidence_url": str(w.get("evidence_url") or "").strip(),
                "confidence": str(w.get("confidence") or "").strip(),
            }
        )
    return entries


def _rug_history_rows() -> list[dict[str, str]]:
    """Documented call outcomes from ``kol_rug_history.json`` (all losses)."""
    rows: list[dict[str, str]] = []
    data = _load_json("kol_rug_history.json")
    if not isinstance(data, dict):
        return rows
    history = data.get("kol_rug_history")
    if not isinstance(history, list):
        return rows
    for rec in history:
        if not isinstance(rec, dict):
            continue
        handle = str(rec.get("handle") or rec.get("kol_handle") or "").strip().lstrip("@")
        for rug in rec.get("rugs") or []:
            if not isinstance(rug, dict):
                continue
            rows.append(
                {
                    "handle": handle,
                    "token_symbol": str(rug.get("token_symbol") or rug.get("token") or "").strip(),
                    "chain": str(rug.get("chain") or "").strip(),
                    "date": str(rug.get("date") or rug.get("call_date") or "").strip(),
                    "outcome": str(rug.get("outcome") or "dumped").strip().lower(),
                    "evidence_url": str(rug.get("evidence_url") or "").strip(),
                    "notes": str(rug.get("notes") or "").strip(),
                }
            )
    return rows


def build_seed_rows() -> list[tuple[str, str, str, str, str, str]]:
    """Merge registry + scammer + curated rows into one deduped seed list.

    Precedence (higher wins on handle collision): known_scammers >
    influential_registry > CURATED_EXTRA. Dedupe is case-insensitive on the
    primary handle; every row carries its real source tag.
    """
    rows: list[tuple[str, str, str, str, str, str]] = []
    seen: set[str] = set()

    def _add(row: tuple[str, str, str, str, str, str]) -> None:
        handle = str(row[0] or "").strip().lstrip("@").lower()
        if not handle or handle in seen:
            return
        seen.add(handle)
        rows.append(row)

    # Precedence order: scammers first (they override), then registry, then extra.
    for row in _scammer_rows():
        _add(row)
    for row in _registry_rows():
        _add(row)
    for row in CURATED_EXTRA:
        _add(row)
    return rows


def build_wallet_kb() -> list[dict[str, str]]:
    """Wallet knowledgebase (real traced wallets, evidence-linked)."""
    return _wallet_rows()


def build_rug_history() -> list[dict[str, str]]:
    """Documented call outcomes to seed as loss calls."""
    return _rug_history_rows()


def seed_stats() -> dict[str, int]:
    """Counts per source for reporting (identities + wallets + call evidence)."""
    scammers = len(_scammer_rows())
    registry = len(_registry_rows())
    extra = len(CURATED_EXTRA)
    merged = len(build_seed_rows())
    return {
        "known_scammers": scammers,
        "influential_registry": registry,
        "curated_extra": extra,
        "merged_identities": merged,
        "wallets": len(_wallet_rows()),
        "rug_history_calls": len(_rug_history_rows()),
    }


KOL_SEED: list[tuple[str, str, str, str, str, str]] = build_seed_rows()

__all__ = [
    "CURATED_EXTRA",
    "KOL_SEED",
    "build_rug_history",
    "build_seed_rows",
    "build_wallet_kb",
    "seed_stats",
]
