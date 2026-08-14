"""Provider protocol seams — the decoupling layer between the OSS bot and
operator-supplied backends (scanners, billing, LLM routers, data providers).

The private monolith implements the REAL providers; OSS ships stubs and
protocols so a self-hoster plugs in their own. See
docs/RUGBRAIN_RUGMUNCHBOT_OSS_PLAN.md §2.3 and
docs/RUGMUNCHBOT-EXTRACTION-CHECKLIST.md §8.
"""

from __future__ import annotations

from rugmunchbot.plugins.billing import BillingProvider, NoopBillingProvider
from rugmunchbot.plugins.data import DataProvider, NoopDataProvider
from rugmunchbot.plugins.llm import LLMRouter, LocalLLMRouter
from rugmunchbot.plugins.scanner import PublicAPIScanProvider, ScanProvider

__all__ = [
    "BillingProvider",
    "DataProvider",
    "LLMRouter",
    "LocalLLMRouter",
    "NoopBillingProvider",
    "NoopDataProvider",
    "PublicAPIScanProvider",
    "ScanProvider",
]
