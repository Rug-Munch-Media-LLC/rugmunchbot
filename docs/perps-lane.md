# Perps desk lane (GMGN perps CTA)

Daily recap card: funding, open interest and 10x liquidation proximity for
BTC / ETH / SOL, plus a single GMGN perps call-to-action.

- Data: our multi-venue aggregator (`/api/v1/market/liquidations-multi-venue`),
  consumed through `tools.cascade_predictor.cascade_watch` and
  `tools.funding_dislocation.funding_dislocation`.
- Card: `channels/perps_card.py` — data rows, one commentary paragraph, risk
  disclaimer, referral disclosure, **one link maximum**.
- Lane: `channels/perps_recap.py` — fail-open; no data, no post.
- Referral: `referrals/_gmgn_perps.py` — `url=None` + `reason` whenever the
  market is unverified or the code is unconfigured.
- Schedule: `channel_perps_recap`, daily `RMI_PERPS_RECAP_HOUR_UTC` (default 07:00 UTC)
  to the alpha channel; disable with `RMI_PERPS_RECAP_ENABLED=false`.

## Configuration

The referral code is **not** in source. Set one of:

```bash
export RMI_REF_GMGN_CODE=rugmunch                                  # slug form
export RMI_REF_GMGN=https://gmgn.ai/perpetual/BTC.PERP_USDC?ref=... # full override
```

## Verified markets

`BTC`, `ETH`, `SOL`, `ZEC` vs USDC (GMGN perps public beta, 2026-10). URL shape:
`https://gmgn.ai/perpetual/{SYMBOL}.PERP_USDC?ref={code}`. Any other symbol is
refused with a reason — the lane never invents a market.

## Rebate terms — [REVIEW]

The GMGN programme advertises a 10–30% share of trading fees. **Perps rebate
terms are not separately documented**, so this lane asserts no rate: copy says
only that a fee may be earned. Do not add a percentage to the card until GMGN
publishes perps-specific terms.
