"""V3 handler package — registration aggregator.

register_v3_handlers(app) must be called BEFORE the legacy handler
registrations in bot.py so V3 callback prefixes win the match order.
"""

from handlers.alerts import alert_delivery_job, cmd_alerts, handle_alerts_callback
from handlers.analyze import cmd_analyze, handle_ai_callback
from handlers.market import cmd_market
from handlers.menu import V3_CALLBACK_PATTERN, handle_v3_callback, v3_main_menu_kb
from handlers.scan import (
    cmd_deepscan,
    cmd_risk,
    cmd_scan,
    handle_deep_callback,
    handle_scan_callback,
    handle_watch_callback,
    set_databus_fallback,
)
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

__all__ = [
    "V3_CALLBACK_PATTERN",
    "alert_delivery_job",
    "cmd_alerts",
    "cmd_analyze",
    "cmd_deepscan",
    "cmd_market",
    "cmd_risk",
    "cmd_scan",
    "handle_ai_callback",
    "handle_alerts_callback",
    "handle_deep_callback",
    "handle_scan_callback",
    "handle_v3_callback",
    "handle_watch_callback",
    "register_v3_handlers",
    "set_databus_fallback",
    "v3_main_menu_kb",
]


def register_v3_handlers(app: Application) -> None:
    """Register all V3 command + callback handlers (run before legacy handlers)."""
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("deepscan", cmd_deepscan))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CallbackQueryHandler(handle_v3_callback, pattern=V3_CALLBACK_PATTERN))
