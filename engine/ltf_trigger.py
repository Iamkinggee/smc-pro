"""
ltf_trigger.py — Lower Timeframe (1m / 5m) Entry Trigger Detector

Watches for entry confirmation signals on lower timeframes (1m, 5m) after
price has entered a Higher Timeframe zone (Order Block or FVG).

Detects two key SMC confirmation patterns:

  BOS (Break of Structure):
    A candle closes beyond the most recent swing high (bullish BOS) or
    swing low (bearish BOS) — confirming continuation in the HTF bias direction.

  CHOCH (Change of Character):
    A candle closes beyond the last opposing swing — signaling a potential
    reversal or trend flip on the LTF. Used as the primary entry trigger.

When a CHOCH or BOS fires while price is inside an HTF zone, the scorer
is called to evaluate the full confluence picture.
"""

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class StructureEvent:
    """Fired when a BOS or CHOCH is detected on LTF."""
    symbol:     str
    timeframe:  str
    event_type: Literal["BOS", "CHOCH"]
    direction:  Literal["bullish", "bearish"]
    price:      float      # close price that triggered the event
    swing_ref:  float      # the swing high/low that was broken
    open_time:  int        # open time of the triggering candle


class LTFTrigger:
    """
    Runs on every closed 1m or 5m candle.
    Detects BOS and CHOCH events and returns them for the scorer to evaluate.
    """

    # Number of candles to look back when identifying "the last swing"
    SWING_LOOKBACK = 10

    # Minimum candles needed before we start detecting
    MIN_CANDLES = 15

    def check(
        self,
        symbol: str,
        timeframe: str,
        candles: list[dict],
        htf_bias: Literal["bullish", "bearish", "neutral"] = "neutral",
    ) -> StructureEvent | None:
        """
        Analyze the latest closed candles and return a StructureEvent if
        a BOS or CHOCH has formed, or None.

        Args:
            symbol:    Trading pair, e.g. "BTCUSDT"
            timeframe: "1m" or "5m"
            candles:   Closed candle list, oldest first
            htf_bias:  Current bias from HTF analyzer (used to classify CHOCH vs BOS)

        Returns:
            A StructureEvent if triggered, otherwise None.
        """
        if len(candles) < self.MIN_CANDLES:
            return None

        latest = candles[-1]

        # ── Bullish BOS / CHOCH ──────────────────────────────
        # Triggered when price closes above the last swing high
        swing_high = self._get_swing_high(candles[:-1])
        if swing_high and latest["close"] > swing_high:
            event_type = "BOS" if htf_bias == "bullish" else "CHOCH"
            logger.info(
                f"[LTF] {symbol} {timeframe} | {event_type} bullish | "
                f"close={latest['close']:.4f} > swing_high={swing_high:.4f}"
            )
            return StructureEvent(
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                direction="bullish",
                price=latest["close"],
                swing_ref=swing_high,
                open_time=latest["open_time"],
            )

        # ── Bearish BOS / CHOCH ──────────────────────────────
        # Triggered when price closes below the last swing low
        swing_low = self._get_swing_low(candles[:-1])
        if swing_low and latest["close"] < swing_low:
            event_type = "BOS" if htf_bias == "bearish" else "CHOCH"
            logger.info(
                f"[LTF] {symbol} {timeframe} | {event_type} bearish | "
                f"close={latest['close']:.4f} < swing_low={swing_low:.4f}"
            )
            return StructureEvent(
                symbol=symbol,
                timeframe=timeframe,
                event_type=event_type,
                direction="bearish",
                price=latest["close"],
                swing_ref=swing_low,
                open_time=latest["open_time"],
            )

        return None

    def check_liquidity_sweep(
        self,
        candles: list[dict],
        pool_price: float,
        pool_type: Literal["equal_highs", "equal_lows"],
    ) -> bool:
        """
        Detect if the last candle swept a liquidity pool then closed back inside.

        A sweep (stop hunt) occurs when price briefly exceeds a liquidity level
        (triggering stop-losses) but then closes back on the other side —
        indicating the sweep was engineered to collect liquidity before reversing.

        Args:
            candles:    Recent candle list
            pool_price: The liquidity pool level
            pool_type:  Whether it's equal highs (swept from below) or equal lows

        Returns:
            True if a sweep occurred on the last candle.
        """
        if len(candles) < 2:
            return False

        last = candles[-1]

        if pool_type == "equal_highs":
            # Wick exceeded pool level but close is back below it
            swept = last["high"] > pool_price and last["close"] < pool_price
        else:  # equal_lows
            # Wick exceeded pool level (to downside) but close is back above it
            swept = last["low"] < pool_price and last["close"] > pool_price

        if swept:
            logger.info(
                f"[LTF] Liquidity sweep detected | pool={pool_price:.4f} "
                f"type={pool_type} | H={last['high']:.4f} L={last['low']:.4f} C={last['close']:.4f}"
            )

        return swept

    # ─── Helpers ──────────────────────────────────────────────

    def _get_swing_high(self, candles: list[dict]) -> float | None:
        """
        Find the most recent swing high in the last SWING_LOOKBACK candles.
        A swing high is a candle whose high is greater than the highs of
        the candles immediately before and after it.
        """
        lookback = candles[-self.SWING_LOOKBACK:]
        if len(lookback) < 3:
            return None

        for i in range(len(lookback) - 2, 0, -1):
            if (lookback[i]["high"] > lookback[i - 1]["high"] and
                    lookback[i]["high"] > lookback[i + 1]["high"]):
                return lookback[i]["high"]

        # Fallback: use the highest high in the window
        return max(c["high"] for c in lookback)

    def _get_swing_low(self, candles: list[dict]) -> float | None:
        """
        Find the most recent swing low in the last SWING_LOOKBACK candles.
        """
        lookback = candles[-self.SWING_LOOKBACK:]
        if len(lookback) < 3:
            return None

        for i in range(len(lookback) - 2, 0, -1):
            if (lookback[i]["low"] < lookback[i - 1]["low"] and
                    lookback[i]["low"] < lookback[i + 1]["low"]):
                return lookback[i]["low"]

        # Fallback: use the lowest low in the window
        return min(c["low"] for c in lookback)