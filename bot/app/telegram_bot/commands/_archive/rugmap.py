"""Telegram /rugmap command — wallet cluster visualization.
Generates deployer network + bubble map via Neo4j wallet clustering engine.
"""

# ⚠️ DEAD CODE — NOT WIRED INTO MAIN BOT
# This module is NOT imported or registered by bot.py.
# Kept as reference for future migration to rmi-backend.
# DO NOT add production credentials to this file.

import io
import logging

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
from telegram import ParseMode, Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.rugmap")


async def rugmap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🗺 *RugMaps — Wallet Cluster Visualization*\n\n"
            "Visualizes deployer networks, funding sources, and scam connections.\n\n"
            "Usage: `/rugmap <address>`\n\n"
            "Example: `/rugmap So11111111111111111111111111111111111111112`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    address = context.args[0]
    msg = await update.message.reply_text(
        f"🗺 Generating cluster map for `{address[:12]}...`",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "http://127.0.0.1:8000/api/v1/wallet-clusters/bubblemap",
                json={"center_wallet": address, "depth": 2, "max_wallets": 50},
            )

            if r.status_code != 200:
                await msg.edit_text(f"❌ Cluster API error: {r.status_code}")
                return

            data = r.json()
            nodes = data.get("nodes", [])
            edges = data.get("edges", [])

            if not nodes:
                await msg.edit_text(
                    f"ℹ️ No cluster data found for `{address[:12]}...`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            # Build NetworkX graph
            G = nx.Graph()
            for n in nodes:
                G.add_node(n["id"], label=n.get("label", "?"), risk=n.get("risk", 0))
            for e in edges:
                G.add_edge(e["source"], e["target"])

            # Render
            fig, ax = plt.subplots(1, 1, figsize=(10, 8), facecolor="#0f0f1a")
            pos = nx.spring_layout(G, k=2, iterations=50)

            # Color nodes by risk
            colors = []
            for n in G.nodes():
                risk = G.nodes[n].get("risk", 0)
                if risk >= 80:
                    colors.append("#ff1744")
                elif risk >= 60:
                    colors.append("#ff9100")
                elif risk >= 40:
                    colors.append("#ffea00")
                else:
                    colors.append("#7c4dff")

            nx.draw(
                G,
                pos,
                ax=ax,
                node_color=colors,
                edge_color="#333355",
                node_size=300,
                with_labels=False,
                alpha=0.8,
            )

            # Highlight center address
            if address in G:
                nx.draw_networkx_nodes(
                    G,
                    pos,
                    nodelist=[address],
                    node_color="#00e676",
                    node_size=500,
                    node_shape="*",
                )

            ax.set_title(
                f"Wallet Cluster: {address[:16]}...", color="white", fontsize=12
            )
            ax.set_facecolor("#0f0f1a")
            fig.patch.set_facecolor("#0f0f1a")

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=120, facecolor="#0f0f1a")
            plt.close(fig)
            buf.seek(0)

            await msg.delete()
            await update.message.reply_photo(
                photo=buf,
                caption=f"🗺 *Wallet Cluster*\n`{address[:12]}...`\n{len(nodes)} wallets, {len(edges)} connections\n🔵 Center | 🟣 Low risk | 🟡 Med | 🟠 High | 🔴 Critical",
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)[:100]}")


handlers = [CommandHandler("rugmap", rugmap_command)]
