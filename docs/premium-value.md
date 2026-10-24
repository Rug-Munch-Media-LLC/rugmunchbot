# Premium — $5/month: what the subscriber actually buys

The free lanes give a verdict. The $5 tier gives **the evidence behind the
verdict, before it is public** — and nothing that cannot show its work ships.

## The four-block post contract (every premium post)

1. **Proof** — a tx hash, or a wallet address plus the token contract. No
   proof, no post.
2. **Source** — the feed or provider that produced it (`databus.smart_money`,
   Hyperliquid API, Polymarket aggregate book). Aggregate-only sources are
   labelled as such.
3. **Timestamp** — UTC, from the payload itself, never "recently".
4. **Why it matters** — one paragraph of interpretation: what the move
   implies, what would confirm it, and what would invalidate it.

Plus a **risk line** on every copy-trading surface: a large wallet entry can be
a hedge, a transfer or an exit. Copying is not a thesis.

## What is premium-only

| Surface | Free lane | Premium lane |
| --- | --- | --- |
| Whale moves | symbol, direction, size band | full wallet, entity label, wallet P&L, tx hash, entry context |
| Alpha calls | headline + risk | evidence set, counter-case, invalidation level |
| Liquidation clusters | which market | size, venue, leverage context, follow-on level |
| Prediction odds | aggregate price | source spread, liquidity, resolution rule |

## What is never premium (quality gate)

- No on-chain proof, or a truncated address with no hash.
- No named source ("sources say").
- No timestamp, or a timestamp older than the feed window.
- A summary with no "why it matters" — that is a news digest, it belongs free.
- Size below $50k notional: dust moves are not alpha.

These are enforced in `rugmunchbot.support.premium_signal.premium_verdict`
(score ≥ 60, and none of the four blocks missing). A held item is logged with
the missing blocks — the gate explains itself, it never silently drops.

## Referral disclosure

Premium posts carry Jupiter (`referralAccount` + 50 bps) and GMGN
(`rugmunch_<contract>` slug) links, capped at three per post with ONE
disclosure line. Polymarket renders only when `RMI_REF_POLYMARKET` is set;
otherwise the link is `null` with a reason and no placeholder appears.

## Cancellation honesty

$5/month, cancel any time, no lock-in, no "last chance" copy. Refund on
request within 7 days if a single paid week shipped fewer than five
evidence-backed posts — the gate above is the receipt.
