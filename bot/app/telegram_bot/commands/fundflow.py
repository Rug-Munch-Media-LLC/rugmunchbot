ANVIL_URL = "http://127.0.0.1:8549"
from app.databus.chain_data import
from app.databus.local_rpc import simulate_tx get_wallet_history, get_token_data
"""Cross-chain fund flow — traces money movement using Ponder + Helius + Neo4j."""
import logging, asyncio
import httpx
import asyncpg
from telegram import Update, ParseMode
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.fundflow")

# Chain database mapping
PONDER_DBS = {
    "solana": "ponder_solana",
    "ethereum": "ponder",
    "bsc": "ponder_bsc",
    "arbitrum": "ponder_arbitrum",
    "polygon": "ponder_polygon",
    "base": "ponder",
    "tron": None,  # TRON via TronGrid API
}

async def query_ponder_transfers(address: str, chain: str, limit: int = 10) -> list:
    """Get recent transfers for an address from Ponder indexed data."""
    db = PONDER_DBS.get(chain)
    if not db:
        return []
    
    try:
        conn = await asyncpg.connect(
            f"postgresql://rmi:RMI_PROD_POSTGRES_2026@127.0.0.1:5432/{db}", timeout=5)
        
        # Find the latest token_transfer view
        view = await conn.fetchval(
            "SELECT tablename FROM pg_tables WHERE tablename LIKE '%__token_transfer' "
            "AND tablename NOT LIKE '%reorg%' ORDER BY tablename DESC LIMIT 1")
        
        if not view:
            await conn.close()
            return []
        
        rows = await conn.fetch(
            f'SELECT token_address, from_address, to_address, CAST(value AS TEXT) as value, '
            f'tx_hash, CAST(timestamp AS BIGINT) as timestamp '
            f'FROM public."{view}" WHERE from_address = $1 OR to_address = $1 '
            f'ORDER BY timestamp DESC LIMIT $2', address.lower(), limit)
        await conn.close()
        
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"Ponder query failed: {e}")
        return []

async def get_solana_transactions(address: str) -> list:
    """Get Solana tx history from Helius (free)."""
    import os
    key = os.getenv("HELIUS_API_KEY", "")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.helius.xyz/v0/addresses/{address}/transactions",
                           params={"apiKey": key, "limit": 5})
            if r.status_code == 200:
                txns = r.json()
                result = []
                for tx in txns[:5]:
                    result.append({
                        "type": tx.get("type", "UNKNOWN"),
                        "source": tx.get("source", "solana"),
                        "fee": tx.get("fee", 0),
                        "timestamp": tx.get("timestamp", 0),
                        "signature": tx.get("signature", "")[:12],
                    })
                return result
    except:
        pass
    return []

async def fundflow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trace fund flow across chains with real on-chain data."""
    if not context.args:
        await update.message.reply_text(
            "🔗 *Cross-Chain Fund Flow*\n\n"
            "Traces real token transfers and transactions for any address.\n"
            "Data sources: Ponder indexers (EVM) + Helius (Solana) + Neo4j (entity clusters)\n\n"
            "Usage: `/fundflow <address>`\n\n"
            "Example: `/fundflow 0xdAC17F958D2ee523a2206206994597C13D831ec7`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    address = context.args[0].lower()
    chain = "ethereum" if address.startswith("0x") else "tron" if address.startswith("T") else "solana"
    
    msg = await update.message.reply_text(f"🔗 Tracing `{address[:12]}...` on {chain}", parse_mode=ParseMode.MARKDOWN)
    
    try:
        text = f"🔗 *Fund Flow: `{address[:12]}...`*\n*Chain:* {chain}\n\n"
        
        # Get wallet history from ALL sources
        transfers = await get_wallet_history(address, chain, 10)
        if transfers:
            text += "*📊 Recent Activity:*\n"
            for t in transfers[:5]:
                src = t.get("source", "?")
                frm = t.get("from", t.get("signature", ""))[:10]
                to = t.get("to", "")[:10]
                if src == "public_rpc":
                    text += f"• `{frm}...` (signature)\n"
                else:
                    text += f"• `{frm}...` → `{to}...`\n"
        else:
            text += "ℹ️ No indexed transfers found in Ponder.\n"
        
        # 2. Get Solana tx history (if applicable)
        if chain == "solana":
            sol_txns = await get_solana_transactions(address)
            if sol_txns:
                text += "\n*🪙 Recent Solana Activity:*\n"
                for tx in sol_txns[:3]:
                    text += f"• {tx['type']} — fee: {tx['fee']/1e9:.4f} SOL\n"
        
        # 3. Check Neo4j for entity cluster
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver("bolt://rmi-neo4j:7687", auth=None)
            with driver.session(database="neo4j") as session:
                # Check if address exists in Neo4j
                row = session.run(
                    "MATCH (a:Address {address: $addr}) RETURN a.label AS label, a.inferred_label AS inferred, a.risk_score AS risk",
                    addr=address
                ).single()
                if row:
                    label = row.get("label") or row.get("inferred") or ""
                    if label:
                        text += f"\n*🏷️ Entity:* {label}\n"
                else:
                    text += "\n*🔍 Entity:* Not in graph (new address)\n"
            driver.close()
        except:
            text += "\n*🔍 Entity:* Graph unavailable\n"
        
        await msg.edit_text(text[:2000], parse_mode=ParseMode.MARKDOWN)
    
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)[:100]}")

handlers = [CommandHandler("fundflow", fundflow_command)]
