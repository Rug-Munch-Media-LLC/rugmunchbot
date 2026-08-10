#!/usr/bin/env python3
"""
Image generation service — charts (candlestick OHLCV) and maps (holder bubble map).

Generates PNG images locally using matplotlib/mplfinance and returns
file paths for Telegram sending. No external image APIs required.
"""

import logging
import os
import tempfile

import matplotlib

matplotlib.use("Agg")  # non-interactive backend — no display needed
import matplotlib.pyplot as plt
import mplfinance as mpf  # pyright: ignore[reportMissingImports]
import pandas as pd

logger = logging.getLogger(__name__)

# ── Style constants (dark theme matching RugMunch brand) ──
BG_COLOR = "#0d1117"
GRID_COLOR = "#1c2333"
TEXT_COLOR = "#c9d1d9"
BULL_COLOR = "#26a69a"
BEAR_COLOR = "#ef5350"
ACCENT_COLOR = "#58a6ff"

plt.rcParams.update(
    {
        "figure.facecolor": BG_COLOR,
        "axes.facecolor": BG_COLOR,
        "axes.edgecolor": GRID_COLOR,
        "axes.labelcolor": TEXT_COLOR,
        "text.color": TEXT_COLOR,
        "xtick.color": TEXT_COLOR,
        "ytick.color": TEXT_COLOR,
        "grid.color": GRID_COLOR,
        "grid.alpha": 0.5,
    }
)

TMP_DIR = tempfile.mkdtemp(prefix="rmi_img_")


