# VENDORED 2026-08-17 from rmi-backend/app/rag/_knowledge.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""TA Methodology Knowledge Base — ingestible patterns for RAG system.

This module contains comprehensive technical analysis methodology that can be
ingested into the RAG vector store via POST /api/v1/rag/v2/bulk-ingest or
POST /api/v1/rag/v2/ingest.

Categories covered:
  1. Classic Chart Patterns (head & shoulders, double top/bottom, cup & handle,
     flags, wedges, triangles)
  2. Indicator Methodology (RSI, MACD, Bollinger Bands, EMA crossovers, volume)
  3. Support/Resistance, Trendlines, Fibonacci Retracements
  4. Volume Analysis & Market Structure (Wyckoff Methodology)
  5. Candlestick Patterns
  6. Risk Management & Position Sizing

Usage:
    from rugmunchbot._vendor.rag_pkg.engine import bulk_ingest
    from rugmunchbot._vendor.rag_pkg.ta_knowledge import TA_KNOWLEDGE_DOCS

    await bulk_ingest(items=TA_KNOWLEDGE_DOCS, collection="ta_methodology")
"""

from __future__ import annotations

from typing import Any

# Each entry is a dict with keys:
#   - content: str          (the knowledge text)
#   - metadata: dict        (structured metadata for filtering)
#   - doc_id: str           (unique identifier for dedup)
#   - collection: str       (target vector collection — defaults to "ta_methodology")

TA_KNOWLEDGE_DOCS: list[dict[str, Any]] = [
    # ── Classic Chart Patterns ──────────────────────────────────
    {
        "content": """HEAD AND SHOULDERS PATTERN

The head and shoulders pattern is one of the most reliable trend reversal patterns in technical analysis. It signals a potential reversal from a bullish trend to a bearish trend.

STRUCTURE:
- Left Shoulder: Price rises to a peak and then declines.
- Head: Price rises above the left shoulder to form a higher peak, then declines back to the base of the left shoulder.
- Right Shoulder: Price rises again but does not reach the height of the head, then declines.
- Neckline: A support line drawn connecting the lows after the left shoulder and the head. The pattern is confirmed when price breaks below the neckline.

MEASUREMENT: The price target is the distance from the head to the neckline, projected downward from the neckline breakout point.

INVERSE HEAD AND SHOULDERS: The same pattern inverted, signaling a reversal from bearish to bullish. The neckline becomes a resistance line, and the breakout is upward.

RELIABILITY FACTORS:
- Volume should decrease as the pattern forms (left shoulder highest volume, head lower, right shoulder lowest).
- The neckline should have at least 2-3 touches for confirmation.
- Breakout should occur on above-average volume.
- A retest of the neckline after breakout is common and healthy.
- False breakouts occur approximately 15-20% of the time.

TRADING STRATEGY:
- Entry: On confirmed neckline break with increased volume.
- Stop Loss: Above the right shoulder (for short) or below the right shoulder (for long on inverse).
- Take Profit: Measured move from head to neckline projected from breakout.""",
        "metadata": {
            "category": "chart_patterns",
            "subcategory": "head_and_shoulders",
            "type": "reversal",
            "direction": "bearish",
            "reliability": 0.85,
            "source": "classic_ta",
        },
        "doc_id": "ta:pattern:head_and_shoulders",
    },
    {
        "content": """DOUBLE TOP AND DOUBLE BOTTOM PATTERNS

Double top and double bottom are classic reversal patterns that signal the end of a trend.

DOUBLE TOP (Bearish Reversal):
- Forms after an extended uptrend
- Price creates a high (first peak), pulls back, then rallies to approximately the same level (second peak) but fails to break through
- The neckline is drawn at the intermediate low between the two peaks
- Confirmation: Price breaks below the neckline
- Target: Distance from peaks to neckline projected downward
- Key feature: The two peaks should be roughly equal in height (within 3-5%)
- Volume: Decreasing on the second peak is ideal

DOUBLE BOTTOM (Bullish Reversal):
- Forms after an extended downtrend
- Price creates a low (first trough), bounces, then declines to approximately the same level (second trough)
- The neckline is drawn at the intermediate high between the two troughs
- Confirmation: Price breaks above the neckline
- Target: Distance from troughs to neckline projected upward
- Key feature: Troughs roughly equal in depth
- Volume: Increasing on the breakout confirms the pattern

PSYCHOLOGY: These patterns represent failed attempts to continue the existing trend. The market tests the prior extreme twice and fails both times, indicating exhaustion and potential reversal.

FALSE SIGNALS: The most common failure is a premature entry before neckline confirmation. The neckline must be clearly broken for the pattern to be valid. Rounded formations (not sharp V-shapes) are more reliable.""",
        "metadata": {
            "category": "chart_patterns",
            "subcategory": "double_top_bottom",
            "type": "reversal",
            "direction": "both",
            "reliability": 0.80,
            "source": "classic_ta",
        },
        "doc_id": "ta:pattern:double_top_bottom",
    },
    {
        "content": """CUP AND HANDLE PATTERN

