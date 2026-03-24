"""
test_engine.py — Phase 1 unit tests

Run with:  python test_engine.py
(No test framework needed — uses built-in unittest)

Tests cover:
  - CandleBuilder: ingest, store, retrieve
  - HTFAnalyzer: OB detection, FVG detection, liquidity pools, bias
  - LTFTrigger: BOS, CHOCH, liquidity sweep detection
  - Scorer: confluence scoring, SL/TP, RR check
"""

import sys
import time
import unittest

# ─── Test Helpers ─────────────────────────────────────────────

def make_candle(open_: float, high: float, low: float, close: float,
                volume: float = 1000.0, ts_offset: int = 0) -> dict:
    """Create a test candle dict."""
    base_ts = 1_700_000_000_000  # arbitrary base timestamp (ms)
    return {
        "open_time":  base_ts + ts_offset * 60_000,
        "open":       open_,
        "high":       high,
        "low":        low,
        "close":      close,
        "volume":     volume,
        "close_time": base_ts + ts_offset * 60_000 + 59_999,
    }


def make_trending_candles(start: float, direction: str = "up", count: int = 60) -> list[dict]:
    """Generate a sequence of trending candles for structure testing."""
    candles = []
    price = start
    for i in range(count):
        if direction == "up":
            o = price
            c = price * 1.002
            h = c * 1.001
            l = o * 0.9995
        else:
            o = price
            c = price * 0.998
            h = o * 1.0005
            l = c * 0.999
        candles.append(make_candle(o, h, l, c, ts_offset=i))
        price = c
    return candles


# ─── CandleBuilder Tests ──────────────────────────────────────

class TestCandleBuilder(unittest.TestCase):

    def setUp(self):
        from candle_builder import CandleBuilder
        self.cb = CandleBuilder()

    def test_ingest_historical(self):
        candles = [make_candle(100, 105, 99, 104, ts_offset=i) for i in range(20)]
        self.cb.ingest_historical("BTCUSDT", "1h", candles)
        result = self.cb.get_candles("BTCUSDT", "1h")
        self.assertEqual(len(result), 20)

    def test_get_candles_limit(self):
        candles = [make_candle(100, 105, 99, 104, ts_offset=i) for i in range(100)]
        self.cb.ingest_historical("BTCUSDT", "1h", candles)
        result = self.cb.get_candles("BTCUSDT", "1h", n=10)
        self.assertEqual(len(result), 10)
        # Should return the most recent 10
        self.assertEqual(result[-1]["open_time"], candles[-1]["open_time"])

    def test_has_enough_data_false(self):
        candles = [make_candle(100, 105, 99, 104, ts_offset=i) for i in range(10)]
        self.cb.ingest_historical("BTCUSDT", "1h", candles)
        self.assertFalse(self.cb.has_enough_data("BTCUSDT", "1h", minimum=50))

    def test_has_enough_data_true(self):
        candles = [make_candle(100, 105, 99, 104, ts_offset=i) for i in range(60)]
        self.cb.ingest_historical("BTCUSDT", "1h", candles)
        self.assertTrue(self.cb.has_enough_data("BTCUSDT", "1h", minimum=50))

    def test_different_pairs_isolated(self):
        btc_candles = [make_candle(42000, 42500, 41800, 42300, ts_offset=i) for i in range(10)]
        eth_candles = [make_candle(2200, 2250, 2180, 2230, ts_offset=i) for i in range(5)]
        self.cb.ingest_historical("BTCUSDT", "1h", btc_candles)
        self.cb.ingest_historical("ETHUSDT", "1h", eth_candles)
        self.assertEqual(len(self.cb.get_candles("BTCUSDT", "1h")), 10)
        self.assertEqual(len(self.cb.get_candles("ETHUSDT", "1h")), 5)


# ─── HTFAnalyzer Tests ────────────────────────────────────────

