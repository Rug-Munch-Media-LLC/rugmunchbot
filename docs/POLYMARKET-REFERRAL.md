# Polymarket content + referral engine — operator runbook

Read-only market/flow intelligence for Polymarket, plus the exact operator steps
for the official referral and builder programs. The engine **never trades, signs,
transfers, or creates markets**, and it **never invents a referral link**.

## 1. Verified program facts (checked 2026-10-24)

| Program | Requirement | Payout | Rules |
| --- | --- | --- | --- |
| Referral | **$10,000 lifetime Polymarket volume** before earning | **10% direct / 5% indirect** of net fees | Attribution lasts **30 days or until the referee reaches Gold**; omnibus and self-referral are banned |
| Builder | Create a builder profile in Polymarket **Settings → Builders** | Builder fees paid to the builder wallet | bytes32 builder code attached to orders; max **100 bps taker / 50 bps maker**; gasless relayer for builder-attributed orders |

Our wallet `0x1E3AC01d0fdb976179790BDD02823196A92705C9` on 2026-10-24:

```
GET https://data-api.polymarket.com/value?user=0x1E3A…05C9
-> [{"user":"0x1e3ac0…05c9","value":0}]
GET https://data-api.polymarket.com/positions?user=0x1E3A…05C9
-> []
GET https://data-api.polymarket.com/activity?user=0x1E3A…05C9
-> []
```

So: **$0 value, zero positions, zero activity.** The engine prints exactly that
(`Our Polymarket wallet … holds $0.00 and has no open positions.`), and prints
`value unavailable` if the public API fails — never a fabricated zero.

## 2. Operator steps — builder profile (do this first)

1. Sign in at `https://polymarket.com` with the account that owns
   `0x1E3AC01d0fdb976179790BDD02823196A92705C9`.
2. Open **Settings → Builders** and create a builder profile (name, logo,
   description).
3. Copy the profile's **bytes32 builder code** (`0x` + 64 hex chars).
4. Set it in the bot env: `POLYMARKET_BUILDER_CODE=0x…`.
5. Restart the bot. Cards then carry the disclosure line, and the value is
   validated (`^0x[0-9a-f]{64}$`) — a malformed code is ignored with a warning.

Notes: attach the code to orders you route yourself. Builder fees accrue to the
builder wallet in the profile; the cap is 100 bps taker / 50 bps maker. Publishing
a builder code is a **fee disclosure**, not a referral.

## 3. Operator steps — referral link

1. Confirm the account has **$10,000 lifetime volume**. Below that, referral
   earnings do not accrue. Volume must be genuine third-party volume — omnibus
   and self-referral are banned and can void the account.
2. On `https://polymarket.com`, open the referral section of the account and copy
   the **generated referral URL** exactly as shown.
3. Set `POLYMARKET_REFERRAL_URL=https://polymarket.com/…` (must be https and a
   `polymarket.com` host; anything else is rejected by `PolySettings`).
4. Restart the bot. Every card then appends:
   `Referral: <url> — we may earn a share of Polymarket fees.`

Do **not** hand-craft a URL with a guessed parameter. If the operator has no
generated link, leave the variable unset — the engine emits no referral line.

## 4. What the engine selects and publishes

* **Markets** (Gamma `/markets`): open markets ranked by 24h volume, liquidity,
  `oneDayPriceChange`, near-resolution bonus; category filter from event tags with a keyword fallback when Gamma omits
  tags; past-end-date markets skipped;
  volume/liquidity/odds-shift floors.
* **Public flow** (Data API `/trades`): taker trades at or above a USD notional
  floor (`size × price`), wallets with a low all-time markets-traded count
  (`/traded`), and same-outcome concentration (≥3 distinct wallets).
* **Wording contract**: "public on-chain evidence only"; no identity claims, no
  accusations, no "insider" framing. Every card carries a risk + not-financial-
  advice disclaimer, a source URL, and a generation timestamp.
* **Dedupe** one card per market per TTL (default 6h) and a minimum post interval
  (default 900s).
* **Dry run by default**: `POLYMARKET_DRY_RUN` unset/true means no publish call is
  ever made; drafts are returned/logged only.

## 5. Commands

```bash
# dry-run one cycle (no network writes, no publishing)
python scripts/polymarket_dryrun.py

# tests for the whole package
python -m pytest tests/test_polymarket_*.py -v
```

## 6. Blocker status

| Item | Status |
| --- | --- |
| Builder profile + bytes32 code | **Operator action** — not created; `POLYMARKET_BUILDER_CODE` unset |
| Referral link | **Blocked on $10k lifetime volume** — wallet has $0; `POLYMARKET_REFERRAL_URL` unset |
| Trading / signing | Not implemented by design — out of scope for this engine |