The cup and handle is a bullish continuation pattern that resembles a teacup. It was popularized by William O'Neil and is a key pattern in CANSLIM methodology.

STRUCTURE:
- Cup: A U-shaped rounded bottom (not a sharp V) that forms over 1-6 months. The decline should retrace about 15-30% of the prior advance.
- Handle: A slight downward drift or consolidation that forms on the right side of the cup, typically over 1-2 weeks. The handle should not retrace more than 8-12% of the cup's right side height.
- Breakout: Price breaks above the handle's resistance (the right rim of the cup).

MEASUREMENT: The price target is the depth of the cup added to the breakout level.

RELIABILITY FACTORS:
- Volume should be higher on the left side of the cup (selling), lower at the bottom, and increase during the right side rise and handle breakout.
- The cup should be relatively smooth (not too many sharp reversals).
- The handle ideally forms in the upper half of the cup.
- Deeper cups (30%+) produce larger moves but have lower success rates.
- The pattern is most reliable in strong uptrends and during bull markets.

VARIATIONS:
- Cup without handle: Some breakouts occur directly from the cup's right rim without a handle. These are less reliable but can still be profitable.
- Inverted cup and handle: The bearish version, forming after downtrends.

COMMON MISTAKES:
- Entering too early (before handle breakout)
- Misidentifying V-shaped recoveries as cups
- Ignoring overall market context""",
        "metadata": {
            "category": "chart_patterns",
            "subcategory": "cup_and_handle",
            "type": "continuation",
            "direction": "bullish",
            "reliability": 0.75,
            "source": "william_oneil_canslim",
        },
        "doc_id": "ta:pattern:cup_and_handle",
    },
    {
        "content": """BULL AND BEAR FLAGS / PENNANTS

Flags and pennants are short-term continuation patterns that represent a brief pause before the trend resumes.

BULL FLAG (Bullish Continuation):
- Preceded by a sharp upward move (the flagpole)
- Consolidation in a downward-sloping or horizontal rectangular channel (the flag)
- Breakout above the upper trendline of the flag
- Target: Length of the flagpole projected from the breakout point
- Typical duration: 1-3 weeks

BEAR FLAG (Bearish Continuation):
- Preceded by a sharp downward move
- Consolidation in an upward-sloping or horizontal channel
- Breakdown below the lower trendline
- Target: Flagpole length projected downward

PENNANTS:
- Similar to flags but the consolidation lines converge (forming a small triangle)
- The flagpole is the same as in flags
- Pennants typically resolve faster than flags
- Both flag and pennant are continuation patterns — they pause, then continue

VOLUME CHARACTERISTICS:
- Volume should be very high during the flagpole formation
- Volume should decline during the flag/pennant consolidation
- Volume should spike on the breakout/breakdown

RELIABILITY:
- Flags and pennants are among the most reliable continuation patterns (80%+ success rate).
- The steeper the flagpole, the more powerful the subsequent move.
- The pattern fails if it takes too long to resolve (the trend loses momentum).

TRADING STRATEGY:
- Entry: On breakout from the flag/pennant with increased volume
- Stop loss: Below the flag's low (bull flag) or above the flag's high (bear flag)
- Multiple flags can form in sequence during strong trends (flag → pole → flag → pole)""",
        "metadata": {
            "category": "chart_patterns",
            "subcategory": "flags_and_pennants",
            "type": "continuation",
            "direction": "both",
            "reliability": 0.82,
            "source": "classic_ta",
        },
        "doc_id": "ta:pattern:flags_pennants",
    },
    {
        "content": """WEDGE PATTERNS (RISING AND FALLING)

Wedges are reversal patterns where price consolidates between two converging trendlines sloping in the same direction.

RISING WEDGE (Bearish Reversal):
- Both support and resistance trendlines slope upward and converge
- Forms after an uptrend — signals that buying momentum is weakening
- Lower highs are converging toward higher lows (the upward slope is unsustainable)
- Breakdown below the lower trendline confirms the reversal
- Volume typically declines during the wedge formation

FALLING WEDGE (Bullish Reversal):
- Both support and resistance trendlines slope downward and converge
- Forms after a downtrend — signals that selling momentum is exhausting
- Lower lows converge toward lower highs
- Breakout above the upper trendline confirms the reversal
- Volume declines then increases on breakout

DIFFERENTIATING WEDGES FROM TRIANGLES:
- Wedges: Both trendlines slope in the SAME direction (converging)
- Symmetrical triangles: Trendlines slope in OPPOSITE directions (one up, one down)
- The slope direction of wedges is key to their reversal nature

