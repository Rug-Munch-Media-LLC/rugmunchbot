# `tools/` — data files that are NOT in this repository

This directory holds bot tooling. One file that used to live here does **not** belong in
the public repository: `src/rugmunchbot/tools/cex_addresses_verified.json`.

## `cex_addresses_verified.json` — private corpus asset

| fact | value |
|---|---|
| size | ~34 MB |
| records | 208,488 addresses |
| key | `0x` + 40 hex characters (lowercase-normalised on load) |
| value fields | `exchange`, `name_tag`, `type`, `tx_count`, `balance_eth` |
| upstream | `DataDr69/labeled_ethereum_addresses_dataset` (Etherscan labels), independently verified |
| licence | none declared upstream — treat as redistributable-restricted |

It is exchange/KOL attribution intelligence. It is **not** source code, it has no
redistribution licence, and it is 34 MB of build weight on every clone. It now lives in the
private corpus, not here.

## Where it lives now

The dataset is stored outside this repository, in the fleet private corpus store. Do not
re-add it here under any name, and do not commit a derivative (a trimmed copy, a subset, a
re-export, a compressed archive) — a subset is the same intel.

## How to fetch it (credentials required)

Access requires fleet credentials. Do not bake them into the repo, a script, a Dockerfile,
or a CI workflow — read them from the secret manager at runtime.

```bash
# 1. credentials from the secret manager (never committed, never echoed)
export CORPUS_TOKEN="$(gopass show -o <corpus-entry>/token)"

# 2. fetch into the runtime path the loader expects
curl -fsSL -H "Authorization: token ${CORPUS_TOKEN}" \
  "<private-corpus-base-url>/rugmunchbot/cex_addresses_verified.json" \
  -o src/rugmunchbot/tools/cex_addresses_verified.json

# 3. sanity check — the loader expects a JSON object keyed by address
python3 -c "import json,sys; d=json.load(open('src/rugmunchbot/tools/cex_addresses_verified.json')); print(len(d),'addresses'); sys.exit(0 if len(d)>200000 else 1)"
```

The exact corpus URL and credential entry are recorded in the fleet secret inventory, not
in this file.

## Runtime behaviour when the file is absent

`cex_database.py` already handles absence gracefully:

- `FileNotFoundError` → logs `cex_database file not found: …` and continues with the
  built-in `CEX_ADDRESSES` seed set. No crash, no traceback.
- `json.JSONDecodeError` → logs a truncated parse error and continues.

So a clone without the dataset still starts; attribution coverage is simply reduced.

## Override without touching the repo

`cex_database.py` supports `CEX_ADDRESSES_FILE`, an optional extra dataset merged on top of
the built-in seed set (existing keys win):

```bash
CEX_ADDRESSES_FILE=/srv/secrets/corpus/cex_addresses_verified.json
```

Point that at the private corpus copy — it keeps the working tree clean and survives
`git pull` without a conflict.

## Rules

- **Never commit** `cex_addresses_verified.json` or any subset/derivative of it.
- **Never** print address labels, exchange attribution or `name_tag` values into logs,
  tickets, PRs or agent prompts.
- **Never** redistribute the dataset; there is no upstream licence permitting it.
- Treat the file as read-only input. It is refreshed from the corpus, not edited here.
