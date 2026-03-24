"""
backfill.py — Binance REST historical candle loader

Fetches the last 500 closed candles for each (symbol, timeframe) via the
Binance public REST API on engine startup. This ensures the HTF and LTF
analyzers have enough history to detect structures immediately, without
waiting for candles to stream in live.

Binance REST endpoint (no auth required):
  GET https://api.binance.com/api/v3/klines
  ?symbol=BTCUSDT&interval=1h&limit=500
"""

import asyncio
import logging
import time

import aiohttp

logger = logging.getLogger(__name__)

BINANCE_REST_BASE = "https://api.binance.com/api/v3"

# Map our internal timeframe names to Binance interval strings
TIMEFRAME_MAP = {
    "1m":  "1m",
    "5m":  "5m",
    "1h":  "1h",
    "4h":  "4h",
}


class Backfiller:
    """
    Fetches historical OHLCV data from Binance REST API and loads it
    into a CandleBuilder instance.
    """

    def __init__(self, candle_builder):
        self.candle_builder = candle_builder
        self._session: aiohttp.ClientSession | None = None

    async def run(self, pairs: list[str], timeframes: list[str] = None, limit: int = 500):
        """
        Fetch historical candles for all pairs × timeframes.

        Args:
            pairs:      List of symbols, e.g. ["BTCUSDT", "ETHUSDT"]
            timeframes: List of timeframes to fetch. Defaults to all 4.
            limit:      Number of candles to fetch per stream (max 1000).
        """
        if timeframes is None:
            timeframes = list(TIMEFRAME_MAP.keys())

        async with aiohttp.ClientSession() as session:
            self._session = session

            tasks = []
            for pair in pairs:
                for tf in timeframes:
                    tasks.append(self._fetch_and_load(pair, tf, limit))

            # Run all fetches concurrently but respect Binance rate limits:
            # public endpoints allow 1200 req/min. With chunking of 10,
            # we avoid hammering the API.
            chunk_size = 10
            for i in range(0, len(tasks), chunk_size):
                chunk = tasks[i:i + chunk_size]
                await asyncio.gather(*chunk)
                if i + chunk_size < len(tasks):
                    await asyncio.sleep(0.5)   # small pause between chunks

        logger.info(f"[BACKFILL] Complete — {len(pairs)} pairs × {len(timeframes)} timeframes loaded")

    async def _fetch_and_load(self, symbol: str, timeframe: str, limit: int):
        """Fetch candles for one symbol/timeframe and load into CandleBuilder."""
        interval = TIMEFRAME_MAP.get(timeframe, timeframe)
        url = f"{BINANCE_REST_BASE}/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}

        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"[BACKFILL] {symbol} {timeframe}: HTTP {resp.status} — {text[:200]}")
                    return

                raw = await resp.json()
                candles = [self._parse_kline(k) for k in raw]

                # Drop the last candle — it may still be forming
                if candles:
                    candles = candles[:-1]

                self.candle_builder.ingest_historical(symbol, timeframe, candles)

        except asyncio.TimeoutError:
            logger.warning(f"[BACKFILL] Timeout fetching {symbol} {timeframe} — skipping")
        except Exception as e:
            logger.error(f"[BACKFILL] Error fetching {symbol} {timeframe}: {e}")

    @staticmethod
    def _parse_kline(raw: list) -> dict:
        """
        Binance kline REST response format (each item is a list):
          [0]  open_time (ms)
          [1]  open
          [2]  high
          [3]  low
          [4]  close
          [5]  volume
          [6]  close_time (ms)
          [7]  quote_asset_volume
          [8]  number_of_trades
          [9]  taker_buy_base_asset_volume
          [10] taker_buy_quote_asset_volume
          [11] ignore
        """
        return {
            "open_time":  raw[0],
            "open":       float(raw[1]),
            "high":       float(raw[2]),
            "low":        float(raw[3]),
            "close":      float(raw[4]),
            "volume":     float(raw[5]),
            "close_time": raw[6],
        }