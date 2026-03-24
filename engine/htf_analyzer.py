"""
htf_analyzer.py — Higher Timeframe (1H / 4H) Structure Analyzer

Detects the core SMC structures that define market bias and key zones:

  1. Order Blocks (OB)      — last opposing candle before a strong impulse
  2. Fair Value Gaps (FVG)  — price imbalances (gaps between candle wicks)
  3. Liquidity Pools        — clusters of equal highs or equal lows
  4. Market Bias            — overall directional bias via Break of Structure

These structures are stored in memory and queried by the confluence scorer
and LTF trigger to determine signal validity.
"""

import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ─── Data Structures ──────────────────────────────────────────

@dataclass
class OrderBlock:
    symbol:    str
    timeframe: str
    direction: Literal["bullish", "bearish"]
    high:      float
    low:       float
    open_time: int
    mitigated: bool = False    # True once price has traded through the OB

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2

    def contains_price(self, price: float) -> bool:
        """Return True if price is within the order block zone."""
        return self.low <= price <= self.high

    def is_tapped(self, current_low: float, current_high: float) -> bool:
        """Return True if the current candle has tapped into this OB."""
        if self.direction == "bullish":
            return current_low <= self.high and current_low >= self.low
        else:
            return current_high >= self.low and current_high <= self.high