def _save_fig(fig, name: str) -> str:
    """Save a matplotlib figure to a temp PNG and return the path."""
    path = os.path.join(TMP_DIR, f"{name}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    return path


def generate_chart_png(
    candles: list[dict],
    symbol: str = "TOKEN",
    chain: str = "",
    price: float = 0.0,
    change_24h: float = 0.0,
) -> str | None:
    """Generate a candlestick chart PNG from OHLCV candle data.

    Args:
        candles: List of dicts with keys 'open', 'high', 'low', 'close', 'timestamp' (unix ms).
        symbol: Token symbol for the title.
        chain: Chain name for the title.
        price: Current price for annotation.
        change_24h: 24h price change percent for annotation.

    Returns:
        Path to the generated PNG file, or None on failure.
    """
    try:
        if not candles or len(candles) < 2:
            return None

        # Build DataFrame for mplfinance
        rows = []
        for c in candles:
            ts = c.get("timestamp") or c.get("time") or c.get("t")
            if ts is None:
                continue
            # mplfinance expects datetime index
            if isinstance(ts, (int, float)):
                if ts > 1e12:
                    ts = ts / 1000  # ms → s
                dt = pd.Timestamp(ts, unit="s")
            else:
                dt = pd.Timestamp(ts)
            rows.append(
                {
                    "Date": dt,
                    "Open": float(c.get("open", 0)),
                    "High": float(c.get("high", 0)),
                    "Low": float(c.get("low", 0)),
                    "Close": float(c.get("close", 0)),
                    "Volume": float(c.get("volume", c.get("v", 0))),
                }
            )

        if len(rows) < 2:
            return None

        df = pd.DataFrame(rows)
        df.set_index("Date", inplace=True)
        df.sort_index(inplace=True)

        # Title with price info (avoid emoji — may not render in matplotlib)
        change_tag = "+" if change_24h >= 0 else ""
        chain_tag = f" {chain.upper()}" if chain else ""
        title = f"{symbol}{chain_tag}  ${price:.6f}  {change_tag}{change_24h:.1f}%"

        # Custom style
        mc = mpf.make_marketcolors(
            up=BULL_COLOR,
            down=BEAR_COLOR,
            edge="inherit",
            wick="inherit",
            volume="in",
        )
        style = mpf.make_mpf_style(
            base_mpf_style="nightclouds",
            marketcolors=mc,
            facecolor=BG_COLOR,
            edgecolor=GRID_COLOR,
            gridcolor=GRID_COLOR,
            rc={
                "figure.facecolor": BG_COLOR,
                "axes.facecolor": BG_COLOR,
                "text.color": TEXT_COLOR,
                "axes.labelcolor": TEXT_COLOR,
            },
        )

        fig, _axes = mpf.plot(
            df,
            type="candle",
            style=style,
            volume=True,
            figsize=(8, 6),
            returnfig=True,
            title=title,
            datetime_format="%H:%M",
            xrotation=0,
        )

        return _save_fig(fig, f"chart_{symbol}_{chain}")

    except Exception as exc:
        logger.warning("Chart PNG generation failed: %s", exc)
        return None


def generate_map_png(
    holders: list[dict],
    symbol: str = "TOKEN",
    chain: str = "",
    total_holders: int = 0,
    top10_pct: float = 0.0,
    clusters: list | None = None,
) -> str | None:
    """Generate a holder bubble map PNG from holder distribution data.

    Args:
        holders: List of dicts with 'address'/'holder' and 'pct'/'percentage'/'share'.
        symbol: Token symbol for the title.
        chain: Chain name for the title.
        total_holders: Total number of holders.
        top10_pct: Top 10 holder concentration percent.
        clusters: Optional list of cluster dicts with 'size' and 'label'.

    Returns:
        Path to the generated PNG file, or None on failure.
    """
    try:
        if not holders:
            return None

        # Extract holder percentages
        pcts = []
        labels = []
        for i, h in enumerate(holders[:20]):  # top 20 for readability
            pct = h.get("pct") or h.get("percentage") or h.get("share", 0)
            addr = h.get("address") or h.get("holder", "")
            tag = h.get("tag", "")
            label = f"{addr[:4]}...{addr[-3:]}" if len(addr) > 8 else addr
            if tag:
                label = f"{tag}"
            elif i < 3:
                label = f"#{i + 1} {label}"
            pcts.append(float(pct))
            labels.append(label)

        if not pcts:
            return None

        # Normalize sizes for bubble chart
        max_pct = max(pcts) if pcts else 1
        sizes = [(p / max_pct) * 2000 + 200 for p in pcts]

        # Color by concentration: red for whales, yellow for medium, green for small
        colors = []
        for p in pcts:
            if p > 20:
                colors.append(BEAR_COLOR)
            elif p > 5:
                colors.append("#f0a500")  # amber
            else:
                colors.append(BULL_COLOR)

        # Create bubble chart
        fig, ax = plt.subplots(figsize=(9, 7), facecolor=BG_COLOR)
        ax.set_facecolor(BG_COLOR)

        # Scatter positions — spiral layout for visual appeal
        import numpy as np

        angles = np.linspace(0, 2 * np.pi, len(pcts), endpoint=False)
        radii = np.linspace(0.3, 1.0, len(pcts))
        x = radii * np.cos(angles)
        y = radii * np.sin(angles)

        ax.scatter(x, y, s=sizes, c=colors, alpha=0.7, edgecolors="white", linewidths=0.5)

        # Add labels
        for i, (xi, yi, label, pct) in enumerate(zip(x, y, labels, pcts, strict=True)):
            fontsize = 7 if i < 5 else 5.5
            ax.annotate(
                f"{label}\n{pct:.1f}%",
                (xi, yi),
                fontsize=fontsize,
                color=TEXT_COLOR,
                ha="center",
                va="center",
                fontweight="bold" if i < 3 else "normal",
            )

        # Title and stats
        chain_tag = f" {chain.upper()}" if chain else ""
        ax.set_title(
            f"{symbol}{chain_tag}  Holder Distribution\n"
            f"Total Holders: {total_holders:,}  |  Top 10: {top10_pct:.1f}%",
            fontsize=12,
            color=TEXT_COLOR,
            pad=15,
        )

        # Cluster info if available
        if clusters:
            cluster_text = f"🕸️ {len(clusters)} wallet clusters detected"
            ax.text(
                0.5,
                -0.12,
                cluster_text,
                transform=ax.transAxes,
                fontsize=9,
                color=ACCENT_COLOR,
                ha="center",
            )

        ax.set_xlim(-1.5, 1.5)
        ax.set_ylim(-1.5, 1.5)
        ax.axis("off")

        # Legend
        from matplotlib.patches import Patch

        legend_elements = [
            Patch(facecolor=BEAR_COLOR, label="Whale (>20%)"),
            Patch(facecolor="#f0a500", label="Medium (5-20%)"),
            Patch(facecolor=BULL_COLOR, label="Small (<5%)"),
        ]
        ax.legend(
            handles=legend_elements,
            loc="lower right",
            fontsize=7,
            facecolor=BG_COLOR,
            edgecolor=GRID_COLOR,
            labelcolor=TEXT_COLOR,
        )

        fig.tight_layout()
        return _save_fig(fig, f"map_{symbol}_{chain}")

    except Exception as exc:
        logger.warning("Map PNG generation failed: %s", exc)
        return None


def cleanup_png(path: str | None) -> None:
    """Remove a generated PNG file after sending."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
