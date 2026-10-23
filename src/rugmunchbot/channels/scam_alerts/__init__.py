"""Scam alerts consumer for RugBrain shill alerts.

This module consumes RugBrain's shill alerts stream and publishes them to:
- Telegram main channel (@cryptorugmuncher)
- Ghost feed page (rugmunch.io/feed)

It uses the existing ledger mechanism to prevent duplicate publications.
"""

from __future__ import annotations

from ._project_alert import build_project_alert
from ._project_publish import publish_project_alert
from ._publisher import run_scam_alerts_publisher

__all__ = ["build_project_alert", "publish_project_alert", "run_scam_alerts_publisher"]