@dataclass
class FairValueGap:
    symbol:    str
    timeframe: str
    direction: Literal["bullish", "bearish"]
    top:       float   # upper edge of the gap
    bottom:    float   # lower edge of the gap
    open_time: int
    filled:    bool = False

    def contains_price(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass
class LiquidityPool:
    symbol:    str
    timeframe: str
    pool_type: Literal["equal_highs", "equal_lows"]
    price:     float   # the clustered level
    count:     int     # how many touches
    swept:     bool = False

    def is_near(self, price: float, tolerance_pct: float = 0.0015) -> bool:
        """Return True if price is within tolerance of the pool level."""
        return abs(price - self.price) / self.price <= tolerance_pct


@dataclass
class HTFState:
    """All detected structures for a single (symbol, timeframe)."""
    symbol:          str
    timeframe:       str
    bias:            Literal["bullish", "bearish", "neutral"] = "neutral"
    order_blocks:    list[OrderBlock]  = field(default_factory=list)
    fvgs:            list[FairValueGap] = field(default_factory=list)
    liquidity_pools: list[LiquidityPool] = field(default_factory=list)
    last_swing_high: float = 0.0
    last_swing_low:  float = float("inf")


# ─── Analyzer ─────────────────────────────────────────────────

class HTFAnalyzer:
    """
    Runs on every closed 1H or 4H candle for a symbol.
    Updates and returns the full HTFState for that symbol/timeframe.
    """

    # Minimum body-to-range ratio for a candle to be considered an impulse
    IMPULSE_BODY_RATIO = 0.5

    # Minimum move (as % of price) to qualify as a structure-breaking impulse
    IMPULSE_MIN_PCT = 0.002   # 0.2%

    # Equal high/low tolerance — within 0.15% is considered "equal"
    EQUAL_LEVEL_TOLERANCE = 0.0015

    # Minimum number of touches to form a liquidity pool
    MIN_POOL_TOUCHES = 2

    def __init__(self):
        # One HTFState per (symbol, timeframe)
        self._states: dict[tuple, HTFState] = {}

    def analyze(self, symbol: str, timeframe: str, candles: list[dict]) -> HTFState:
        """
        Run full HTF analysis on the latest closed candles.

        Args:
            symbol:    e.g. "BTCUSDT"
            timeframe: "1h" or "4h"
            candles:   list of candle dicts, oldest first, from CandleBuilder

        Returns:
            Updated HTFState with all detected structures.
        """
        if len(candles) < 10:
            logger.debug(f"[HTF] Not enough candles for {symbol} {timeframe} ({len(candles)})")
            return self._get_or_create_state(symbol, timeframe)

        key = (symbol, timeframe)
        state = self._get_or_create_state(symbol, timeframe)

        state.bias            = self._detect_bias(candles)
        state.order_blocks    = self._detect_order_blocks(symbol, timeframe, candles)
        state.fvgs            = self._detect_fvgs(symbol, timeframe, candles)
        state.liquidity_pools = self._detect_liquidity_pools(symbol, timeframe, candles)
        state.last_swing_high = self._get_last_swing_high(candles)
        state.last_swing_low  = self._get_last_swing_low(candles)

        self._states[key] = state

        logger.info(
            f"[HTF] {symbol} {timeframe} | bias={state.bias} | "
            f"OBs={len(state.order_blocks)} | FVGs={len(state.fvgs)} | "
            f"pools={len(state.liquidity_pools)}"
        )

        return state

    def get_state(self, symbol: str, timeframe: str) -> HTFState | None:
        """Return the last computed state for a symbol/timeframe."""
        return self._states.get((symbol, timeframe))

    def get_nearest_ob(self, symbol: str, timeframe: str, price: float) -> OrderBlock | None:
        """Return the closest unmitigated OB to the current price."""
        state = self.get_state(symbol, timeframe)
        if not state:
            return None
        active_obs = [ob for ob in state.order_blocks if not ob.mitigated]
        if not active_obs:
            return None
        return min(active_obs, key=lambda ob: abs(ob.midpoint - price))

    # ─── Bias Detection ───────────────────────────────────────

    def _detect_bias(self, candles: list[dict]) -> Literal["bullish", "bearish", "neutral"]:
        """
        Determine market bias by analyzing swing structure over the last 50 candles.
        Bullish: series of higher highs and higher lows (BOS to upside).
        Bearish: series of lower highs and lower lows (BOS to downside).
        """
        if len(candles) < 20:
            return "neutral"

        # Use last 50 candles for bias
        recent = candles[-50:]
        swing_highs = self._find_swing_highs(recent)
        swing_lows  = self._find_swing_lows(recent)

        # Fallback: if swing detection finds fewer than 2 swings,
        # use a simpler slope comparison on the last vs first close
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            if len(recent) >= 20:
                first_close = sum(c["close"] for c in recent[:5]) / 5
                last_close  = sum(c["close"] for c in recent[-5:]) / 5
                if last_close > first_close * 1.001:
                    return "bullish"
                if last_close < first_close * 0.999:
                    return "bearish"
            return "neutral"

        # Compare the last two swing highs and lows
        hh = swing_highs[-1] > swing_highs[-2]   # higher high
        hl = swing_lows[-1]  > swing_lows[-2]    # higher low
        lh = swing_highs[-1] < swing_highs[-2]   # lower high
        ll = swing_lows[-1]  < swing_lows[-2]    # lower low

        if hh and hl:
            return "bullish"
        if lh and ll:
            return "bearish"

        # Secondary fallback: slope of closes
        first_close = sum(c["close"] for c in recent[:5]) / 5
        last_close  = sum(c["close"] for c in recent[-5:]) / 5
        if last_close > first_close * 1.001:
            return "bullish"
        if last_close < first_close * 0.999:
            return "bearish"
        return "neutral"

    # ─── Order Block Detection ────────────────────────────────

    def _detect_order_blocks(self, symbol: str, timeframe: str, candles: list[dict]) -> list[OrderBlock]:
        """
        An Order Block is the last opposing candle immediately before a strong
        impulse move that breaks structure.

        Bullish OB: a bearish (red) candle that precedes a strong bullish impulse
                    which breaks the last swing high.
        Bearish OB: a bullish (green) candle that precedes a strong bearish impulse
                    which breaks the last swing low.

        We look back over the last 100 candles and collect the 5 most recent
        unmitigated OBs in each direction.
        """
        obs: list[OrderBlock] = []
        lookback = candles[-100:] if len(candles) >= 100 else candles

        for i in range(1, len(lookback) - 3):
            prev = lookback[i - 1]
            ob_c = lookback[i]         # candidate OB candle
            next1 = lookback[i + 1]
            next2 = lookback[i + 2]

            # ── Bullish OB ──────────────────────────────────────
            # Condition: ob_c is bearish, next candles form a strong bullish impulse
            if self._is_bearish(ob_c):
                impulse_high = max(next1["high"], next2["high"])
                impulse_move = (impulse_high - ob_c["low"]) / ob_c["low"]

                if impulse_move >= self.IMPULSE_MIN_PCT and self._is_impulsive(next1):
                    # Check if the impulse breaks the most recent swing high
                    prior_swing_high = self._get_last_swing_high(lookback[:i])
                    if impulse_high > prior_swing_high:
                        obs.append(OrderBlock(
                            symbol=symbol,
                            timeframe=timeframe,
                            direction="bullish",
                            high=ob_c["high"],
                            low=ob_c["low"],
                            open_time=ob_c["open_time"],
                        ))

            # ── Bearish OB ──────────────────────────────────────
            # Condition: ob_c is bullish, next candles form a strong bearish impulse
            elif self._is_bullish(ob_c):
                impulse_low  = min(next1["low"], next2["low"])
                impulse_move = (ob_c["high"] - impulse_low) / ob_c["high"]

                if impulse_move >= self.IMPULSE_MIN_PCT and self._is_impulsive(next1, "bearish"):
                    prior_swing_low = self._get_last_swing_low(lookback[:i])
                    if impulse_low < prior_swing_low:
                        obs.append(OrderBlock(
                            symbol=symbol,
                            timeframe=timeframe,
                            direction="bearish",
                            high=ob_c["high"],
                            low=ob_c["low"],
                            open_time=ob_c["open_time"],
                        ))

        # Deduplicate by open_time and keep the 5 most recent of each direction
        seen = set()
        deduped = []
        for ob in reversed(obs):
            key = (ob.direction, ob.open_time)
            if key not in seen:
                seen.add(key)
                deduped.append(ob)

        bullish_obs = [ob for ob in deduped if ob.direction == "bullish"][:5]
        bearish_obs = [ob for ob in deduped if ob.direction == "bearish"][:5]

        return bullish_obs + bearish_obs

    # ─── Fair Value Gap Detection ─────────────────────────────

    def _detect_fvgs(self, symbol: str, timeframe: str, candles: list[dict]) -> list[FairValueGap]:
        """
        A Fair Value Gap (FVG / Imbalance) is a 3-candle pattern where there is
        a gap between candle[i].high and candle[i+2].low (bullish FVG) or
        between candle[i].low and candle[i+2].high (bearish FVG).

        This represents a price area that was skipped over quickly due to strong
        momentum — institutions typically return to fill these imbalances.
        """
        fvgs: list[FairValueGap] = []
        lookback = candles[-50:] if len(candles) >= 50 else candles

        for i in range(len(lookback) - 2):
            c1, c2, c3 = lookback[i], lookback[i + 1], lookback[i + 2]

            # Bullish FVG: gap between c1 high and c3 low (c2 is the impulse up)
            if c3["low"] > c1["high"]:
                fvgs.append(FairValueGap(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction="bullish",
                    top=c3["low"],
                    bottom=c1["high"],
                    open_time=c2["open_time"],
                ))

            # Bearish FVG: gap between c3 high and c1 low (c2 is the impulse down)
            elif c3["high"] < c1["low"]:
                fvgs.append(FairValueGap(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction="bearish",
                    top=c1["low"],
                    bottom=c3["high"],
                    open_time=c2["open_time"],
                ))

        # Keep only the 10 most recent FVGs
        return fvgs[-10:]

    # ─── Liquidity Pool Detection ─────────────────────────────

    def _detect_liquidity_pools(self, symbol: str, timeframe: str, candles: list[dict]) -> list[LiquidityPool]:
        """
        Liquidity pools form where multiple candles share very similar highs or lows.
        These equal levels attract stop-loss orders from retail traders, making them
        prime targets for institutional liquidity grabs before reversals.

        Algorithm:
          1. Collect all candle highs and lows from last 100 candles
          2. Cluster levels within EQUAL_LEVEL_TOLERANCE of each other
          3. Any cluster with 2+ touches = liquidity pool
        """
        lookback = candles[-100:] if len(candles) >= 100 else candles
        pools: list[LiquidityPool] = []

        # ── Equal Highs ──────────────────────────────────────
        highs = [(i, c["high"]) for i, c in enumerate(lookback)]
        high_clusters = self._cluster_levels(highs, self.EQUAL_LEVEL_TOLERANCE)

        for level, count in high_clusters:
            if count >= self.MIN_POOL_TOUCHES:
                pools.append(LiquidityPool(
                    symbol=symbol,
                    timeframe=timeframe,
                    pool_type="equal_highs",
                    price=level,
                    count=count,
                ))

        # ── Equal Lows ──────────────────────────────────────
        lows = [(i, c["low"]) for i, c in enumerate(lookback)]
        low_clusters = self._cluster_levels(lows, self.EQUAL_LEVEL_TOLERANCE)

        for level, count in low_clusters:
            if count >= self.MIN_POOL_TOUCHES:
                pools.append(LiquidityPool(
                    symbol=symbol,
                    timeframe=timeframe,
                    pool_type="equal_lows",
                    price=level,
                    count=count,
                ))

        return pools

    # ─── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _is_bullish(c: dict) -> bool:
        return c["close"] > c["open"]

    @staticmethod
    def _is_bearish(c: dict) -> bool:
        return c["close"] < c["open"]

    @staticmethod
    def _is_impulsive(c: dict, direction: str = "bullish") -> bool:
        """True if candle body is at least 50% of total range."""
        total_range = c["high"] - c["low"]
        if total_range == 0:
            return False
        body = abs(c["close"] - c["open"])
        body_ratio = body / total_range
        if direction == "bullish":
            return body_ratio >= 0.5 and c["close"] > c["open"]
        else:
            return body_ratio >= 0.5 and c["close"] < c["open"]

    @staticmethod
    def _find_swing_highs(candles: list[dict], lookback: int = 3) -> list[float]:
        """Find swing highs: candles whose high is the highest in a window."""
        highs = []
        for i in range(lookback, len(candles) - lookback):
            window = candles[i - lookback: i + lookback + 1]
            if candles[i]["high"] == max(c["high"] for c in window):
                highs.append(candles[i]["high"])
        return highs

    @staticmethod
    def _find_swing_lows(candles: list[dict], lookback: int = 3) -> list[float]:
        """Find swing lows: candles whose low is the lowest in a window."""
        lows = []
        for i in range(lookback, len(candles) - lookback):
            window = candles[i - lookback: i + lookback + 1]
            if candles[i]["low"] == min(c["low"] for c in window):
                lows.append(candles[i]["low"])
        return lows

    @staticmethod
    def _get_last_swing_high(candles: list[dict], lookback: int = 3) -> float:
        """Return the most recent swing high from the candle list."""
        for i in range(len(candles) - lookback - 1, lookback - 1, -1):
            window = candles[max(0, i - lookback): i + lookback + 1]
            if candles[i]["high"] == max(c["high"] for c in window):
                return candles[i]["high"]
        return candles[-1]["high"] if candles else 0.0

    @staticmethod
    def _get_last_swing_low(candles: list[dict], lookback: int = 3) -> float:
        """Return the most recent swing low from the candle list."""
        for i in range(len(candles) - lookback - 1, lookback - 1, -1):
            window = candles[max(0, i - lookback): i + lookback + 1]
            if candles[i]["low"] == min(c["low"] for c in window):
                return candles[i]["low"]
        return candles[-1]["low"] if candles else float("inf")

    @staticmethod
    def _cluster_levels(indexed_prices: list[tuple], tolerance: float) -> list[tuple[float, int]]:
        """
        Group prices that are within tolerance% of each other.
        Returns list of (representative_level, count) tuples.
        """
        if not indexed_prices:
            return []

        prices = [p for _, p in indexed_prices]
        prices.sort()
        clusters: list[tuple[float, int]] = []
        i = 0

        while i < len(prices):
            group = [prices[i]]
            j = i + 1
            while j < len(prices) and abs(prices[j] - prices[i]) / prices[i] <= tolerance:
                group.append(prices[j])
                j += 1
            # Use the mean as the representative level
            level = sum(group) / len(group)
            clusters.append((level, len(group)))
            i = j

        return clusters

    def _get_or_create_state(self, symbol: str, timeframe: str) -> HTFState:
        key = (symbol, timeframe)
        if key not in self._states:
            self._states[key] = HTFState(symbol=symbol, timeframe=timeframe)
        return self._states[key]