MEASUREMENT:
- Target: The widest part of the wedge projected from the breakout point
- Conservative target: Retest of the starting point of the wedge

PSYCHOLOGY:
- Rising wedge: Buyers are pushing higher but with less conviction each time; sellers are gradually building pressure
- Falling wedge: Sellers push lower but each push is weaker; buyers accumulate at higher lows

TRADING:
- Wait for confirmed breakout (close outside the wedge on significant volume)
- Initial stop at the opposite side of the wedge
- Wedges that break before reaching the apex are more reliable""",
        "metadata": {
            "category": "chart_patterns",
            "subcategory": "wedges",
            "type": "reversal",
            "direction": "both",
            "reliability": 0.78,
            "source": "classic_ta",
        },
        "doc_id": "ta:pattern:wedges",
    },
    # ── Indicator Methodology ──────────────────────────────────
    {
        "content": """RSI (RELATIVE STRENGTH INDEX) METHODOLOGY

The RSI is a momentum oscillator developed by J. Welles Wilder that measures the speed and magnitude of recent price changes. It oscillates between 0 and 100.

CALCULATION: RSI = 100 - (100 / (1 + RS)) where RS = Average Gain / Average Loss over N periods (typically 14).

KEY LEVELS:
- Overbought: RSI > 70 — Price may be overextended and due for a pullback
- Oversold: RSI < 30 — Price may be undervalued and due for a bounce
- Centerline: RSI = 50 — Neutral momentum

ADVANCED USAGE:
- Overbought/oversold in strong trends: In strong uptrends, RSI can remain overbought (70-80+) for extended periods. In strong downtrends, RSI can stay oversold (20-30). Don't blindly fade extreme RSI readings.
- RSI Divergence:
  * Bullish divergence: Price makes lower low, RSI makes higher low → potential reversal up
  * Bearish divergence: Price makes higher high, RSI makes lower high → potential reversal down
  * Hidden bullish divergence (continuation): Price makes higher low, RSI makes lower low → trend continuation
  * Hidden bearish divergence: Price makes lower high, RSI makes higher high → trend continuation
- Failure Swings: RSI moves above 70, pulls back, makes a higher low above 40-50, then breaks above prior high — bullish failure swing. Bearish: below 30, rallies, lower high below 50-60, breaks below prior low.
- RSI Range Shifts:
  * Bull market: RSI typically ranges between 40-80
  * Bear market: RSI typically ranges between 20-60

TIMEFRAME CONSIDERATIONS:
- 14-period is default but can be adjusted:
  * Shorter (7-9): More sensitive, more false signals
  * Longer (21-25): Smoother, fewer but more reliable signals
- On lower timeframes (1H, 4H), RSI is more prone to noise

COMMON MISTAKES:
- Selling just because RSI crosses above 70 without divergence confirmation
- Ignoring the prevailing trend (counter-trend trades based on RSI alone)
- Not waiting for RSI to leave extreme zones before acting on divergence""",
        "metadata": {
            "category": "indicators",
            "subcategory": "rsi",
            "type": "momentum_oscillator",
            "reliability": 0.70,
            "source": "welles_wilder",
        },
        "doc_id": "ta:indicator:rsi",
    },
    {
        "content": """MACD (MOVING AVERAGE CONVERGENCE DIVERGENCE) METHODOLOGY

MACD, developed by Gerald Appel, is a trend-following momentum indicator showing the relationship between two moving averages.

COMPONENTS:
- MACD Line: 12-period EMA minus 26-period EMA (fast minus slow)
- Signal Line: 9-period EMA of the MACD line
- Histogram: MACD Line minus Signal Line (visualizes momentum changes)

KEY SIGNALS:

1. CROSSOVERS:
   - Bullish Crossover: MACD line crosses ABOVE signal line → bullish momentum shift
   - Bearish Crossover: MACD line crosses BELOW signal line → bearish momentum shift

2. CENTERLINE CROSSOVERS:
   - MACD crosses above zero: 12 EMA crosses above 26 EMA → bullish trend
   - MACD crosses below zero: 12 EMA crosses below 26 EMA → bearish trend

3. DIVERGENCE:
   - Bullish Divergence: Price makes lower low, MACD makes higher low
   - Bearish Divergence: Price makes higher high, MACD makes lower high
   - Divergence is most powerful when MACD is at extremes (far from zero)

4. HISTOGRAM ANALYSIS:
   - Expanding histogram (bars getting taller): Momentum increasing
   - Contracting histogram (bars getting shorter): Momentum decreasing
   - Histogram flipping from negative to positive: Early bullish signal
   - Histogram peaking and starting to decline: Warning of potential reversal

OPTIMAL SETTINGS BY TIMEFRAME:
- Short-term trading: (8, 17, 9) or (5, 13, 6)
- Standard/Medium-term: (12, 26, 9)
- Long-term/Position: (21, 55, 13) or (13, 34, 9)

