"""Community (channel-owner) settings for a chat.

Recovered from a single 648-line ``community/settings.py`` and split so every
module stays under the 200-line cap and has one job:

* :mod:`models`         — token, amount presets, button config + allowlists
* :mod:`config_models`  — moderation, voting, features, root document
* :mod:`store`          — Redis persistence (one JSON doc per chat)
* :mod:`validation`     — validated single-key writes
* :mod:`auth`           — owner / explicit admin / Telegram admin gating
* :mod:`audit`          — append-only change trail
* :mod:`commands`       — ``/setup`` grammar (Telegram-free)
* :mod:`api`            — stable read getters for other modules
"""

from rugmunchbot.community.api import (
    get_admin_ids,
    get_amount_presets,
    get_button_config,
    get_features_config,
    get_moderation_config,
    get_token_config,
    get_voting_config,
    is_feature_enabled,
)
from rugmunchbot.community.audit import AuditEntry, get_audit_log
from rugmunchbot.community.auth import is_chat_admin, require_chat_admin
from rugmunchbot.community.commands import SETUP_HELP, run_setup_command
from rugmunchbot.community.config_models import (
    CommunitySettings,
    FeaturesConfig,
    ModerationConfig,
    VotingConfig,
)
from rugmunchbot.community.models import (
    AmountPresets,
    ButtonConfig,
    Chain,
    EmojiConfig,
    TokenConfig,
)
from rugmunchbot.community.store import (
    COMMUNITY_CLIENT_KEY,
    delete_settings,
    load_settings,
    save_settings,
)
from rugmunchbot.community.validation import update_setting

__all__ = [
    "COMMUNITY_CLIENT_KEY",
    "SETUP_HELP",
    "AmountPresets",
    "AuditEntry",
    "ButtonConfig",
    "Chain",
    "CommunitySettings",
    "EmojiConfig",
    "FeaturesConfig",
    "ModerationConfig",
    "TokenConfig",
    "VotingConfig",
    "delete_settings",
    "get_admin_ids",
    "get_amount_presets",
    "get_audit_log",
    "get_button_config",
    "get_features_config",
    "get_moderation_config",
    "get_token_config",
    "get_voting_config",
    "is_chat_admin",
    "is_feature_enabled",
    "load_settings",
    "require_chat_admin",
    "run_setup_command",
    "save_settings",
    "update_setting",
]