class TestHTFAnalyzer(unittest.TestCase):

    def setUp(self):
        from htf_analyzer import HTFAnalyzer
        self.analyzer = HTFAnalyzer()

    def test_bullish_bias_detected(self):
        candles = make_trending_candles(42000, direction="up", count=60)
        state = self.analyzer.analyze("BTCUSDT", "1h", candles)
        self.assertEqual(state.bias, "bullish")

    def test_bearish_bias_detected(self):
        candles = make_trending_candles(42000, direction="down", count=60)
        state = self.analyzer.analyze("BTCUSDT", "1h", candles)
        self.assertEqual(state.bias, "bearish")

    def test_fvg_detection(self):
        # Construct a 3-candle FVG pattern manually:
        # c1: normal candle up to 100
        # c2: strong bullish impulse
        # c3: opens with gap (low > c1.high)
        candles = [make_candle(90, 100, 89, 99, ts_offset=i) for i in range(20)]
        # Add a bullish FVG pattern at the end
        candles.append(make_candle(100, 102, 99, 101, ts_offset=20))   # c1
        candles.append(make_candle(101, 112, 100, 111, ts_offset=21))  # c2 impulse
        candles.append(make_candle(112, 115, 103, 114, ts_offset=22))  # c3 — low (103) > c1 high (102) = FVG

        state = self.analyzer.analyze("BTCUSDT", "1h", candles)
        bullish_fvgs = [f for f in state.fvgs if f.direction == "bullish"]
        self.assertGreater(len(bullish_fvgs), 0, "Expected at least one bullish FVG")

    def test_liquidity_pool_detected(self):
        # Create candles that repeatedly hit the same high (equal highs = liquidity)
        candles = []
        for i in range(30):
            # Most candles have highs around 42500 — equal highs pool
            candles.append(make_candle(42000, 42500, 41800, 42200, ts_offset=i))
        # Add a few with slightly different highs (within tolerance)
        candles[5]  = make_candle(42000, 42502, 41800, 42200, ts_offset=5)
        candles[10] = make_candle(42000, 42498, 41800, 42200, ts_offset=10)
        candles[20] = make_candle(42000, 42501, 41800, 42200, ts_offset=20)

        state = self.analyzer.analyze("BTCUSDT", "1h", candles)
        equal_highs = [p for p in state.liquidity_pools if p.pool_type == "equal_highs"]
        self.assertGreater(len(equal_highs), 0, "Expected equal highs liquidity pool")

    def test_returns_state_with_insufficient_candles(self):
        state = self.analyzer.analyze("BTCUSDT", "1h", [])
        self.assertIsNotNone(state)
        self.assertEqual(state.bias, "neutral")


# ─── LTFTrigger Tests ─────────────────────────────────────────

class TestLTFTrigger(unittest.TestCase):

    def setUp(self):
        from ltf_trigger import LTFTrigger
        self.trigger = LTFTrigger()

    def _make_ranging_candles(self, count: int = 20, base: float = 100.0) -> list[dict]:
        """Make sideways-ranging candles."""
        candles = []
        for i in range(count):
            candles.append(make_candle(base, base * 1.005, base * 0.995, base, ts_offset=i))
        return candles

    def test_bullish_bos_detected(self):
        candles = self._make_ranging_candles(20, 100.0)
        # Final candle breaks above the swing high
        candles.append(make_candle(100, 107, 99.5, 106.5, ts_offset=20))
        event = self.trigger.check("BTCUSDT", "1m", candles, htf_bias="bullish")
        self.assertIsNotNone(event)
        self.assertEqual(event.direction, "bullish")
        self.assertEqual(event.event_type, "BOS")

    def test_bearish_choch_detected(self):
        candles = self._make_ranging_candles(20, 100.0)
        # Final candle breaks below the swing low (against bullish bias = CHOCH)
        candles.append(make_candle(100, 100.5, 92, 92.5, ts_offset=20))
        event = self.trigger.check("BTCUSDT", "1m", candles, htf_bias="bullish")
        self.assertIsNotNone(event)
        self.assertEqual(event.direction, "bearish")
        self.assertEqual(event.event_type, "CHOCH")

    def test_no_event_on_ranging_candle(self):
        candles = self._make_ranging_candles(20, 100.0)
        event = self.trigger.check("BTCUSDT", "1m", candles, htf_bias="bullish")
        self.assertIsNone(event)

    def test_no_event_with_insufficient_candles(self):
        candles = self._make_ranging_candles(5, 100.0)
        event = self.trigger.check("BTCUSDT", "1m", candles)
        self.assertIsNone(event)

    def test_liquidity_sweep_equal_highs(self):
        pool_price = 100.0
        candles = [make_candle(99, 99.5, 98.5, 99, ts_offset=i) for i in range(4)]
        # Candle that wicks above pool but closes below = sweep
        candles.append(make_candle(99, 100.5, 98.8, 99.2, ts_offset=4))
        swept = self.trigger.check_liquidity_sweep(candles, pool_price, "equal_highs")
        self.assertTrue(swept)

    def test_no_sweep_when_close_above_pool(self):
        pool_price = 100.0
        candles = [make_candle(99, 99.5, 98.5, 99, ts_offset=i) for i in range(4)]
        # Candle wicks above and closes ABOVE pool = not a sweep
        candles.append(make_candle(99, 101, 98.8, 100.5, ts_offset=4))
        swept = self.trigger.check_liquidity_sweep(candles, pool_price, "equal_highs")
        self.assertFalse(swept)


# ─── Scorer Tests ─────────────────────────────────────────────

