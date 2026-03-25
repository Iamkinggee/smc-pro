# """
# main.py — SMC Signal Engine entry point

# Wires all Phase 1 components together:
#   1. Starts Binance WebSocket connection
#   2. Backfills historical candles via REST
#   3. Runs HTF analysis on every 1H/4H close
#   4. Runs LTF trigger detection on every 1m/5m close
#   5. Scores confluences and publishes signals to Redis

# Environment variables (see .env.example):
#   TRADING_PAIRS            — comma-separated symbols
#   UPSTASH_REDIS_REST_URL   — from upstash.com
#   UPSTASH_REDIS_REST_TOKEN — from upstash.com
#   CONFLUENCE_THRESHOLD     — float 0.0–1.0 (default 0.65)
#   MIN_RR_RATIO             — float (default 2.0)
#   LOG_LEVEL                — DEBUG | INFO | WARNING
# """

# import asyncio
# import logging
# import os
# import time
# from typing import Literal

# from dotenv import load_dotenv

# from backfill import Backfiller
# from candle_builder import CandleBuilder
# from htf_analyzer import HTFAnalyzer, HTFState
# from ltf_trigger import LTFTrigger, StructureEvent
# from redis_publisher import RedisPublisher
# from scorer import Scorer, build_factors
# from ws_client import BinanceWSClient

# # ─── Configuration ────────────────────────────────────────────

# load_dotenv()

# LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# logging.basicConfig(
#     level=getattr(logging, LOG_LEVEL, logging.INFO),
#     format="%(asctime)s [%(levelname)s] %(message)s",
#     datefmt="%H:%M:%S",
# )
# logger = logging.getLogger(__name__)

# PAIRS = [p.strip().upper() for p in os.getenv("TRADING_PAIRS", "BTCUSDT,ETHUSDT").split(",")]
# THRESHOLD  = float(os.getenv("CONFLUENCE_THRESHOLD", "0.65"))
# MIN_RR     = float(os.getenv("MIN_RR_RATIO", "2.0"))
# REDIS_URL   = os.getenv("UPSTASH_REDIS_REST_URL", "")
# REDIS_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

# # HTF timeframes drive bias + zone detection
# HTF_TIMEFRAMES = {"1h", "4h"}

# # LTF timeframes drive entry triggers
# LTF_TIMEFRAMES = {"1m", "5m"}

# # Minimum candles before running analysis (avoids false signals on startup)
# MIN_CANDLES_BEFORE_ANALYSIS = 50

# # De-bounce: don't emit the same signal twice within this window (seconds)
# SIGNAL_COOLDOWN_SECONDS = 300   # 5 minutes per pair


# # ─── Engine ───────────────────────────────────────────────────

# class SMCEngine:
#     def __init__(self):
#         self.candle_builder = CandleBuilder(on_candle_close=self._on_candle_close)
#         self.htf_analyzer   = HTFAnalyzer()
#         self.ltf_trigger    = LTFTrigger()
#         self.scorer         = Scorer(threshold=THRESHOLD, min_rr=MIN_RR)
#         self.publisher      = RedisPublisher(url=REDIS_URL, token=REDIS_TOKEN)

#         # Track last signal time per pair to prevent duplicate signals
#         self._last_signal_time: dict[str, float] = {}

#         # Cache latest LTF structure events per pair (used when new HTF candle closes)
#         self._last_ltf_event: dict[str, StructureEvent | None] = {}

#     async def start(self):
#         logger.info(f"[ENGINE] Starting SMC engine for pairs: {PAIRS}")
#         logger.info(f"[ENGINE] Confluence threshold: {THRESHOLD:.0%}  Min RR: {MIN_RR}:1")

#         # Step 1: backfill historical candles so analyzers have enough data immediately
#         if REDIS_URL:
#             logger.info("[ENGINE] Redis configured — signals will be published")
#         else:
#             logger.warning("[ENGINE] No Redis URL set — signals will only be logged (set UPSTASH_REDIS_REST_URL)")

#         backfiller = Backfiller(self.candle_builder)
#         logger.info("[ENGINE] Backfilling historical candles...")
#         await backfiller.run(PAIRS)
#         logger.info("[ENGINE] Backfill complete — running initial HTF analysis...")