LIMITATIONS:
- MACD is a lagging indicator — signals come AFTER the move starts
- Generates false signals in sideways/choppy markets
- Less effective on very short timeframes (< 1H)
- Should be combined with support/resistance levels and price action

CONFLUENCE: MACD signals are most reliable when:
- They align with the higher timeframe trend
- They occur at key support/resistance levels
- Volume confirms the move""",
        "metadata": {
            "category": "indicators",
            "subcategory": "macd",
            "type": "trend_following_momentum",
            "reliability": 0.72,
            "source": "gerald_appel",
        },
        "doc_id": "ta:indicator:macd",
    },
    {
        "content": """BOLLINGER BANDS METHODOLOGY

Developed by John Bollinger, Bollinger Bands consist of three lines: a middle band (SMA), an upper band (SMA + 2 standard deviations), and a lower band (SMA - 2 standard deviations). They measure volatility and identify potential overbought/oversold conditions.

KEY CONCEPTS:

1. THE SQUEEZE (Volatility Contraction):
   - When bands narrow significantly, it indicates low volatility
   - A squeeze precedes a significant price expansion (breakout)
   - The longer the squeeze, the more powerful the eventual breakout
   - "The Squeeze" is Bollinger's most famous concept

2. BAND WALKS (Trend Confirmation):
   - In strong uptrends: Price walks along the upper band, with tags continuing
   - In strong downtrends: Price walks along the lower band
   - DON'T fade band tags in strong trends — they're not reversal signals
   - Band walks indicate trend strength, not exhaustion

3. DOUBLE BOTTOM/TOP WITH BB CONFIRMATION:
   - BB Double Bottom: First low below lower band, second low above lower band → bullish
   - BB Double Top: First high above upper band, second high below upper band → bearish

4. PERCENTAGE B (%B):
   - Formula: (Price - Lower Band) / (Upper Band - Lower Band)
   - %B > 1: Price above upper band
   - %B < 0: Price below lower band
   - %B = 0.5: Price at middle band

5. BANDWIDTH:
   - Formula: (Upper - Lower) / Middle
   - Identifies squeeze conditions
   - Very low bandwidth → impending breakout

STANDARD SETTINGS:
- Period: 20 (most common)
- Standard Deviations: 2 (captures ~95% of price action)
- For shorter timeframes: (10, 1.9) or (10, 2.0)
- For longer timeframes: (50, 2.1)

REVERSAL PATTERNS (BOLLINGER BAND SPECIFIC):
- W-Bottom: First low at/below lower band, bounce, second low above lower band forming a W, then break above middle band
- M-Top: First high at/above upper band, pullback, second high below upper band forming an M, then break below middle band
- Head Fake: Price briefly breaks a band then reverses — traps traders on the wrong side

BEST PRACTICES:
- Never use Bollinger Bands in isolation — combine with RSI, volume, or price action
- Band tags are NOT automatic buy/sell signals — context matters
- The middle band (SMA) often acts as support/resistance during trends""",
        "metadata": {
            "category": "indicators",
            "subcategory": "bollinger_bands",
            "type": "volatility",
            "reliability": 0.68,
            "source": "john_bollinger",
        },
        "doc_id": "ta:indicator:bollinger_bands",
    },
    {
        "content": """MOVING AVERAGE CROSSOVER STRATEGIES

Moving average crossovers are among the most widely followed technical signals. They occur when a shorter-term MA crosses a longer-term MA.

KEY CROSSOVER TYPES:

1. GOLDEN CROSS (Bullish):
   - 50-period MA crosses ABOVE 200-period MA
   - Most famous and widely watched MA crossover
   - Signals a potential long-term bull market
   - Confirmation: Price also above both MAs
   - Three stages:
     a) Downtrend bottom: 50 turns up toward 200
     b) Golden Cross: 50 crosses above 200
     c) Uptrend continuation: Both MAs rising with price above

2. DEATH CROSS (Bearish):
   - 50-period MA crosses BELOW 200-period MA
   - Signals a potential long-term bear market
   - Confirmation: Price below both MAs

3. FASTER CROSSOVERS (Tactical):
   - 9/21 EMA crossover (bullish: 9 crosses above 21)
   - 10/20 SMA crossover
   - 5/13 EMA crossover (aggressive, more signals, more false)
   - 8/21 EMA crossover (popular among day traders)

4. TRIPLE MOVING AVERAGE SYSTEMS:
   - 4/9/18 EMA: Fast system for scalping
   - 10/20/50 SMA: Medium-term trend following
   - 8/21/55 EMA: Fibonacci-based, popularized by traders

MULTIPLE TIMEFRAME CONFLUENCE:
- Daily TF: 50/200 for primary trend
- 4H TF: 20/50 for intermediate trend
- 1H TF: 9/21 for entry/exit timing
- Alignment across timeframes increases probability

