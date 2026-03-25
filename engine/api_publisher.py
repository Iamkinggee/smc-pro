"""
api_publisher.py — Sends scored SMC signals to the Fastify API.

Posts to POST /signals/internal using an internal service key.
The API then:
  1. Re-scores with the TypeScript confluence engine (bonus factors)
  2. Persists to Supabase
  3. Publishes to Redis → WebSocket broadcast

This replaces direct Redis publishing from the Python engine,
keeping all business logic and persistence in one place (the API).

Environment variables:
  API_BASE_URL      — e.g. http://localhost:3001  (or Railway URL in prod)
  INTERNAL_API_KEY  — shared secret between engine and API
"""

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

API_BASE_URL     = os.getenv("API_BASE_URL", "http://localhost:3001")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
INTERNAL_ENDPOINT = f"{API_BASE_URL}/signals/internal"

# How long to wait for API response before giving up (seconds)
REQUEST_TIMEOUT = 10


class APIPublisher:
    """
    Posts signals to the Fastify API's internal endpoint.
    Uses a persistent aiohttp session for connection pooling.
    """

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._published_count = 0
        self._failed_count    = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return (or create) the shared aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type":   "application/json",
                    "x-internal-key": INTERNAL_API_KEY,
                },
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            )
        return self._session

    async def publish_signal(self, signal: dict) -> bool:
        """
        POST the scored signal to the API.

        The signal dict from scorer.py already contains all required fields:
          pair, type, entry, stop_loss, take_profit, htf_bias,
          ltf_event (mapped to timeframe), confluences, confidence_raw, timestamp.

        We enrich it with a `factors` object so the TypeScript confluence
        scorer can apply bonus multipliers (sweep depth, OB strength, etc.).

        Returns:
            True on success (API returned 201), False on any error.
        """
        if not INTERNAL_API_KEY:
            logger.warning("[API] INTERNAL_API_KEY not set — signal not published")
            return False

        # Build the payload shape expected by RawEnginePayload in signal.ts
        payload = self._build_payload(signal)

        try:
            session = await self._get_session()
            async with session.post(INTERNAL_ENDPOINT, json=payload) as resp:
                if resp.status == 201:
                    self._published_count += 1
                    logger.info(
                        f"[API] Published signal #{self._published_count}: "
                        f"{signal['pair']} {signal['type']} {signal['confidence_score']}"
                    )
                    return True
                elif resp.status == 422:
                    # API rejected the signal (below threshold or bad RR) — not an error
                    body = await resp.json()
                    logger.debug(f"[API] Signal rejected by API: {body.get('error')}")
                    return False
                else:
                    body = await resp.text()
                    logger.error(f"[API] Unexpected response {resp.status}: {body[:200]}")
                    self._failed_count += 1
                    return False

        except asyncio.TimeoutError:
            logger.error(f"[API] Timeout posting signal for {signal.get('pair')}")
            self._failed_count += 1
            return False
        except aiohttp.ClientConnectorError as e:
            logger.error(f"[API] Cannot connect to API at {API_BASE_URL}: {e}")
            self._failed_count += 1
            return False
        except Exception as e:
            logger.error(f"[API] Unexpected error publishing signal: {e}")
            self._failed_count += 1
            return False

    def _build_payload(self, signal: dict) -> dict:
        """
        Convert the Python scorer output into RawEnginePayload shape.

        scorer.py output keys:
          pair, type, entry, stop_loss, take_profit, rr_ratio,
          confidence_score ("87%"), confidence_raw (0.87),
          confluences, htf_bias, ltf_event, timestamp

        Expected by signal.ts:
          pair, type, entry, stop_loss, take_profit, htf_bias,
          timeframe, factors (ConfluentFactors), timestamp
        """
        confluences: list[str] = signal.get("confluences", [])

        factors = {
            "liquiditySweep":  "Liquidity Sweep"       in confluences,
            "orderBlockTap":   "Order Block Tap"        in confluences,
            "bosChoch":        "BOS/CHOCH"              in confluences,
            "fvgPresent":      "FVG"                    in confluences,
            "htfBias":         "HTF Bias Aligned"       in confluences,
        }

        return {
            "pair":       signal["pair"],
            "type":       signal["type"],
            "entry":      signal["entry"],
            "stop_loss":  signal["stop_loss"],
            "take_profit":signal["take_profit"],
            "htf_bias":   signal.get("htf_bias", ""),
            "timeframe":  signal.get("ltf_event", "5m"),
            "factors":    factors,
            "timestamp":  signal["timestamp"],
        }

    async def close(self):
        """Close the aiohttp session on shutdown."""
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info(
            f"[API] Publisher closed — "
            f"published={self._published_count} failed={self._failed_count}"
        )

    @property
    def published_count(self) -> int:
        return self._published_count

    @property
    def failed_count(self) -> int:
        return self._failed_count