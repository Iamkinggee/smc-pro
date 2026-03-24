"""
scorer.py — SMC Weighted Confluence Scorer + Signal Builder

Evaluates all detected SMC factors and produces a scored signal.
A signal is only emitted if the confluence score meets the threshold.

Factor weights (must sum to 1.0):
  liquidity_sweep   30%   — stop hunt / equal H/L sweep detected
  order_block_tap   25%   — price entered a valid OB zone
  bos_choch         20%   — LTF BOS or CHOCH confirmed
  fvg               15%   — price is inside or near an open FVG
  htf_bias          10%   — LTF direction aligns with HTF bias

Signal output schema:
  {
    "pair":             str,
    "type":             "BUY" | "SELL",
    "entry":            float,
    "stop_loss":        float,
    "take_profit":      float,
    "confidence_score": str,    e.g. "87%"
    "confluences":      list[str],
    "ai_explanation":   str,    (populated by AI service in Phase 4)
    "timestamp":        int,    (ms)
  }
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Literal

from htf_analyzer import HTFState, OrderBlock
from ltf_trigger import StructureEvent

logger = logging.getLogger(__name__)


# ─── Confluence Factors ───────────────────────────────────────

@dataclass
class ConfluenceFactors:
    """Boolean flags for each SMC factor present in the setup."""
    liquidity_sweep:  bool = False
    order_block_tap:  bool = False
    bos_choch:        bool = False
    fvg:              bool = False
    htf_bias_aligned: bool = False

    # Detail strings for the AI explanation layer
    ob_level:         float = 0.0
    ob_direction:     str   = ""
    fvg_top:          float = 0.0
    fvg_bottom:       float = 0.0
    sweep_level:      float = 0.0


# ─── Weights (configurable) ──────────────────────────────────

WEIGHTS = {
    "liquidity_sweep":  0.30,
    "order_block_tap":  0.25,
    "bos_choch":        0.20,
    "fvg":              0.15,
    "htf_bias_aligned": 0.10,
}

assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"


class Scorer:
    """
    Evaluates a ConfluenceFactors object and builds a signal dict
    if the score meets the configured threshold.
    """

    def __init__(self, threshold: float = 0.65, min_rr: float = 2.0):
        """
        Args:
            threshold: Minimum confluence score (0–1) to emit a signal.
            min_rr:    Minimum risk:reward ratio required.
        """
        self.threshold = threshold
        self.min_rr    = min_rr

    def evaluate(
        self,
        symbol:    str,
        direction: Literal["BUY", "SELL"],
        entry:     float,
        factors:   ConfluenceFactors,
        htf_state: HTFState,
        ltf_event: StructureEvent,
    ) -> dict | None:
        """
        Score the setup and return a signal dict, or None if it doesn't qualify.

        Stop-loss is placed beyond the triggering Order Block.
        Take-profit targets the nearest opposing liquidity pool.
        """
        score, present = self._calculate_score(factors)

        if score < self.threshold:
            logger.debug(
                f"[SCORER] {symbol} {direction} rejected — score {score:.0%} < threshold {self.threshold:.0%}"
            )
            return None

        # ── SL / TP Calculation ───────────────────────────────
        sl, tp = self._calculate_sl_tp(
            direction=direction,
            entry=entry,
            htf_state=htf_state,
            factors=factors,
        )

        if sl is None or tp is None:
            logger.debug(f"[SCORER] {symbol} {direction} — could not calculate valid SL/TP, skipping")
            return None

        # Check risk:reward
        risk   = abs(entry - sl)
        reward = abs(tp - entry)
        if risk == 0 or (reward / risk) < self.min_rr:
            logger.debug(
                f"[SCORER] {symbol} {direction} — RR {reward/risk:.1f}:{1} below minimum {self.min_rr}:1"
            )
            return None

        rr_ratio = round(reward / risk, 1)

        signal = {
            "pair":             symbol,
            "type":             direction,
            "entry":            round(entry, 6),
            "stop_loss":        round(sl, 6),
            "take_profit":      round(tp, 6),
            "rr_ratio":         rr_ratio,
            "confidence_score": f"{int(score * 100)}%",
            "confidence_raw":   round(score, 4),
            "confluences":      present,
            "ai_explanation":   "",   # filled by AI service in Phase 4
            "timestamp":        int(time.time() * 1000),
            "htf_bias":         htf_state.bias,
            "ltf_event":        ltf_event.event_type,
        }

        logger.info(
            f"[SIGNAL] {symbol} {direction} | score={score:.0%} | "
            f"entry={entry:.4f} SL={sl:.4f} TP={tp:.4f} RR=1:{rr_ratio} | "
            f"confluences={present}"
        )

        return signal

    # ─── Score Calculation ────────────────────────────────────

    def _calculate_score(self, factors: ConfluenceFactors) -> tuple[float, list[str]]:
        """
        Return (total_score, list_of_present_confluence_names).
        """
        score = 0.0
        present = []

        factor_map = {
            "liquidity_sweep":  factors.liquidity_sweep,
            "order_block_tap":  factors.order_block_tap,
            "bos_choch":        factors.bos_choch,
            "fvg":              factors.fvg,
            "htf_bias_aligned": factors.htf_bias_aligned,
        }

        for name, is_present in factor_map.items():
            if is_present:
                score += WEIGHTS[name]
                # Convert snake_case to human-readable labels
                present.append(name.replace("_", " ").title().replace("Htf", "HTF").replace("Fvg", "FVG").replace("Bos Choch", "BOS/CHOCH"))

        return score, present

    # ─── SL / TP Calculation ─────────────────────────────────

    def _calculate_sl_tp(
        self,
        direction: str,
        entry:     float,
        htf_state: HTFState,
        factors:   ConfluenceFactors,
        buffer_pct: float = 0.0010,    # 0.10% buffer beyond OB
    ) -> tuple[float | None, float | None]:
        """
        SL: placed beyond the order block that was tapped (+ buffer).
        TP: placed at the nearest opposing liquidity pool or swing extreme.

        Returns (stop_loss, take_profit) or (None, None) if levels can't be found.
        """
        sl = None
        tp = None

        if direction == "BUY":
            # SL below the bullish order block low
            if factors.ob_level > 0:
                ob = self._find_ob(htf_state, "bullish", factors.ob_level)
                if ob:
                    sl = ob.low * (1 - buffer_pct)

            # Fallback SL: 1.5× ATR below entry (rough estimate using recent candles)
            if sl is None:
                sl = entry * (1 - 0.015)    # 1.5% fallback

            # TP: nearest equal_highs liquidity pool above entry
            tp_pools = [
                p for p in htf_state.liquidity_pools
                if p.pool_type == "equal_highs" and p.price > entry and not p.swept
            ]
            if tp_pools:
                tp = min(tp_pools, key=lambda p: p.price).price * 0.9995   # just below the pool
            else:
                # Fallback: use last swing high
                tp = htf_state.last_swing_high * 0.9995

        else:  # SELL
            # SL above the bearish order block high
            if factors.ob_level > 0:
                ob = self._find_ob(htf_state, "bearish", factors.ob_level)
                if ob:
                    sl = ob.high * (1 + buffer_pct)

            if sl is None:
                sl = entry * (1 + 0.015)

            # TP: nearest equal_lows liquidity pool below entry
            tp_pools = [
                p for p in htf_state.liquidity_pools
                if p.pool_type == "equal_lows" and p.price < entry and not p.swept
            ]
            if tp_pools:
                tp = max(tp_pools, key=lambda p: p.price).price * 1.0005
            else:
                tp = htf_state.last_swing_low * 1.0005

        return sl, tp

    @staticmethod
    def _find_ob(htf_state: HTFState, direction: str, near_price: float) -> OrderBlock | None:
        """Find the OB with the given direction closest to near_price."""
        candidates = [
            ob for ob in htf_state.order_blocks
            if ob.direction == direction and not ob.mitigated
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda ob: abs(ob.midpoint - near_price))


# ─── Confluence Builder Helper ────────────────────────────────

def build_factors(
    direction:       Literal["bullish", "bearish"],
    htf_state:       HTFState,
    ltf_event:       StructureEvent | None,
    current_price:   float,
    sweep_detected:  bool = False,
    sweep_level:     float = 0.0,
) -> ConfluenceFactors:
    """
    Convenience function to build a ConfluenceFactors object from the current
    engine state, ready to pass into Scorer.evaluate().
    """
    factors = ConfluenceFactors()

    # 1. Liquidity sweep
    factors.liquidity_sweep = sweep_detected
    factors.sweep_level     = sweep_level

    # 2. Order block tap — check if price is inside a valid OB
    for ob in htf_state.order_blocks:
        if ob.direction == direction and not ob.mitigated:
            if ob.is_tapped(current_price, current_price):
                factors.order_block_tap = True
                factors.ob_level        = ob.midpoint
                factors.ob_direction    = ob.direction
                break

    # 3. BOS or CHOCH on LTF
    if ltf_event and ltf_event.direction == direction:
        factors.bos_choch = True

    # 4. FVG — check if price is inside an open fair value gap
    for fvg in htf_state.fvgs:
        if fvg.direction == direction and not fvg.filled:
            if fvg.contains_price(current_price):
                factors.fvg        = True
                factors.fvg_top    = fvg.top
                factors.fvg_bottom = fvg.bottom
                break

    # 5. HTF bias alignment
    factors.htf_bias_aligned = (
        (direction == "bullish" and htf_state.bias == "bullish") or
        (direction == "bearish" and htf_state.bias == "bearish")
    )

    return factors