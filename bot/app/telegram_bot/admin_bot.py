#!/usr/bin/env python3
"""
RMI Admin Bot v2 — Full ChatOps Server Management
===================================================
@rmi_admin_bot — Manage the entire RMI fleet from Telegram.

Commands:
  🖥️  /status    — Full system dashboard
  🐳  /docker    — Container management (keyboard-driven)
  💻  /sh        — Safe shell execution
  🔧  /service   — Systemd/docker service control
  📦  /backup    — Backup triggers & status
  🚀  /deploy    — Rebuild & deploy services
  📊  /alerts    — Fleet health check
  📋  /logs      — View service logs
  ⚡  /quick     — One-tap common ops

Owner-only — rejects non-owner users immediately.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import admin_ops
import admin_terminal
from config import ADMIN_BOT_TOKEN, OWNER_IDS
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    import admin_ai
except ImportError:
    admin_ai = None
try:
    import admin_monitor
except ImportError:
    admin_monitor = None
try:
    import admin_docker
except ImportError:
    admin_docker = None
try:
    import admin_deploy
except ImportError:
    admin_deploy = None

# ── Setup ──
if not ADMIN_BOT_TOKEN:
    print("FATAL: ADMIN_BOT_TOKEN not set")
    sys.exit(1)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] admin_bot: %(message)s", level=logging.INFO
)
logger = logging.getLogger("admin_bot")

# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════


def is_owner(update: Update) -> bool:
    return update.effective_user.id in OWNER_IDS


def owner_only(func):
    """Decorator: reject non-owners immediately."""

    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not is_owner(update):
            await update.message.reply_text(
                "⛔ Unauthorized. This bot is for RMI admins only."
            )
            return
        return await func(update, ctx)

    return wrapper


async def edit_or_reply(
    update: Update, ctx, text: str, keyboard=None, parse_mode=ParseMode.MARKDOWN
):
    """Edit the callback message or send new reply."""
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=keyboard, parse_mode=parse_mode
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text, reply_markup=keyboard, parse_mode=parse_mode
            )
    else:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode=parse_mode
        )


def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🐳 Docker", callback_data="docker"),
                InlineKeyboardButton("🖥️ Status", callback_data="status"),
            ],
            [
                InlineKeyboardButton("💻 Shell", callback_data="shell_help"),
                InlineKeyboardButton("🔧 Services", callback_data="services"),
            ],
            [
                InlineKeyboardButton("📦 Backup", callback_data="backup"),
                InlineKeyboardButton("🚀 Deploy", callback_data="deploy"),
            ],
            [
                InlineKeyboardButton("📊 Health", callback_data="alerts"),
                InlineKeyboardButton("📋 Logs", callback_data="logs_menu"),
            ],
            [InlineKeyboardButton("⚡ Quick Ops", callback_data="quick")],
        ]
    )


def back_button(data: str = "main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=data)]])


# ══════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════


@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛡️ *RMI Admin Bot v2*\n\n"
        "Server management for the Rug Munch fleet.\n"
        "All commands are owner-only and logged.\n\n"
        "*Commands:*\n"
        "/status — System dashboard\n"
        "/docker — Container management\n"
        "/sh — Shell execution\n"
        "/service — Service control\n"
        "/backup — Backup triggers\n"
        "/deploy — Deploy services\n"
        "/alerts — Health checks\n"
        "/logs — View logs\n"
        "/quick — Common ops\n"
        "/help — This message",
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


@owner_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


# ══════════════════════════════════════════════
# STATUS
# ══════════════════════════════════════════════


@owner_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Gathering fleet status...")
    s = admin_ops.system_status()
    text = admin_ops.format_status(s)
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="status"),
                    InlineKeyboardButton("📊 Health", callback_data="alerts"),
                ],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
    )


# ══════════════════════════════════════════════
# DOCKER
# ══════════════════════════════════════════════


@owner_only
async def cmd_docker(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐳 *Docker Management*",
        reply_markup=docker_menu(),
        parse_mode=ParseMode.MARKDOWN,
    )


def docker_menu():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📋 List Containers", callback_data="docker_ps"),
                InlineKeyboardButton("📊 Stats", callback_data="docker_stats"),
            ],
            [
                InlineKeyboardButton(
                    "🔄 Restart Backend", callback_data="docker_restart_rmi-backend"
                ),
                InlineKeyboardButton(
                    "🔄 Restart Postgres", callback_data="docker_restart_rmi-postgres"
                ),
            ],
            [
                InlineKeyboardButton("📋 Compose PS", callback_data="compose_ps"),
                InlineKeyboardButton(
                    "🔄 Compose Restart", callback_data="compose_restart"
                ),
            ],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
        ]
    )


# ══════════════════════════════════════════════
# SHELL
# ══════════════════════════════════════════════


@owner_only
async def cmd_shell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "💻 *Shell Execution*\n\n"
            "Usage: `/sh <command>`\n\n"
            "Examples:\n"
            "`/sh df -h`\n"
            "`/sh docker ps`\n"
            "`/sh systemctl status rmi-backend`\n\n"
            "⚠️ All commands are logged. Dangerous commands are blocked.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    cmd = " ".join(args)
    msg = await update.message.reply_text(
        f"⏳ Running: `{cmd[:100]}`...", parse_mode=ParseMode.MARKDOWN
    )
    result = admin_ops.run_shell(cmd, timeout=30)
    output = result["output"] or result["error"]
    status_line = (
        f"{'✅' if result['ok'] else '❌'} `{cmd[:80]}` ({result['duration']:.1f}s)"
    )
    await msg.edit_text(
        f"{status_line}\n\n```\n{output[:3000]}\n```",
        parse_mode=ParseMode.MARKDOWN,
    )


# ══════════════════════════════════════════════
# SERVICES
# ══════════════════════════════════════════════


@owner_only
async def cmd_service(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        text = admin_ops.service_list()
        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=service_action_keyboard()
        )
        return

    name = args[0]
    action = args[1] if len(args) > 1 else "status"

    msg = await update.message.reply_text(f"⏳ {action} {name}...")
    result = admin_ops.service_control(name, action)
    if result["ok"]:
        await msg.edit_text(
            f"✅ `{action} {name}` — done\n\n```\n{result['output'][:2000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=service_action_keyboard(name),
        )
    else:
        await msg.edit_text(
            f"❌ {result['error']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("services"),
        )


def service_action_keyboard(svc: str = ""):
    buttons = []
    if svc:
        buttons.append(
            [
                InlineKeyboardButton("🔄 Restart", callback_data=f"svc_restart_{svc}"),
                InlineKeyboardButton("⏹ Stop", callback_data=f"svc_stop_{svc}"),
                InlineKeyboardButton("▶️ Start", callback_data=f"svc_start_{svc}"),
            ]
        )
    buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])
    return InlineKeyboardMarkup(buttons)


# ══════════════════════════════════════════════
# BACKUP
# ══════════════════════════════════════════════


@owner_only
async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    action = args[0] if args else ""

    if action == "run":
        msg = await update.message.reply_text("⏳ Running full backup...")
        result = admin_ops.trigger_backup("full")
        await msg.edit_text(
            f"{'✅' if result['ok'] else '❌'} Backup complete ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("backup"),
        )
    elif action == "db":
        msg = await update.message.reply_text("⏳ Running database backup...")
        result = admin_ops.trigger_backup("db")
        await msg.edit_text(
            f"{'✅' if result['ok'] else '❌'} DB Backup ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("backup"),
        )
    else:
        await update.message.reply_text(
            "📦 *Backup Control*",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Run Full Backup", callback_data="backup_run"
                        ),
                        InlineKeyboardButton("🗄️ DB Backup", callback_data="backup_db"),
                    ],
                    [
                        InlineKeyboardButton(
                            "📊 Backup Status", callback_data="backup_status"
                        )
                    ],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
            parse_mode=ParseMode.MARKDOWN,
        )


# ══════════════════════════════════════════════
# DEPLOY
# ══════════════════════════════════════════════


@owner_only
async def cmd_deploy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "🚀 *Deploy Services*",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🖥️ Backend", callback_data="deploy_backend"
                        ),
                        InlineKeyboardButton("🤖 Bot", callback_data="deploy_bot"),
                    ],
                    [
                        InlineKeyboardButton(
                            "🛡️ Admin Bot", callback_data="deploy_admin"
                        ),
                        InlineKeyboardButton("🏗️ Infra", callback_data="deploy_infra"),
                    ],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    service = args[0]
    msg = await update.message.reply_text(f"⏳ Deploying {service}...")
    result = admin_ops.deploy_service(service)
    await msg.edit_text(
        f"{'✅' if result['ok'] else '❌'} Deploy {service} ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_button("deploy"),
    )


# ══════════════════════════════════════════════
# LOGS
# ══════════════════════════════════════════════


@owner_only
async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "📋 *View Logs*\n\nPick a service:",
            reply_markup=logs_menu(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    service = args[0]
    tail = int(args[1]) if len(args) > 1 and args[1].isdigit() else 50
    msg = await update.message.reply_text(f"⏳ Fetching logs for {service}...")
    result = admin_ops.docker_logs(service, tail=tail)
    if result["ok"] and result["output"]:
        await msg.edit_text(
            f"📋 `{service}` (last {tail} lines):\n\n```\n{result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )
    else:
        # Try journalctl
        j_result = admin_ops.run_shell(
            f"journalctl -u {service}.service --no-pager -n {tail} 2>&1", timeout=10
        )
        await msg.edit_text(
            f"📋 `{service}` (systemd, last {tail} lines):\n\n```\n{j_result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )


def logs_menu():
    buttons = []
    row = []
    for i, svc in enumerate(
        [
            "rmi-backend",
            "rmi-postgres",
            "rmi-redis",
            "rmi-clickhouse",
            "rmi-neo4j",
            "rmi-qdrant",
            "rmi-prometheus",
            "rmi-mcp-x402",
        ]
    ):
        row.append(InlineKeyboardButton(svc, callback_data=f"logs_{svc}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])
    return InlineKeyboardMarkup(buttons)


# ══════════════════════════════════════════════
# ALERTS / HEALTH
# ══════════════════════════════════════════════


@owner_only
async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Running health checks...")
    checks = admin_ops.health_check()
    text = admin_ops.format_health(checks)
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="alerts"),
                    InlineKeyboardButton("🖥️ Full Status", callback_data="status"),
                ],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
    )


# ══════════════════════════════════════════════
# QUICK OPS
# ══════════════════════════════════════════════


@owner_only
async def cmd_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ *Quick Operations*",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔄 Restart Backend", callback_data="docker_restart_rmi-backend"
                    ),
                    InlineKeyboardButton(
                        "🔄 Restart Bot", callback_data="docker_restart_rugmunchbot"
                    ),
                ],
                [
                    InlineKeyboardButton("🖥️ Full Status", callback_data="status"),
                    InlineKeyboardButton("📊 Health Check", callback_data="alerts"),
                ],
                [
                    InlineKeyboardButton("🐳 Docker PS", callback_data="docker_ps"),
                    InlineKeyboardButton(
                        "📦 Backup Status", callback_data="backup_status"
                    ),
                ],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


# ══════════════════════════════════════════════
# CALLBACK ROUTER
# ══════════════════════════════════════════════


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if not is_owner(update):
        await query.edit_message_text("⛔ Unauthorized.")
        return

    try:
        # ── Main navigation ──
        if data == "main":
            await query.edit_message_text(
                "🛡️ *RMI Admin Bot v2* — Select an option:",
                reply_markup=main_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data == "status":
            s = admin_ops.system_status()
            text = admin_ops.format_status(s)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("🔄 Refresh", callback_data="status"),
                            InlineKeyboardButton("📊 Health", callback_data="alerts"),
                        ],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        # ── Docker ──
        elif data == "docker":
            await query.edit_message_text(
                "🐳 *Docker Management*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=docker_menu(),
            )

        elif data == "docker_ps":
            out = admin_ops.docker_ps()
            await query.edit_message_text(
                f"🐳 *Docker PS*\n\n```\n{out[:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("docker"),
            )

        elif data == "docker_stats":
            result = admin_ops.docker_stats()
            await query.edit_message_text(
                f"📊 *Container Stats*\n\n```\n{result['output'][:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("docker"),
            )

        elif data.startswith("docker_restart_"):
            container = data.replace("docker_restart_", "")
            result = admin_ops.docker_restart(container)
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} Restarted `{container}`\n\n```\n{result.get('output', result.get('error', ''))[:2000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("docker"),
            )

        # ── Compose ──
        elif data == "compose_ps":
            result = admin_ops.docker_compose("ps")
            await query.edit_message_text(
                f"📋 *Compose PS*\n\n```\n{result['output'][:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("docker"),
            )

        elif data == "compose_restart":
            await query.edit_message_text("⏳ Restarting infra...")
            result = admin_ops.docker_compose("restart")
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} Infra restarted\n\n```\n{result['output'][:2000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("docker"),
            )

        # ── Shell help ──
        elif data == "shell_help":
            await query.edit_message_text(
                "💻 *Shell Execution*\n\n"
                "Use `/sh <command>` from the chat.\n"
                "Examples: `/sh df -h`, `/sh docker ps`\n\n"
                "⚠️ Dangerous commands are blocked.\n"
                "All commands are logged.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("main"),
            )

        # ── Services ──
        elif data == "services":
            text = admin_ops.service_list()
            await query.edit_message_text(
                text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main")
            )

        elif data.startswith("svc_"):
            parts = data.split("_", 2)  # svc_restart_rmi-backend
            if len(parts) >= 3:
                action, svc = parts[1], parts[2]
                result = admin_ops.service_control(svc, action)
                await query.edit_message_text(
                    f"{'✅' if result['ok'] else '❌'} `{action} {svc}`\n\n```\n{result.get('output', result.get('error', ''))[:2000]}\n```",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=service_action_keyboard(svc),
                )

        # ── Backup ──
        elif data == "backup":
            await query.edit_message_text(
                "📦 *Backup Control*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔄 Run Full Backup", callback_data="backup_run"
                            ),
                            InlineKeyboardButton(
                                "🗄️ DB Backup", callback_data="backup_db"
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                "📊 Backup Status", callback_data="backup_status"
                            )
                        ],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        elif data in ("backup_run", "backup_db"):
            btype = "full" if data == "backup_run" else "db"
            await query.edit_message_text(f"⏳ Running {btype} backup...")
            result = admin_ops.trigger_backup(btype)
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} {btype} backup ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("backup"),
            )

        elif data == "backup_status":
            result = admin_ops.trigger_backup("status")
            await query.edit_message_text(
                result["output"],
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("backup"),
            )

        # ── Deploy ──
        elif data == "deploy":
            await query.edit_message_text(
                "🚀 *Deploy Services*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🖥️ Backend", callback_data="deploy_backend"
                            ),
                            InlineKeyboardButton("🤖 Bot", callback_data="deploy_bot"),
                        ],
                        [
                            InlineKeyboardButton(
                                "🛡️ Admin Bot", callback_data="deploy_admin"
                            ),
                            InlineKeyboardButton(
                                "🏗️ Infra", callback_data="deploy_infra"
                            ),
                        ],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        elif data.startswith("deploy_"):
            svc = data.replace("deploy_", "")
            await query.edit_message_text(f"⏳ Deploying {svc}...")
            result = admin_ops.deploy_service(svc)
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} Deploy {svc} ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("deploy"),
            )

        # ── Logs ──
        elif data == "logs_menu":
            await query.edit_message_text(
                "📋 *View Logs* — Pick a service:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=logs_menu(),
            )

        elif data.startswith("logs_"):
            svc = data.replace("logs_", "")
            result = admin_ops.docker_logs(svc, tail=40)
            text = (
                result["output"]
                if result["ok"] and result["output"]
                else result.get("error", "no output")
            )
            await query.edit_message_text(
                f"📋 `{svc}` (last 40 lines):\n\n```\n{text[:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("logs_menu"),
            )

        # ── Alerts ──
        elif data == "alerts":
            checks = admin_ops.health_check()
            text = admin_ops.format_health(checks)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("🔄 Refresh", callback_data="alerts"),
                            InlineKeyboardButton("🖥️ Status", callback_data="status"),
                        ],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        # ── Quick ──
        elif data == "quick":
            await query.edit_message_text(
                "⚡ *Quick Operations*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔄 Restart Backend",
                                callback_data="docker_restart_rmi-backend",
                            ),
                            InlineKeyboardButton(
                                "🔄 Restart Bot",
                                callback_data="docker_restart_rugmunchbot",
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                "🖥️ Full Status", callback_data="status"
                            ),
                            InlineKeyboardButton(
                                "📊 Health Check", callback_data="alerts"
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                "🐳 Docker PS", callback_data="docker_ps"
                            ),
                            InlineKeyboardButton(
                                "📦 Backup Status", callback_data="backup_status"
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                "💻 Terminal", callback_data="term_enter"
                            )
                        ],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        # ── Terminal callbacks ──
        elif data == "term_enter":
            await query.edit_message_text(
                "💻 Use `/term` to enter terminal mode from the chat.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("main"),
            )
        elif data == "term_quick_menu":
            await query.edit_message_text(
                "⚡ *Terminal Quick Commands* — pick one to run:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=admin_terminal.quick_commands_keyboard(),
            )
        elif data.startswith("term_quick_"):
            cmd_name = data.replace("term_quick_", "")
            result = admin_terminal.run_quick(cmd_name)
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} Quick: `{cmd_name}`\n\n```\n{result['output'][:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⚡ More Commands", callback_data="term_quick_menu"
                            )
                        ],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )
        elif data == "term_exit":
            user_id = query.from_user.id
            admin_terminal.exit_terminal(user_id)
            await query.edit_message_text(
                "👋 Exited terminal mode.",
                reply_markup=main_menu_keyboard(),
            )

        # ── AI callbacks ──
        elif data.startswith("ai_model_"):
            if admin_ai is None:
                await query.edit_message_text("⚠️ AI module not available.")
                return
            model_id = data.replace("ai_model_", "")
            admin_ai.set_model(query.from_user.id, model_id)
            model_name = admin_ai.MODELS.get(model_id, {}).get("name", model_id)
            await query.edit_message_text(
                f"🤖 Switched to *{model_name}*\n`{model_id}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=admin_ai.model_keyboard(),
            )
        elif data == "ai_clear":
            if admin_ai:
                admin_ai.clear_history(query.from_user.id)
            await query.edit_message_text(
                "🧹 Chat history cleared.", parse_mode=ParseMode.MARKDOWN
            )

    except Exception as e:
        logger.error(f"Callback error: {e}")
        try:
            await query.edit_message_text(
                f"⚠️ Error: {str(e)[:200]}", reply_markup=back_button("main")
            )
        except Exception:
            pass


# ══════════════════════════════════════════════
# ERROR HANDLER
# ══════════════════════════════════════════════


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {ctx.error}", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"⚠️ Operation failed: {str(ctx.error)[:200]}"
            )
        except Exception:
            pass


# ══════════════════════════════════════════════
# TERMINAL MODE
# ══════════════════════════════════════════════


@owner_only
async def cmd_terminal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Enter terminal mode — every message becomes a shell command."""
    user_id = update.effective_user.id
    admin_terminal.enter_terminal(user_id)
    session = admin_terminal._sessions[user_id]
    prompt = admin_terminal.format_prompt(session)
    await update.message.reply_text(
        f"💻 *TERMINAL MODE*\n\n{prompt}\n\n"
        "Every message is now a shell command.\n"
        "• `cd <dir>` — change directory\n"
        "• `pwd` — show current dir\n"
        "• `exit` — leave terminal mode\n\n"
        "_Commands are filtered for safety. All commands logged._",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⚡ Quick Commands", callback_data="term_quick_menu"
                    )
                ],
                [InlineKeyboardButton("🚪 Exit Terminal", callback_data="term_exit")],
            ]
        ),
    )


