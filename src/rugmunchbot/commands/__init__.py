"""RugMunchBot commands package — token scanning, portfolio, charting, risk analysis."""

from app.domains.telegram.rugmunchbot.commands.chart import cmd_chart
from app.domains.telegram.rugmunchbot.commands.dune import cmd_dune
from app.domains.telegram.rugmunchbot.commands.events import cmd_events
from app.domains.telegram.rugmunchbot.commands.portfolio import cmd_portfolio
from app.domains.telegram.rugmunchbot.commands.rugmap import cmd_rugmap
from app.domains.telegram.rugmunchbot.commands.simulate import cmd_simulate
from app.domains.telegram.rugmunchbot.commands.smartmoney import cmd_smartmoney

__all__ = [
    "cmd_chart",
    "cmd_dune",
    "cmd_events",
    "cmd_portfolio",
    "cmd_rugmap",
    "cmd_simulate",
    "cmd_smartmoney",
]