FALSE SIGNALS (WHIPSAWS):
- Most common in sideways/choppy markets
- Adding a filter (e.g., price must also break a level) reduces false signals
- Waiting for a retest of the MA after crossover improves reliability

EMA VS SMA:
- EMA: More responsive to recent price action, better for shorter timeframes
- SMA: Smoother, less noise, better for identifying primary trend on higher timeframes

BEST PRACTICES:
- MAs work best in trending markets, poorly in ranging markets
- Use MA crossovers as trend filters, not isolated entry signals
- The slope of the MA matters more than the crossover itself
- Combine with volume: Crossovers on increasing volume are more reliable
- MA ribbon (multiple MAs fanning out) confirms strong trends""",
        "metadata": {
            "category": "indicators",
            "subcategory": "moving_averages",
            "type": "trend_following",
            "reliability": 0.65,
            "source": "classic_ta",
        },
        "doc_id": "ta:indicator:moving_average_crossovers",
    },
    # ── Support/Resistance & Trendlines ─────────────────────────
    {
        "content": """SUPPORT AND RESISTANCE METHODOLOGY

Support and resistance are the foundation of all technical analysis. They represent price levels where buying and selling pressure historically converge.

SUPPORT (Demand Zone):
- A price level where buying pressure overcomes selling pressure
- Price tends to "bounce" off support
- Broken support becomes resistance (role reversal)
- Stronger support has more historical touches/bounces
- Psychological levels: Round numbers ($10, $100, $1000) often act as support/resistance

RESISTANCE (Supply Zone):
- A price level where selling pressure overcomes buying pressure
- Price tends to "reject" from resistance
- Broken resistance becomes support
- More touches = stronger resistance

STRENGTH FACTORS (how to assess level quality):
1. Number of touches: 3+ touches = strong level; 1-2 = developing
2. Time duration: Levels that held for months > levels from days ago
3. Volume at level: Higher volume = stronger level
4. Speed of move to level: Quick moves are less significant than gradual approaches
5. Confluence: Multiple indicators aligning at the same level

TYPES OF SUPPORT/RESISTANCE:
- Horizontal: Fixed price levels (all-time highs, previous swing highs/lows)
- Dynamic: Moving averages, trendlines, Bollinger Bands
- Fibonacci: 0.382, 0.5, 0.618, 0.786 retracement levels
- Pivot Points: Calculated from prior period's H, L, C
- Volume Profile: High Volume Nodes (HVN) and Low Volume Nodes (LVN)
- Order Blocks: Zones where large orders were placed (institutional S/R)

POLARITY PRINCIPLE (Role Reversal):
- When support breaks, it becomes resistance
- When resistance breaks, it becomes support
- This is one of the most reliable concepts in TA
- The retest of the broken level provides the best entry opportunity

TRADING WITH S/R:
- Buy at support with stop below; target at resistance
- Sell at resistance with stop above; target at support
- Breakout trades: Enter on confirmed break, wait for retest, enter retest
- The "ping" (single touch) is less reliable than accumulation/distribution zones

MARKET STRUCTURE:
- Higher highs + higher lows = uptrend (bullish structure)
- Lower highs + lower lows = downtrend (bearish structure)
- Equal highs + equal lows = range/consolidation
- Structure break = potential trend change""",
        "metadata": {
            "category": "support_resistance",
            "subcategory": "core_concepts",
            "type": "foundational",
            "reliability": 0.90,
            "source": "classic_ta",
        },
        "doc_id": "ta:sr:core_concepts",
    },
    {
        "content": """FIBONACCI RETRACEMENT AND EXTENSION METHODOLOGY

Fibonacci tools are based on the Fibonacci sequence (0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144...) and the golden ratio (1.618). These ratios appear throughout nature and financial markets exhibit self-similar fractal behavior.

KEY FIBONACCI LEVELS:

RETRACEMENT (pullback levels):
- 0.236 (23.6%): Shallow retracement — strong trend
- 0.382 (38.2%): Moderate retracement — most common in healthy trends
- 0.500 (50.0%): Half retracement — psychological level (not true fib but widely followed)
- 0.618 (61.8%): Golden ratio — most important fib level, "golden pocket"
- 0.786 (78.6%): Deep retracement — trend in danger

EXTENSION (target levels):
- 1.272 (127.2%): First extension target
- 1.618 (161.8%): Golden ratio extension — primary target
- 2.000 (200.0%): Double extension
- 2.618 (261.8%): Extended target

GOLDEN POCKET (0.618-0.65):
- The zone between 61.8% and 65% retracement
- Most reliable reversal zone for trend continuation entries
- Where price often finds support/resistance before continuing

BEST PRACTICES:
1. Draw from significant swing low to swing high (uptrend retracement) or high to low (downtrend retracement)
2. Use only significant swings — not minor wiggles
3. Confluence is key: Multiple fib levels from different swings aligning at same price
4. Combine with horizontal S/R for stronger levels
5. The 0.618 level is most reliable when it aligns with prior S/R