class TestScorer(unittest.TestCase):

    def setUp(self):
        from scorer import Scorer, ConfluenceFactors
        from htf_analyzer import HTFState, LiquidityPool
        self.Scorer = Scorer
        self.ConfluenceFactors = ConfluenceFactors
        self.HTFState = HTFState
        self.LiquidityPool = LiquidityPool

    def _make_ltf_event(self, direction="bullish", price=42300.0):
        from ltf_trigger import StructureEvent
        return StructureEvent(
            symbol="BTCUSDT",
            timeframe="5m",
            event_type="CHOCH",
            direction=direction,
            price=price,
            swing_ref=42200.0,
            open_time=int(time.time() * 1000),
        )

    def _make_htf_state_with_pools(self):
        from htf_analyzer import OrderBlock
        state = self.HTFState(symbol="BTCUSDT", timeframe="4h", bias="bullish")
        state.order_blocks = [
            OrderBlock(
                symbol="BTCUSDT", timeframe="4h", direction="bullish",
                high=42380.0, low=42100.0, open_time=1700000000000
            )
        ]
        state.liquidity_pools = [
            self.LiquidityPool(
                symbol="BTCUSDT", timeframe="4h",
                pool_type="equal_highs", price=43500.0, count=3
            )
        ]
        state.last_swing_high = 43500.0
        state.last_swing_low  = 41800.0
        return state

    def test_high_confidence_signal_emitted(self):
        scorer = self.Scorer(threshold=0.65, min_rr=2.0)
        factors = self.ConfluenceFactors(
            liquidity_sweep=True,
            order_block_tap=True,
            bos_choch=True,
            fvg=True,
            htf_bias_aligned=True,
            ob_level=42240.0,
            ob_direction="bullish",
        )
        htf_state  = self._make_htf_state_with_pools()
        ltf_event  = self._make_ltf_event("bullish", 42300.0)
        signal = scorer.evaluate("BTCUSDT", "BUY", 42300.0, factors, htf_state, ltf_event)

        self.assertIsNotNone(signal, "Expected a signal to be emitted")
        self.assertEqual(signal["type"], "BUY")
        self.assertEqual(signal["confidence_score"], "100%")
        self.assertGreater(signal["take_profit"], signal["entry"])
        self.assertLess(signal["stop_loss"], signal["entry"])

    def test_low_confidence_signal_rejected(self):
        scorer = self.Scorer(threshold=0.65, min_rr=2.0)
        # Only one factor present (10%) — below threshold
        factors = self.ConfluenceFactors(htf_bias_aligned=True)
        htf_state = self._make_htf_state_with_pools()
        ltf_event = self._make_ltf_event("bullish", 42300.0)
        signal = scorer.evaluate("BTCUSDT", "BUY", 42300.0, factors, htf_state, ltf_event)
        self.assertIsNone(signal, "Expected signal to be rejected (score too low)")

    def test_score_calculation(self):
        from scorer import WEIGHTS
        scorer = self.Scorer()
        factors = self.ConfluenceFactors(
            liquidity_sweep=True,   # 30%
            order_block_tap=True,   # 25%
            bos_choch=False,
            fvg=True,               # 15%
            htf_bias_aligned=False,
        )
        # Expected score: 0.30 + 0.25 + 0.15 = 0.70
        score, present = scorer._calculate_score(factors)
        self.assertAlmostEqual(score, 0.70, places=9)
        self.assertEqual(len(present), 3)

    def test_rr_below_minimum_rejected(self):
        scorer = self.Scorer(threshold=0.65, min_rr=2.0)
        factors = self.ConfluenceFactors(
            liquidity_sweep=True, order_block_tap=True, bos_choch=True
        )
        htf_state = self._make_htf_state_with_pools()
        # Set TP very close to entry so RR is below 2:1
        htf_state.last_swing_high = 42310.0   # tiny TP above entry 42300
        htf_state.liquidity_pools = []
        ltf_event = self._make_ltf_event("bullish", 42300.0)
        signal = scorer.evaluate("BTCUSDT", "BUY", 42300.0, factors, htf_state, ltf_event)
        self.assertIsNone(signal, "Expected signal rejected due to low RR")


# ─── Run all tests ────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("SMC Engine — Phase 1 Unit Tests")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    for test_class in [TestCandleBuilder, TestHTFAnalyzer, TestLTFTrigger, TestScorer]:
        suite.addTests(loader.loadTestsFromTestCase(test_class))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if result.wasSuccessful():
        print("\n✓ All tests passed")
        sys.exit(0)
    else:
        print(f"\n✗ {len(result.failures)} failure(s), {len(result.errors)} error(s)")
        sys.exit(1)