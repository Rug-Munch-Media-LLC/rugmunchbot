"""Smart money signals — early buyers from Ponder, snipers from Neo4j, profitable wallets."""

# ⚠️ DEAD CODE — NOT WIRED INTO MAIN BOT
# This module is NOT imported or registered by bot.py.
# Kept as reference for future migration to rmi-backend.
# DO NOT add production credentials to this file.

import logging
import os

import asyncpg
import httpx
from telegram import ParseMode, Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.smartmoney")


async def smartmoney_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show smart money activity for a token from multiple data sources."""
    if not context.args:
        await update.message.reply_text(
            "💎 *Smart Money Signals*\n\n"
            "Analyzes who's buying and selling — snipers, insiders, whales.\n"
            "Data: Ponder (transfers) + Neo4j (entities) + DexScreener (price)\n\n"
            "Usage: `/smartmoney <address>`\n\n"
            "Example: `/smartmoney So11111111111111111111111111111111111111112`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    address = context.args[0].lower()
    chain = (
        "ethereum"
        if address.startswith("0x")
        else "tron"
        if address.startswith("T")
        else "solana"
    )

    msg = await update.message.reply_text(
        f"💎 Analyzing smart money for `{address[:12]}...`",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        text = f"💎 *Smart Money: `{address[:12]}...`*\n*Chain:* {chain}\n\n"

        # 1. Check DexScreener for price + liquidity data
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{address}"
                )
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        p = pairs[0]
                        price = float(p.get("priceUsd", 0))
                        liq = float(p.get("liquidity", {}).get("usd", 0))
                        vol_24h = float(p.get("volume", {}).get("h24", 0))
                        age = p.get("pairCreatedAt", 0)
                        txns = p.get("txns", {}).get("h24", {})
                        buys = int(txns.get("buys", 0))
                        sells = int(txns.get("sells", 0))

                        text += "*📊 Market Data:*\n"
                        text += f"• Price: ${price:.8f}\n"
                        text += f"• Liquidity: ${liq:,.0f}\n"
                        text += f"• 24h Vol: ${vol_24h:,.0f}\n"
                        text += f"• Buys/Sells: {buys}/{sells}\n\n"
                    else:
                        text += "ℹ️ No DEX data found.\n\n"
        except:
            text += "ℹ️ Market data unavailable.\n\n"

        # 2. Check Ponder for early holder patterns
        db_map = {
            "solana": "ponder_solana",
            "ethereum": "ponder",
            "bsc": "ponder_bsc",
            "arbitrum": "ponder_arbitrum",
            "polygon": "ponder_polygon",
        }
        db = db_map.get(chain)
        if db:
            try:
                conn = await asyncpg.connect(
                    f"postgresql://{os.getenv('POSTGRES_USER', 'rmi')}:{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST', '127.0.0.1')}:{os.getenv('POSTGRES_PORT', '5432')}/{db}",
                    timeout=5,
                )
                view = await conn.fetchval(
                    "SELECT tablename FROM pg_tables WHERE tablename LIKE '%__token_transfer' "
                    "AND tablename NOT LIKE '%reorg%' ORDER BY tablename DESC LIMIT 1"
                )
                if view:
                    # Get top early holders (first to buy)
                    rows = await conn.fetch(
                        f"SELECT from_address, to_address, CAST(value AS TEXT) as val, "
                        f"CAST(timestamp AS BIGINT) as ts "
                        f'FROM public."{view}" WHERE token_address = $1 '
                        f"ORDER BY timestamp ASC LIMIT 10",
                        address,
                    )
                    await conn.close()

                    if rows:
                        text += "*🐳 Early Movements:*\n"
                        seen = set()
                        for r in rows[:5]:
                            for addr in [r["from_address"], r["to_address"]]:
                                if (
                                    addr not in seen
                                    and addr
                                    != "0x0000000000000000000000000000000000000000"
                                ):
                                    seen.add(addr)
                                    val = int(r["val"]) / 1e18
                                    text += f"• `{addr[:10]}...` — {val:.2f} tokens\n"
                    else:
                        text += "ℹ️ No transfer history in Ponder.\n"
            except:
                text += "ℹ️ Ponder data unavailable.\n"

        # 3. Check Neo4j for entity labels
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver("bolt://rmi-neo4j:7687", auth=None)
            with driver.session(database="neo4j") as session:
                rows = session.run(
                    "MATCH (a:Address)-[:INTERACTS_WITH]->(t:Address {address: $addr}) "
                    "WHERE a.label IS NOT NULL OR a.inferred_label IS NOT NULL "
                    "RETURN a.address AS wallet, COALESCE(a.label, a.inferred_label) AS label "
                    "LIMIT 8",
                    addr=address,
                )
                records = list(rows)
                if records:
                    text += "\n*🏷️ Known Entities:*\n"
                    for r in records:
                        text += f"• `{r['wallet'][:10]}...` — {r['label'][:25]}\n"
            driver.close()
        except:
            pass

        # 4. Risk assessment
        text += "\n*⚡ Signal Summary:*\n"
        if len(records) > 3:
            text += "Multiple known entities interacting — 🟡 monitor"
        else:
            text += "Few known entities — 🟢 clean"

        await msg.edit_text(text[:2000], parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)[:100]}")


handlers = [CommandHandler("smartmoney", smartmoney_command)]