#         # Run initial HTF analysis on backfilled data
#         for pair in PAIRS:
#             for tf in ["1h", "4h"]:
#                 candles = self.candle_builder.get_candles(pair, tf)
#                 if len(candles) >= MIN_CANDLES_BEFORE_ANALYSIS:
#                     self.htf_analyzer.analyze(pair, tf, candles)

#         # Step 2: start live WebSocket feed
#         logger.info("[ENGINE] Connecting to Binance WebSocket...")
#         ws_client = BinanceWSClient(pairs=PAIRS, on_kline=self.candle_builder.on_kline)
#         await ws_client.start()

#     async def _on_candle_close(self, symbol: str, timeframe: str, candle: dict, history: list[dict]):
#         """Called by CandleBuilder whenever a candle closes. Routes to HTF or LTF handler."""
#         if timeframe in HTF_TIMEFRAMES:
#             await self._handle_htf_close(symbol, timeframe, history)
#         elif timeframe in LTF_TIMEFRAMES:
#             await self._handle_ltf_close(symbol, timeframe, history)

#     async def _handle_htf_close(self, symbol: str, timeframe: str, candles: list[dict]):
#         """Re-run HTF analysis when a 1H or 4H candle closes."""
#         if len(candles) < MIN_CANDLES_BEFORE_ANALYSIS:
#             return

#         self.htf_analyzer.analyze(symbol, timeframe, candles)
#         logger.debug(f"[ENGINE] HTF updated for {symbol} {timeframe}")

#         # After updating HTF, immediately try to score any cached LTF event
#         # (an LTF trigger that fired right before this HTF close is now rescored
#         # with updated structure context)
#         ltf_event = self._last_ltf_event.get(symbol)
#         if ltf_event:
#             await self._try_score(symbol, ltf_event)

#     async def _handle_ltf_close(self, symbol: str, timeframe: str, candles: list[dict]):
#         """Run LTF trigger detection when a 1m or 5m candle closes."""
#         if len(candles) < MIN_CANDLES_BEFORE_ANALYSIS:
#             return

#         # Get the current HTF state to determine bias
#         htf_state_1h = self.htf_analyzer.get_state(symbol, "1h")
#         htf_state_4h = self.htf_analyzer.get_state(symbol, "4h")

#         # Use 4H bias as primary, 1H as tiebreaker
#         bias = "neutral"
#         if htf_state_4h:
#             bias = htf_state_4h.bias
#         elif htf_state_1h:
#             bias = htf_state_1h.bias

#         ltf_event = self.ltf_trigger.check(symbol, timeframe, candles, htf_bias=bias)

#         if ltf_event:
#             self._last_ltf_event[symbol] = ltf_event
#             await self._try_score(symbol, ltf_event)

#     async def _try_score(self, symbol: str, ltf_event: StructureEvent):
#         """
#         Attempt to build a full confluence score for the current setup.
#         Uses both 1H and 4H HTF state — prefers the 4H for SL/TP calculation.
#         """
#         # Prevent duplicate signals within cooldown window
#         now = time.time()
#         last = self._last_signal_time.get(symbol, 0)
#         if now - last < SIGNAL_COOLDOWN_SECONDS:
#             logger.debug(f"[ENGINE] {symbol} — signal cooldown active ({SIGNAL_COOLDOWN_SECONDS}s), skipping")
#             return

#         htf_state = self.htf_analyzer.get_state(symbol, "4h") or \
#                     self.htf_analyzer.get_state(symbol, "1h")

#         if not htf_state:
#             return

#         # Map LTF direction to trade direction
#         direction: Literal["BUY", "SELL"] = "BUY" if ltf_event.direction == "bullish" else "SELL"

#         # Current price = the LTF event trigger price
#         current_price = ltf_event.price

#         # Check for a recent liquidity sweep (last 5m candles)
#         ltf_candles = self.candle_builder.get_candles(symbol, "5m", n=5)
#         sweep_detected = False
#         sweep_level    = 0.0

#         for pool in htf_state.liquidity_pools:
#             if pool.swept:
#                 continue
#             if self.ltf_trigger.check_liquidity_sweep(ltf_candles, pool.price, pool.pool_type):
#                 sweep_detected = True
#                 sweep_level    = pool.price
#                 pool.swept     = True   # mark as swept so we don't re-score
#                 break

#         # Build factors object
#         factors = build_factors(
#             direction=ltf_event.direction,
#             htf_state=htf_state,
#             ltf_event=ltf_event,
#             current_price=current_price,
#             sweep_detected=sweep_detected,
#             sweep_level=sweep_level,
#         )