TRADING STRATEGY:
- Entry: At confluence of fib level + horizontal S/R + candlestick confirmation
- Primary target: 1.272 or 1.618 extension
- Stop: Just beyond the next fib level or the swing origin
- Best setups: 0.618 retrace to prior S/R with bullish engulfing/hammer
- Avoid: Trading every fib level in isolation

FIBONACCI TIME ZONES:
- Vertical lines based on Fibonacci numbers (1, 2, 3, 5, 8, 13, 21...)
- Identify potential reversal dates
- Less reliable than price-based fibs but useful for timing confluence

FIBONACCI FANS AND ARCS:
- Fan: Diagonal lines from a pivot at Fibonacci angles
- Arc: Curved lines showing potential S/R based on time and price
- Both are supplementary tools, not primary

COMMON MISTAKES:
- Drawing from wrong swings (minor pivots instead of major ones)
- Expecting exact hits (treat fibs as ZONES, not exact prices)
- Using too many fib levels creating "analysis paralysis"
- Ignoring higher timeframe structure""",
        "metadata": {
            "category": "support_resistance",
            "subcategory": "fibonacci",
            "type": "mathematical",
            "reliability": 0.65,
            "source": "classic_ta",
        },
        "doc_id": "ta:sr:fibonacci",
    },
    # ── Volume Analysis & Wyckoff ───────────────────────────────
    {
        "content": """VOLUME ANALYSIS METHODOLOGY

Volume is the number of shares/contracts/tokens traded during a given period. It confirms price action and reveals the strength behind moves.

CORE PRINCIPLES:
1. Volume confirms trend — increasing volume in the direction of the trend confirms it
2. Volume precedes price — volume changes often lead price changes
3. Volume climaxes — extreme volume can signal trend exhaustion

KEY VOLUME PATTERNS:

1. VOLUME CONFIRMATION:
   - Uptrend + increasing volume: Strong trend (healthy)
   - Uptrend + decreasing volume: Weak trend (distribution)
   - Downtrend + increasing volume: Strong selling (capitulation potential)
   - Downtrend + decreasing volume: Selling exhaustion (accumulation potential)

2. VOLUME CLIMAX:
   - Buying climax: Extreme volume at the top of an uptrend → potential reversal
   - Selling climax: Extreme volume at the bottom of a downtrend → potential reversal
   - Often accompanied by wide-range candles

3. VOLUME DRY-UP:
   - Very low volume during consolidation
   - Indicates indecision and potential for strong breakout
   - "The calm before the storm"

4. EFFORT VS RESULT (Wyckoff):
   - High volume + small price change = Effort without result (reversal signal)
   - Low volume + large price change = Result without effort (unsustainable)
   - This is one of the most powerful volume analysis concepts

5. VOLUME SPREAD ANALYSIS (VSA):
   - Wide spread + high volume on up bar: Strong buying (bullish)
   - Wide spread + high volume on down bar: Strong selling (bearish)
   - Narrow spread + high volume: Stopping volume (potential reversal)
   - Wide spread + low volume: No commitment (likely false move)

VOLUME PROFILE:
- Point of Control (POC): Price with the most volume — strongest S/R level
- Value Area: 70% of volume around POC
- High Volume Node (HVN): Price acceptance zone
- Low Volume Node (LVN): Price rejection zone — fast moves through these areas

ON-BALANCE VOLUME (OBV):
- Running total of volume: Add on up days, subtract on down days
- OBV breaking to new highs before price: Leading bullish signal
- OBV divergence: Price up, OBV flat/down → distribution

VOLUME-WEIGHTED AVERAGE PRICE (VWAP):
- Average price weighted by volume
- Institutional benchmark for "fair value"
- Above VWAP = bullish intraday; Below VWAP = bearish intraday

CRYPTO-SPECIFIC VOLUME ANALYSIS:
- Wash trading detection: Flat volume, no price movement
- Fake volume on DEXs: Check volume/liquidity ratio (> 50x is suspicious)
- Exchange volume comparison: Discrepancies between exchanges = warning
- Bot patterns: Identical volume bars repeated = automated trading""",
        "metadata": {
            "category": "volume_analysis",
            "subcategory": "core_concepts",
            "type": "foundational",
            "reliability": 0.85,
            "source": "wyckoff_vsa",
        },
        "doc_id": "ta:volume:core_concepts",
    },
    {
        "content": """WYCKOFF METHODOLOGY — MARKET STRUCTURE AND ACCUMULATION/DISTRIBUTION

The Wyckoff Method, developed by Richard Wyckoff in the early 1900s, is a comprehensive approach to understanding market structure through the lens of supply and demand. It's based on the idea that markets are manipulated by "Composite Operators" (smart money/institutions) who accumulate at lows and distribute at highs.

