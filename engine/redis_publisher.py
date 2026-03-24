"""
redis_publisher.py — Upstash Redis signal publisher

Publishes scored SMC signals to Upstash Redis pub/sub channel
so downstream services (API, AI service) can consume them in real time.

Uses the Upstash REST API (HTTP-based) instead of raw TCP Redis,
which works reliably from Railway's free tier without connection limits.

Free tier: 10,000 commands/day — plenty for signal publishing + caching.
"""

import json
import logging
import time

from upstash_redis import Redis

logger = logging.getLogger(__name__)

# Redis channel name — subscribers listen on this
SIGNAL_CHANNEL = "smc:signals"

# Key prefix for storing latest signal per pair (for REST API to read)
SIGNAL_KEY_PREFIX = "signal:latest:"

# TTL for stored signals — 24 hours
SIGNAL_TTL_SECONDS = 86_400


class RedisPublisher:
    """
    Publishes signals to Redis pub/sub and stores them as key-value pairs
    so they can be fetched by the REST API without requiring a subscriber.
    """

    def __init__(self, url: str, token: str):
        """
        Args:
            url:   Upstash Redis REST URL (from upstash.com console)
            token: Upstash Redis REST token
        """
        self._redis = Redis(url=url, token=token)
        self._published_count = 0

    async def publish_signal(self, signal: dict) -> bool:
        """
        Publish a signal to:
          1. Redis pub/sub channel (real-time fan-out to subscribers)
          2. Redis key-value store (REST API can fetch latest signal per pair)

        Args:
            signal: The fully scored signal dict from Scorer.evaluate()

        Returns:
            True if published successfully, False on error.
        """
        try:
            payload = json.dumps(signal)
            pair    = signal["pair"]

            # Publish to pub/sub channel — all subscribers receive this immediately
            self._redis.publish(SIGNAL_CHANNEL, payload)

            # Also store as latest signal for this pair (for REST API polling)
            key = f"{SIGNAL_KEY_PREFIX}{pair}"
            self._redis.set(key, payload, ex=SIGNAL_TTL_SECONDS)

            # Keep a list of recent signal keys for the API to query
            self._redis.lpush("smc:signal_history", payload)
            self._redis.ltrim("smc:signal_history", 0, 199)   # keep last 200

            self._published_count += 1
            logger.info(
                f"[REDIS] Published signal #{self._published_count}: "
                f"{pair} {signal['type']} {signal['confidence_score']}"
            )
            return True

        except Exception as e:
            logger.error(f"[REDIS] Failed to publish signal: {e}")
            return False

    def publish_event(self, event_type: str, data: dict) -> bool:
        """
        Publish a raw SMC event (non-signal) for debugging / monitoring.
        Used to stream OB detections, FVG formations, etc. to any dashboard.
        """
        try:
            payload = json.dumps({"event": event_type, "data": data, "ts": int(time.time() * 1000)})
            self._redis.publish("smc:events", payload)
            return True
        except Exception as e:
            logger.debug(f"[REDIS] Event publish error: {e}")
            return False

    def get_latest_signal(self, pair: str) -> dict | None:
        """Fetch the most recent signal for a pair from the key-value store."""
        try:
            raw = self._redis.get(f"{SIGNAL_KEY_PREFIX}{pair}")
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.error(f"[REDIS] Error fetching signal for {pair}: {e}")
            return None

    def get_signal_history(self, limit: int = 50) -> list[dict]:
        """Fetch recent signals from the list store."""
        try:
            raw_list = self._redis.lrange("smc:signal_history", 0, limit - 1)
            return [json.loads(r) for r in raw_list]
        except Exception as e:
            logger.error(f"[REDIS] Error fetching history: {e}")
            return []

    @property
    def published_count(self) -> int:
        return self._published_count