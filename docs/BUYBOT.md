# Buy popups (`/buybot`)

Admin-configurable buy cards for **any** token in **any** chat. Parity item P0-2 from
`docs/competitors/rugmunch-better.md`.

## Commands (chat admin / fleet owner only)

| command | effect |
| --- | --- |
| `/buybot` | status card, or the setup card when no token is configured |
| `/buybot token <chain> <address> [symbol]` | set the token (validated by `community.models.TokenConfig`) |
| `/buybot presets 0.1,0.5,1` | buy-amount buttons (shared `AmountPresets`, max 10) |
| `/buybot emoji 🔥 Ape` | buy-button emoji + label |
| `/buybot min <usd>` | minimum buy value rendered |
| `/buybot fields amount,usd,price,mcap,liquidity,risk` | card rows (canonical order, subset allowed) |
| `/buybot buttons chart,scan,holders` | extra action rows |
| `/buybot dedupe <s>` · `/buybot cap <n>` | dedupe window · cards per minute |
| `/buybot_on` / `/buybot_off` | enable / disable (the drop counter is kept) |
| `/buybot_preview [amount]` | render the card with live market rows, no trade |
| `/buybot_template` | show the row/button template |

## Layout

```
src/rugmunchbot/buybot/
  models.py     BuybotConfig (chain derived from the token — never duplicated)
  store.py      Redis: buybot:config:<chat>; reads fail soft, writes fail loud
  ledger.py     dedupe claims + per-minute card cap (injected clock)
  render.py     pure HTML card + button rows (stdlib html.escape, <4096 chars)
  execution.py  SwapService seam — no keys, no signing
  market.py     MarketSnapshot via services.pricing.get_price (single fallback chain)
  commands.py   Telegram-free grammar (parse / apply_intent)
  callbacks.py  buy|<chat>|<mint_ref>|<amount> router, re-validated per tap
  handlers.py   the five commands + registration
```

## Execution honesty

`BuyExecutor` holds **no** private key and signs nothing. With no `SwapService`
installed it returns:

```
ExecutionResult(status="preview", reason="swap service not configured — nothing was signed, sent or settled")
```

so a tap can never look like a settlement. Install an adapter once:

```python
from rugmunchbot.buybot import BuybotState, SwapService
state: BuybotState = app.bot_data["buybot_state"]
state.swap_service(my_adapter)        # only path to "submitted"
```

The adapter implements `async def buy(self, request: BuyRequest) -> str` and returns the
transaction hash; an empty hash or an exception becomes `status="failed"`, never success.

## Callback contract

`buy|<chat_id>|<mint_ref>|<amount>` and `tok|<chat_id>|<mint_ref>|<action>` (≤64 bytes).

* the chat id in the payload must match the message chat;
* the mint prefix must match the **current** config (a stale button cannot touch
  another token);
* the amount must still be one of the presets;
* the tap is claim-gated for `max(15 s, dedupe_window_s)`, so a double tap executes once.

## Storage

`buybot:config:<chat_id>` in Redis, one JSON document per chat. `token`, `amounts` and
`emojis` are the same models `/setup` writes — one source of truth. A missing, corrupt
or unreachable document degrades to **disabled**, never to an exception.
