"""
Phase 4 — Signal Explainer
Auto-generates an AI explanation for every new signal.
Caches in Redis (24h TTL) keyed by signal hash so identical signals skip the API.
Called by the SMC engine on signal emit.
"""

import hashlib
import json
import logging
from typing import Optional

import redis.asyncio as aioredis

from ai.llm_client     import complete
from ai.prompt_builder import PromptBuilder, SignalContext, UserContext

logger = logging.getLogger(__name__)

# ── Redis connection (injected or default) ────────────────────────────────────
_redis: Optional[aioredis.Redis] = None


def init_redis(redis_url: str = "redis://localhost:6379"):
    global _redis
    _redis = aioredis.from_url(redis_url, decode_responses=True)


CACHE_TTL = 60 * 60 * 24   # 24 hours

_builder = PromptBuilder()


# ── Cache helpers ─────────────────────────────────────────────────────────────
def _signal_hash(signal: dict) -> str:
    """Deterministic hash from the signal's structural fingerprint (not timestamp)."""
    key_fields = {
        "pair":             signal.get("pair"),
        "type":             signal.get("type"),
        "entry":            round(float(signal.get("entry", 0)), 2),
        "stop_loss":        round(float(signal.get("stop_loss", 0)), 2),
        "take_profit":      round(float(signal.get("take_profit", 0)), 2),
        "confidence_score": signal.get("confidence_score"),
        "confluences":      sorted(signal.get("confluences", [])),
    }
    raw = json.dumps(key_fields, sort_keys=True)
    return "sigexp:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _cache_get(key: str) -> Optional[str]:
    if _redis is None:
        return None
    try:
        return await _redis.get(key)
    except Exception as e:
        logger.warning(f"Redis GET failed: {e}")
        return None


async def _cache_set(key: str, value: str):
    if _redis is None:
        return
    try:
        await _redis.setex(key, CACHE_TTL, value)
    except Exception as e:
        logger.warning(f"Redis SET failed: {e}")


# ── Public API ────────────────────────────────────────────────────────────────
async def explain_signal(signal: dict) -> str:
    """
    Generate (or retrieve from cache) an AI explanation for a signal dict.
    Signal dict must match the standard signal output format.
    Returns the explanation string.
    """
    cache_key = _signal_hash(signal)

    # 1. Try cache
    cached = await _cache_get(cache_key)
    if cached:
        logger.info(f"Signal explanation cache HIT: {cache_key}")
        return cached

    # 2. Build context
    signal_ctx = SignalContext(
        pair             = signal.get("pair", ""),
        type             = signal.get("type", ""),
        entry            = float(signal.get("entry", 0)),
        stop_loss        = float(signal.get("stop_loss", 0)),
        take_profit      = float(signal.get("take_profit", 0)),
        confidence_score = float(signal.get("confidence_score", 0)),
        confluences      = signal.get("confluences", []),
        htf_bias         = signal.get("htf_bias", ""),
        timeframe        = signal.get("timeframe", "5M"),
    )

    user_message = (
        f"Explain this {signal_ctx.type} signal on {signal_ctx.pair} "
        f"with {signal_ctx.confidence_score}% confidence."
    )

    system_prompt, full_message = _builder.build(
        mode       = "signal_explanation",
        user_message = user_message,
        signal_ctx  = signal_ctx,
    )

    # 3. Call LLM
    try:
        explanation = await complete(system_prompt, full_message)
    except Exception as e:
        logger.error(f"LLM explanation failed: {e}")
        explanation = _fallback_explanation(signal_ctx)

    # 4. Attach to signal + cache
    signal["ai_explanation"] = explanation
    await _cache_set(cache_key, explanation)
    logger.info(f"Signal explanation cached: {cache_key}")

    return explanation


def _fallback_explanation(ctx: SignalContext) -> str:
    """Rule-based fallback when LLM is unavailable."""
    conf_str = ", ".join(ctx.confluences) if ctx.confluences else "N/A"
    return (
        f"**{ctx.type} setup on {ctx.pair}** ({ctx.confidence_score}% confidence)\n\n"
        f"Confluences detected: {conf_str}\n"
        f"Entry: {ctx.entry} | SL: {ctx.stop_loss} | TP: {ctx.take_profit}\n\n"
        f"Price has interacted with a key SMC zone. "
        f"Wait for LTF confirmation before executing."
    )