"""
ws_client.py — Binance WebSocket client

Connects to Binance public kline (candlestick) streams for multiple pairs
and timeframes. No API key required. Feeds raw kline data to CandleBuilder.

Binance stream format:
  wss://stream.binance.com:9443/stream?streams=btcusdt@kline_1m/btcusdt@kline_1h/...
"""

import asyncio
import json
import logging
import time
from typing import Callable

import websockets

logger = logging.getLogger(__name__)

# Binance public WebSocket base URL — no authentication needed
BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream"

# Timeframes the engine tracks
TIMEFRAMES = ["1m", "5m", "1h", "4h"]


class BinanceWSClient:
    """
    Manages a single multiplexed WebSocket connection to Binance for all
    configured pairs and timeframes. Reconnects automatically with
    exponential backoff on disconnect.
    """

    def __init__(self, pairs: list[str], on_kline: Callable):
        """
        Args:
            pairs:     List of Binance symbols, e.g. ["BTCUSDT", "ETHUSDT"]
            on_kline:  Async callback called with (symbol, timeframe, kline_data)
                       whenever a kline message arrives.
        """
        self.pairs = [p.upper() for p in pairs]
        self.on_kline = on_kline
        self._running = False
        self._reconnect_delay = 1.0   # seconds; doubles on each failed attempt
        self._max_delay = 60.0        # cap backoff at 60s

    def _build_stream_url(self) -> str:
        """Build the combined stream URL for all pairs × timeframes."""
        streams = []
        for pair in self.pairs:
            for tf in TIMEFRAMES:
                # Binance stream names are lowercase: btcusdt@kline_1m
                streams.append(f"{pair.lower()}@kline_{tf}")
        stream_param = "/".join(streams)
        return f"{BINANCE_WS_BASE}?streams={stream_param}"

    async def start(self):
        """Start the WebSocket loop. Runs until stop() is called."""
        self._running = True
        while self._running:
            url = self._build_stream_url()
            try:
                logger.info(f"[WS] Connecting to Binance ({len(self.pairs)} pairs × {len(TIMEFRAMES)} timeframes)...")
                async with websockets.connect(
                    url,
                    ping_interval=20,    # send ping every 20s to keep connection alive
                    ping_timeout=10,     # disconnect if pong not received within 10s
                    close_timeout=5,
                ) as ws:
                    self._reconnect_delay = 1.0   # reset backoff on successful connect
                    logger.info("[WS] Connected to Binance")
                    await self._listen(ws)

            except websockets.ConnectionClosedError as e:
                logger.warning(f"[WS] Connection closed: {e}. Reconnecting in {self._reconnect_delay:.0f}s...")
            except websockets.InvalidStatusCode as e:
                logger.error(f"[WS] Invalid status {e.status_code}. Reconnecting in {self._reconnect_delay:.0f}s...")
            except OSError as e:
                logger.error(f"[WS] Network error: {e}. Reconnecting in {self._reconnect_delay:.0f}s...")
            except Exception as e:
                logger.error(f"[WS] Unexpected error: {e}. Reconnecting in {self._reconnect_delay:.0f}s...")

            if self._running:
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_delay)

    async def _listen(self, ws):
        """Process incoming messages from the WebSocket."""
        async for raw_msg in ws:
            try:
                msg = json.loads(raw_msg)
                # Combined stream wraps data in: {"stream": "btcusdt@kline_1m", "data": {...}}
                stream_name = msg.get("stream", "")
                data = msg.get("data", {})

                if data.get("e") != "kline":
                    continue   # ignore non-kline events

                kline = data["k"]
                symbol = kline["s"]            # e.g. "BTCUSDT"
                timeframe = kline["i"]         # e.g. "1m"
                is_closed = kline["x"]         # True when candle is finalized

                kline_data = {
                    "open_time":  kline["t"],
                    "open":       float(kline["o"]),
                    "high":       float(kline["h"]),
                    "low":        float(kline["l"]),
                    "close":      float(kline["c"]),
                    "volume":     float(kline["v"]),
                    "close_time": kline["T"],
                    "is_closed":  is_closed,
                }

                await self.on_kline(symbol, timeframe, kline_data)

            except (KeyError, ValueError, json.JSONDecodeError) as e:
                logger.debug(f"[WS] Malformed message skipped: {e}")

    def stop(self):
        """Signal the client to stop reconnecting after current connection closes."""
        self._running = False
        logger.info("[WS] Stop requested")