async def handle_terminal_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle messages in terminal mode."""
    user_id = update.effective_user.id
    if not admin_terminal.in_terminal(user_id):
        return
    if not is_owner(update):
        await update.message.reply_text("⛔ Unauthorized")
        return

    session = admin_terminal._sessions[user_id]
    cmd = update.message.text.strip()
    result = admin_terminal.execute(session, cmd)

    if result.get("exit"):
        admin_terminal.exit_terminal(user_id)
        await update.message.reply_text(
            "👋 *Exited terminal mode.*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(),
        )
        return

    response_text = admin_terminal.format_result(result, session)
    next_prompt = admin_terminal.format_prompt(session)

    await update.message.reply_text(
        f"{response_text}\n\n{next_prompt}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⚡ Quick Commands", callback_data="term_quick_menu"
                    )
                ],
                [InlineKeyboardButton("🚪 Exit Terminal", callback_data="term_exit")],
            ]
        ),
    )


# ══════════════════════════════════════════════
# AI GATEWAY
# ══════════════════════════════════════════════


@owner_only
async def cmd_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """AI Gateway — query our AI models."""
    if admin_ai is None:
        await update.message.reply_text(
            "⚠️ AI module not available (missing admin_ai.py)"
        )
        return

    args = ctx.args
    if not args:
        await update.message.reply_text(
            "🤖 *AI Gateway*\n\n"
            "`/ai ask <prompt>` — Ask any AI model\n"
            "`/ai model` — Switch models\n"
            "`/ai clear` — Clear chat history\n"
            "`/ai status` — Show usage stats",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_ai.model_keyboard()
            if hasattr(admin_ai, "model_keyboard")
            else None,
        )
        return

    action = args[0].lower()
    user_id = update.effective_user.id

    if action == "model":
        await update.message.reply_text(
            admin_ai.list_models(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_ai.model_keyboard(),
        )
    elif action == "clear":
        admin_ai.clear_history(user_id)
        await update.message.reply_text(
            "🧹 Chat history cleared.", parse_mode=ParseMode.MARKDOWN
        )
    elif action == "status":
        usage = admin_ai.get_usage(user_id)
        await update.message.reply_text(
            f"📊 *AI Usage*\n\nModel: `{usage['model']}`\nTokens: {usage['tokens']}\nMessages: {usage['messages']}",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif action in ("ask", "q", "query"):
        prompt = " ".join(args[1:]) if len(args) > 1 else ""
        if not prompt:
            await update.message.reply_text(
                "Usage: `/ai ask <your question>`", parse_mode=ParseMode.MARKDOWN
            )
            return
        msg = await update.message.reply_text("🤖 Thinking...")
        result = await admin_ai.chat(
            user_id,
            prompt,
            system_prompt="You are an RMI server admin assistant. Be concise and technical.",
        )
        if result["ok"]:
            content = result["content"][:3500]
            await msg.edit_text(
                f"🤖 *{result['model']}* ({result['tokens']} tokens)\n\n{content}",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await msg.edit_text(
                f"❌ AI Error: {result['error'][:200]}", parse_mode=ParseMode.MARKDOWN
            )


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════


def main():
    app = Application.builder().token(ADMIN_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("docker", cmd_docker))
    app.add_handler(CommandHandler("sh", cmd_shell))
    app.add_handler(CommandHandler("service", cmd_service))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("deploy", cmd_deploy))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("quick", cmd_quick))
    app.add_handler(CommandHandler("term", cmd_terminal))
    app.add_handler(CommandHandler("ai", cmd_ai))

    # Terminal mode — catch ALL text messages when in terminal
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_terminal_message),
        group=0,
    )

    # Callback queries (inline keyboards)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Error handler
    app.add_error_handler(error_handler)

    logger.info("🛡️ RMI Admin Bot v2 starting (@rmi_admin_bot)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