THE WYCKOFF MARKET CYCLE:
1. Accumulation (TR — Trading Range at bottom)
2. Markup (Uptrend)
3. Distribution (TR — Trading Range at top)
4. Markdown (Downtrend)

ACCUMULATION SCHEMATIC (Phases A-E):
- Phase A: Stopping of the downtrend. Preliminary Support (PS) and Selling Climax (SC) occur. Automatic Rally (AR) follows. Secondary Test (ST) back toward SC area.
- Phase B: Building a cause. Price oscillates within the trading range. Smart money accumulating.
- Phase C: Spring or Shakeout. Final test of support that traps late shorts and tests remaining supply. Often undercuts prior lows.
- Phase D: Markup begins. Last Point of Support (LPS) forms. Sign of Strength (SOS) on increased volume.
- Phase E: Trend emerges. Price leaves the trading range and trends upward.

DISTRIBUTION SCHEMATIC (Phases A-E):
- Phase A: Stopping of uptrend. Preliminary Supply (PSY) and Buying Climax (BC). Automatic Reaction (AR). Secondary Test (ST).
- Phase B: Building a cause for decline. Smart money distributes to late buyers.
- Phase C: Upthrust After Distribution (UTAD). Final test of resistance trapping late buyers.
- Phase D: Markdown begins. Last Point of Supply (LPSY). Sign of Weakness (SOW).
- Phase E: Downtrend emerges.

