"""Telegram /scan command handler — extracted from bot.py."""
import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.scan")

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /scan — multi-chain token scanner."""
    # This will be wired up properly during refactor
    # For now, delegates to the existing cmd_scan in bot.py

handlers = [CommandHandler("scan", cmd_scan)]
