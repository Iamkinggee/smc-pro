"""
candle_builder.py — OHLCV candle store

Receives raw kline updates from BinanceWSClient and maintains a rolling
window of closed candles per (symbol, timeframe). Notifies registered
listeners when a candle closes so the analyzers can run.

Each candle is stored as a plain dict:
  {
    "open_time": int (ms),
    "open":      float,
    "high":      float,
    "low":       float,
    "close":     float,
    "volume":    float,
    "close_time": int (ms),
  }
"""

import logging
from collections import deque
from typing import Callable

logger = logging.getLogger(__name__)

# How many closed candles to keep in memory per (symbol, timeframe)
MAX_CANDLES = 500


class CandleBuilder:
    """
    Maintains rolling candle windows for every (symbol, timeframe) pair.

    Usage:
        builder = CandleBuilder(on_candle_close=my_callback)
        # Feed kline updates from BinanceWSClient:
        await builder.on_kline(symbol, timeframe, kline_data)
    """

    def __init__(self, on_candle_close: Callable | None = None):
        """
        Args:
            on_candle_close: Optional async callback(symbol, timeframe, candle, history)
                             fired whenever a candle closes.
                             history is a list of the last MAX_CANDLES closed candles.
        """
        self.on_candle_close = on_candle_close

        # Closed candle history: { (symbol, tf): deque of candle dicts }
        self._history: dict[tuple, deque] = {}

        # Current (possibly unclosed) candle per stream
        self._current: dict[tuple, dict] = {}

    # ─── Public API ───────────────────────────────────────────

    async def on_kline(self, symbol: str, timeframe: str, kline: dict):
        """
        Process a kline update from BinanceWSClient.
        Called on every tick — both open (live) and closed candles.
        """
        key = (symbol, timeframe)

        if kline["is_closed"]:
            # Candle has closed — store it and notify listeners
            closed_candle = self._to_candle(kline)
            self._store(key, closed_candle)

            logger.debug(
                f"[CANDLE] {symbol} {timeframe} closed | "
                f"O={closed_candle['open']:.2f} H={closed_candle['high']:.2f} "
                f"L={closed_candle['low']:.2f} C={closed_candle['close']:.2f}"
            )

            if self.on_candle_close:
                history = self.get_candles(symbol, timeframe)
                await self.on_candle_close(symbol, timeframe, closed_candle, history)

            # Remove the current open tracker now that it's closed
            self._current.pop(key, None)

        else:
            # Candle is still open — update live state (used for intra-candle detection)
            self._current[key] = self._to_candle(kline)

    def get_candles(self, symbol: str, timeframe: str, n: int = MAX_CANDLES) -> list[dict]:
        """Return the last n closed candles for (symbol, timeframe), oldest first."""
        key = (symbol, timeframe)
        history = self._history.get(key, deque())
        candles = list(history)
        return candles[-n:] if n < len(candles) else candles

    def get_live_candle(self, symbol: str, timeframe: str) -> dict | None:
        """Return the currently forming (unclosed) candle, or None."""
        return self._current.get((symbol, timeframe))

    def ingest_historical(self, symbol: str, timeframe: str, candles: list[dict]):
        """
        Bulk-load historical candles from the REST backfill.
        Candles must be dicts with keys: open_time, open, high, low, close, volume, close_time.
        """
        key = (symbol, timeframe)
        if key not in self._history:
            self._history[key] = deque(maxlen=MAX_CANDLES)

        for c in candles:
            self._history[key].append(c)

        logger.info(f"[CANDLE] Loaded {len(candles)} historical candles for {symbol} {timeframe}")

    def has_enough_data(self, symbol: str, timeframe: str, minimum: int = 50) -> bool:
        """Check if we have enough candles to run analysis reliably."""
        return len(self.get_candles(symbol, timeframe)) >= minimum

    # ─── Private helpers ──────────────────────────────────────

    def _store(self, key: tuple, candle: dict):
        if key not in self._history:
            self._history[key] = deque(maxlen=MAX_CANDLES)
        self._history[key].append(candle)

    @staticmethod
    def _to_candle(kline: dict) -> dict:
        """Convert a raw kline dict to a clean candle dict."""
        return {
            "open_time":  kline["open_time"],
            "open":       kline["open"],
            "high":       kline["high"],
            "low":        kline["low"],
            "close":      kline["close"],
            "volume":     kline["volume"],
            "close_time": kline["close_time"],
        }