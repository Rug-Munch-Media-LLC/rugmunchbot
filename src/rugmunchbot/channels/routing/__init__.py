"""Channel routing: one table, one gate, one dry-run proof."""

from rugmunchbot.channels.routing.gate import (
    Decision,
    decide,
    is_live,
    release_slot,
    reset_buckets,
)
from rugmunchbot.channels.routing.table import (
    REDIRECTS,
    ROUTING_TABLE,
    SEVERITY_RANK,
    ChannelRoute,
    policy_for,
)

__all__ = [
    "REDIRECTS",
    "ROUTING_TABLE",
    "SEVERITY_RANK",
    "ChannelRoute",
    "Decision",
    "decide",
    "is_live",
    "release_slot",
    "policy_for",
    "reset_buckets",
]