EFFORT VS RESULT (Wyckoff's Law):
- Wide spread + high volume: Effort matches result — valid move
- Narrow spread + high volume: Effort exceeds result — potential reversal (absorption)
- Wide spread + low volume: Result exceeds effort — unsustainable move
- This law is the foundation for identifying accumulation and distribution

CAUSE AND EFFECT:
- Cause: Time spent in accumulation/distribution (the trading range)
- Effect: The subsequent trend move
- The longer the cause (more time in TR), the larger the effect (trend magnitude)
- Use Point & Figure charts to measure cause and project targets

SUPPLY AND DEMAND:
- Supply > Demand: Price falls
- Demand > Supply: Price rises
- Wyckoff teaches you to READ the imbalance, not predict it

CRYPTO APPLICATION:
- Wyckoff works exceptionally well in crypto due to:
  * High retail participation (smart money exploitation is more visible)
  * Clear accumulation/distribution phases in altcoins
  * Spring/shakeout patterns are common before major moves
  * UTAD patterns visible before major selloffs""",
        "metadata": {
            "category": "volume_analysis",
            "subcategory": "wyckoff_method",
            "type": "market_structure",
            "reliability": 0.82,
            "source": "richard_wyckoff",
        },
        "doc_id": "ta:volume:wyckoff",
    },
    # ── Candlestick Patterns ────────────────────────────────────
    {
        "content": """ESSENTIAL CANDLESTICK PATTERNS FOR TRADING

Candlestick patterns provide visual insight into the battle between buyers and sellers. Each candle tells a story of who controlled the session.

SINGLE CANDLE PATTERNS:

1. DOJI (Indecision):
   - Open ≈ Close, with wicks on both sides
   - Dragonfly Doji: Long lower wick, no upper — bullish reversal signal
   - Gravestone Doji: Long upper wick, no lower — bearish reversal signal
   - Long-legged Doji: Both wicks long — extreme indecision

2. HAMMER / HANGING MAN:
   - Small body at top, long lower wick (2-3x the body)
   - Hammer (after downtrend): Bullish reversal
   - Hanging Man (after uptrend): Bearish reversal
   - Color doesn't matter, but green hammer is slightly more bullish

3. SHOOTING STAR / INVERTED HAMMER:
   - Small body at bottom, long upper wick
   - Shooting Star (after uptrend): Bearish reversal
   - Inverted Hammer (after downtrend): Bullish reversal

4. MARUBOZU (Strong Momentum):
   - No or very small wicks — body fills entire range
   - Green Marubozu: Strong bullish momentum (buyers in full control)
   - Red Marubozu: Strong bearish momentum (sellers in full control)

TWO-CANDLE PATTERNS:

5. ENGULFING:
   - Bullish Engulfing: Green candle completely engulfs prior red candle (close > prior open, open < prior close)
   - Bearish Engulfing: Red candle completely engulfs prior green candle
   - More reliable when it occurs at support/resistance

6. HARAMI (Inside Bar):
   - Small candle contained within prior candle's range
   - Indicates momentum pause / potential reversal
   - More significant after strong directional moves

7. TWEEZER TOPS/BOTTOMS:
   - Two candles with matching highs (tweezer top) or lows (tweezer bottom)
   - Indicates support or resistance has been tested and held
   - The second candle is often a reversal candle

THREE-CANDLE PATTERNS:

8. MORNING STAR (Bullish Reversal):
   - First: Long red candle (downtrend)
   - Second: Small candle (indecision, gap down ideal)
   - Third: Long green candle closing into first candle's body
   - Most reliable reversal pattern

9. EVENING STAR (Bearish Reversal):
   - First: Long green candle (uptrend)
   - Second: Small candle (indecision, gap up ideal)
   - Third: Long red candle closing into first candle's body

10. THREE WHITE SOLDIERS (Bullish Continuation):
    - Three consecutive long green candles
    - Each opens within prior body, closes near high
    - Strong bullish momentum

11. THREE BLACK CROWS (Bearish Continuation):
    - Three consecutive long red candles
    - Each opens within prior body, closes near low
    - Strong bearish momentum

CONFIRMATION RULES:
- Never trade a candlestick pattern in isolation
- Always check the prevailing trend (reversal patterns need a trend to reverse)
- Volume confirmation increases reliability significantly
- Higher timeframe patterns (> 4H) are more reliable than lower timeframe ones (< 1H)
- Wait for the NEXT candle to confirm before entering""",
        "metadata": {
            "category": "candlestick_patterns",
            "subcategory": "essential_patterns",
            "type": "price_action",
            "reliability": 0.70,
            "source": "steve_nison",
        },
        "doc_id": "ta:candles:essential_patterns",
    },
    # ── Risk Management ─────────────────────────────────────────
    {
        "content": """RISK MANAGEMENT AND POSITION SIZING FOR CRYPTO TRADING

Risk management is the MOST IMPORTANT aspect of trading. Without it, even a 90% win rate strategy can blow up an account. Professional traders focus on risk first, returns second.

THE 1% RULE:
- Never risk more than 1-2% of your total portfolio on any single trade
- If portfolio is $10,000, max risk per trade is $100-$200
- This ensures you can survive extended losing streaks
- Even 20 consecutive losses won't destroy the account at 1% risk

POSITION SIZING FORMULA:
Position Size = (Account Risk Amount) / (Entry Price - Stop Loss Price)

Example:
- Account: $10,000
- Risk per trade: 1% ($100)
- Entry: $100
- Stop loss: $95
- Risk per unit: $5
- Position size: $100 / $5 = 20 units = $2,000 position

RISK-REWARD RATIO (R:R):
- Minimum acceptable R:R for most traders: 1:2 (risk $1 to make $2)
- With 1:2 R:R, you only need a 33% win rate to break even
- With 1:3 R:R, you only need a 25% win rate
- Always calculate R:R BEFORE entering a trade
- Don't take trades with less than 1:1.5 R:R

MAXIMUM DRAWDOWN LIMITS:
- Set a daily loss limit (e.g., 3% of account)
- Set a weekly loss limit (e.g., 6% of account)
- If limits are hit: STOP trading for the period. No revenge trading.
- Track and respect these limits religiously

CORRELATION RISK:
- Multiple positions in correlated assets = concentrated risk
- Crypto is highly correlated (most altcoins follow BTC)
- Limit total crypto exposure during high-correlation periods
- Use a correlation matrix to understand your true exposure

CRYPTO-SPECIFIC RISKS:
1. Liquidity risk: Low liquidity tokens can have massive slippage
2. Smart contract risk: Hacks, exploits, rug pulls
3. Exchange risk: CEX hacks, withdrawal freezes
4. Regulatory risk: Sudden bans or restrictions
5. DeFi protocol risk: Oracle manipulation, flash loan attacks
6. Rug pull risk: New tokens with anonymous teams, low liquidity
7. Volatility risk: Crypto can move 20%+ in hours

TRADE MANAGEMENT:
- Never move your stop loss further from entry (hoping it comes back)
- Trail stops as price moves in your favor (protect profits)
- Take partial profits at key levels (e.g., 50% at 1:1, 30% at 1:2, 20% runner)
- Don't add to losing positions (averaging down is a trap)

PSYCHOLOGICAL RISK MANAGEMENT:
- FOMO (Fear of Missing Out): Leads to chasing entries at tops
- FUD (Fear, Uncertainty, Doubt): Leads to panic selling at bottoms
- Revenge trading: Trying to recover losses quickly — BIGGEST account killer
- Overconfidence after wins: Increasing size after a streak (leads to giving back gains)
- The best traders are BORED — consistent small wins beat occasional big wins

TRADING JOURNAL:
- Log every trade: Entry, exit, size, R:R, outcome, screenshot, notes
- Review weekly: Identify patterns in wins and losses
- Track metrics: Win rate, average win, average loss, profit factor, expectancy""",
        "metadata": {
            "category": "risk_management",
            "subcategory": "position_sizing",
            "type": "foundational",
            "reliability": 0.95,
            "source": "professional_trading",
        },
        "doc_id": "ta:risk:position_sizing",
    },
]