#         # Score and build signal
#         signal = self.scorer.evaluate(
#             symbol=symbol,
#             direction=direction,
#             entry=current_price,
#             factors=factors,
#             htf_state=htf_state,
#             ltf_event=ltf_event,
#         )

#         if signal:
#             self._last_signal_time[symbol] = now
#             self._last_ltf_event[symbol]   = None   # reset after signal fires

#             # Publish to Redis
#             if REDIS_URL:
#                 await asyncio.get_event_loop().run_in_executor(
#                     None, lambda: self.publisher.publish_signal(signal)
#                 )
#             else:
#                 # No Redis configured — print the signal for verification
#                 import json
#                 print("\n" + "="*60)
#                 print("SIGNAL GENERATED (no Redis — printing to console):")
#                 print(json.dumps(signal, indent=2))
#                 print("="*60 + "\n")


# # ─── Entry Point ──────────────────────────────────────────────

# if __name__ == "__main__":
#     engine = SMCEngine()
#     try:
#         asyncio.run(engine.start())
#     except KeyboardInterrupt:
#         logger.info("[ENGINE] Shutdown requested — goodbye")










"""
main.py — SMC Signal Engine entry point (updated for API integration)

Flow:
  Binance WS → CandleBuilder → HTFAnalyzer + LTFTrigger → Scorer → APIPublisher

The engine no longer writes to Redis directly. Instead it POSTs scored
signals to the Fastify API's /signals/internal endpoint. The API handles
persistence (Supabase) and real-time broadcast (Redis → WebSocket).

Environment variables:
  TRADING_PAIRS      — comma-separated symbols (default: BTCUSDT,ETHUSDT)
  API_BASE_URL       — Fastify API URL (default: http://localhost:3001)
  INTERNAL_API_KEY   — shared secret for /signals/internal
  CONFLUENCE_THRESHOLD — float 0.0–1.0 (default 0.65)
  MIN_RR_RATIO       — float (default 2.0)
  LOG_LEVEL          — DEBUG | INFO | WARNING
"""

import asyncio
import logging
import os
import time
from typing import Literal

from dotenv import load_dotenv

from backfill       import Backfiller
from candle_builder import CandleBuilder
from htf_analyzer   import HTFAnalyzer, HTFState
from ltf_trigger    import LTFTrigger, StructureEvent
from api_publisher  import APIPublisher          # ← replaces RedisPublisher
from scorer         import Scorer, build_factors
from ws_client      import BinanceWSClient

# ─── Configuration ────────────────────────────────────────────

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PAIRS     = [p.strip().upper() for p in os.getenv("TRADING_PAIRS", "BTCUSDT,ETHUSDT").split(",")]
THRESHOLD = float(os.getenv("CONFLUENCE_THRESHOLD", "0.65"))
MIN_RR    = float(os.getenv("MIN_RR_RATIO", "2.0"))

HTF_TIMEFRAMES = {"1h", "4h"}
LTF_TIMEFRAMES = {"1m", "5m"}

MIN_CANDLES_BEFORE_ANALYSIS = 50
SIGNAL_COOLDOWN_SECONDS     = 300   # 5 min per pair


# ─── Engine ───────────────────────────────────────────────────

