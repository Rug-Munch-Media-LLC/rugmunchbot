#!/usr/bin/env python3
"""
RugMunchBot -- Keyboard builders.

All inline keyboard markup generators in one place (modularized from the
former single-module keyboards.py):

    _token_actions.py  tap-to-action rows for token displays
    _menus.py          welcome menu + per-category submenus
    _admin.py          owner admin dashboards
    _results.py        post-scan / post-analysis action keyboards
    _commerce.py       pricing, top-ups, Stars packs, Scam School
    _utility.py        navigation, socials, paywall, nudges, API
    _engagement.py     quick-start + daily hook (v11)
"""

from __future__ import annotations

from ._admin import (
    admin_confirm_kb as admin_confirm_kb,
    admin_kb as admin_kb,
    owner_admin_reply_kb as owner_admin_reply_kb,
)
from ._commerce import (
    buy_stars_kb as buy_stars_kb,
    pricing_kb as pricing_kb,
    scamschool_kb as scamschool_kb,
    topup_kb as topup_kb,
)
from ._engagement import (
    daily_hook_kb as daily_hook_kb,
    merge_menu_kb as merge_menu_kb,
    quick_start_kb as quick_start_kb,
)
from ._menus import (
    _channel_public_url as _channel_public_url,
    _main_menu_rows as _main_menu_rows,
    _menu_row as _menu_row,
    category_account_kb as category_account_kb,
    category_ai_kb as category_ai_kb,
    category_alerts_kb as category_alerts_kb,
    category_charts_kb as category_charts_kb,
    category_help_kb as category_help_kb,
    category_intel_kb as category_intel_kb,
    category_market_kb as category_market_kb,
    category_portfolio_kb as category_portfolio_kb,
    category_rugmaps_kb as category_rugmaps_kb,
    category_scan_kb as category_scan_kb,
    category_security_kb as category_security_kb,
    category_wallet_kb as category_wallet_kb,
    main_menu_kb as main_menu_kb,
    menu_kb as menu_kb,
    welcome_menu_kb as welcome_menu_kb,
)
from ._results import (
    _share_token_url as _share_token_url,
    deep_scan_result_kb as deep_scan_result_kb,
    holders_kb as holders_kb,
    investigate_kb as investigate_kb,
    scan_actions_kb as scan_actions_kb,
    scan_result_kb as scan_result_kb,
    trace_kb as trace_kb,
)
from ._token_actions import token_buttons as token_buttons
from ._utility import (
    api_kb as api_kb,
    back_kb as back_kb,
    paywall_kb as paywall_kb,
    social_kb as social_kb,
    upgrade_nudge_kb as upgrade_nudge_kb,
)
from ._vcard import vcard_kb as vcard_kb