class SMCEngine:
    def __init__(self):
        self.candle_builder = CandleBuilder(on_candle_close=self._on_candle_close)
        self.htf_analyzer   = HTFAnalyzer()
        self.ltf_trigger    = LTFTrigger()
        self.scorer         = Scorer(threshold=THRESHOLD, min_rr=MIN_RR)
        self.publisher      = APIPublisher()          # ← API publisher

        self._last_signal_time: dict[str, float]             = {}
        self._last_ltf_event:   dict[str, StructureEvent | None] = {}

    async def start(self):
        logger.info(f"[ENGINE] Starting SMC engine for pairs: {PAIRS}")
        logger.info(f"[ENGINE] Threshold: {THRESHOLD:.0%}  Min RR: {MIN_RR}:1")
        logger.info(f"[ENGINE] Posting signals to: {os.getenv('API_BASE_URL', 'http://localhost:3001')}")

        # Backfill historical candles
        backfiller = Backfiller(self.candle_builder)
        logger.info("[ENGINE] Backfilling historical candles...")
        await backfiller.run(PAIRS)
        logger.info("[ENGINE] Backfill complete — running initial HTF analysis...")

        for pair in PAIRS:
            for tf in ["1h", "4h"]:
                candles = self.candle_builder.get_candles(pair, tf)
                if len(candles) >= MIN_CANDLES_BEFORE_ANALYSIS:
                    self.htf_analyzer.analyze(pair, tf, candles)

        logger.info("[ENGINE] Connecting to Binance WebSocket...")
        ws_client = BinanceWSClient(pairs=PAIRS, on_kline=self.candle_builder.on_kline)

        try:
            await ws_client.start()
        finally:
            # Clean up HTTP session on shutdown
            await self.publisher.close()

    async def _on_candle_close(self, symbol: str, timeframe: str, candle: dict, history: list[dict]):
        if timeframe in HTF_TIMEFRAMES:
            await self._handle_htf_close(symbol, timeframe, history)
        elif timeframe in LTF_TIMEFRAMES:
            await self._handle_ltf_close(symbol, timeframe, history)

    async def _handle_htf_close(self, symbol: str, timeframe: str, candles: list[dict]):
        if len(candles) < MIN_CANDLES_BEFORE_ANALYSIS:
            return
        self.htf_analyzer.analyze(symbol, timeframe, candles)
        logger.debug(f"[ENGINE] HTF updated for {symbol} {timeframe}")

        ltf_event = self._last_ltf_event.get(symbol)
        if ltf_event:
            await self._try_score(symbol, ltf_event)

    async def _handle_ltf_close(self, symbol: str, timeframe: str, candles: list[dict]):
        if len(candles) < MIN_CANDLES_BEFORE_ANALYSIS:
            return

        htf_state_4h = self.htf_analyzer.get_state(symbol, "4h")
        htf_state_1h = self.htf_analyzer.get_state(symbol, "1h")

        bias = "neutral"
        if htf_state_4h:
            bias = htf_state_4h.bias
        elif htf_state_1h:
            bias = htf_state_1h.bias

        ltf_event = self.ltf_trigger.check(symbol, timeframe, candles, htf_bias=bias)

        if ltf_event:
            self._last_ltf_event[symbol] = ltf_event
            await self._try_score(symbol, ltf_event)

    async def _try_score(self, symbol: str, ltf_event: StructureEvent):
        # Cooldown check
        now  = time.time()
        last = self._last_signal_time.get(symbol, 0)
        if now - last < SIGNAL_COOLDOWN_SECONDS:
            logger.debug(f"[ENGINE] {symbol} — cooldown active, skipping")
            return

        htf_state = (
            self.htf_analyzer.get_state(symbol, "4h") or
            self.htf_analyzer.get_state(symbol, "1h")
        )
        if not htf_state:
            return

        direction: Literal["BUY", "SELL"] = "BUY" if ltf_event.direction == "bullish" else "SELL"
        current_price = ltf_event.price

        # Check for liquidity sweep
        ltf_candles    = self.candle_builder.get_candles(symbol, "5m", n=5)
        sweep_detected = False
        sweep_level    = 0.0

        for pool in htf_state.liquidity_pools:
            if pool.swept:
                continue
            if self.ltf_trigger.check_liquidity_sweep(ltf_candles, pool.price, pool.pool_type):
                sweep_detected = True
                sweep_level    = pool.price
                pool.swept     = True
                break

        factors = build_factors(
            direction=ltf_event.direction,
            htf_state=htf_state,
            ltf_event=ltf_event,
            current_price=current_price,
            sweep_detected=sweep_detected,
            sweep_level=sweep_level,
        )

        signal = self.scorer.evaluate(
            symbol=symbol,
            direction=direction,
            entry=current_price,
            factors=factors,
            htf_state=htf_state,
            ltf_event=ltf_event,
        )

        if signal:
            self._last_signal_time[symbol] = now
            self._last_ltf_event[symbol]   = None

            # POST to API (async, non-blocking)
            published = await self.publisher.publish_signal(signal)
            if not published:
                # Fallback: log to console so signals aren't silently lost
                import json as _json
                print("\n" + "="*60)
                print("SIGNAL (API unavailable — console fallback):")
                print(_json.dumps(signal, indent=2))
                print("="*60 + "\n")


# ─── Entry Point ──────────────────────────────────────────────

if __name__ == "__main__":
    engine = SMCEngine()
    try:
        asyncio.run(engine.start())
    except KeyboardInterrupt:
        logger.info("[ENGINE] Shutdown requested — goodbye")