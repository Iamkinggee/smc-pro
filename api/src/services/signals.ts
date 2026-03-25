// api/src/services/signal.ts

import { computeConfluenceScore, ConfluentFactors, WeightConfig } from './confluence.js';
import { createClient } from '@supabase/supabase-js';

export interface RawEnginePayload {
  pair:           string;
  type:           'BUY' | 'SELL';
  entry:          number;
  stop_loss:      number;
  take_profit:    number;
  htf_bias:       string;
  timeframe:      string;
  factors:        ConfluentFactors;
  timestamp:      number;
}

export interface SignalOutput {
  id?:              string;
  pair:             string;
  type:             'BUY' | 'SELL';
  entry:            number;
  stop_loss:        number;
  take_profit:      number;
  confidence_score: number;
  confluences:      string[];
  ai_explanation:   string;
  grade:            string;
  rr_ratio:         number;
  raw_scores:       Record<string, number>;
  htf_bias:         string;
  timeframe:        string;
  timestamp:        number;
}

const MIN_RR = parseFloat(process.env.MIN_RR_RATIO ?? '2.0');
const SCORE_THRESHOLD = parseInt(process.env.SCORE_THRESHOLD ?? '65');

export function buildSignal(
  payload: RawEnginePayload,
  weights?: WeightConfig
): SignalOutput | null {
  // 1. Compute RR
  const rr = computeRR(payload);
  if (rr < MIN_RR) return null; // Reject low RR signals

  // 2. Compute confluence score
  const score = computeConfluenceScore(payload.factors, weights, SCORE_THRESHOLD);
  if (!score.passed) return null; // Below threshold

  // 3. Build AI explanation
  const explanation = buildExplanation(payload, score);

  return {
    pair:             payload.pair,
    type:             payload.type,
    entry:            payload.entry,
    stop_loss:        payload.stop_loss,
    take_profit:      payload.take_profit,
    confidence_score: score.total,
    confluences:      score.confluences,
    ai_explanation:   explanation,
    grade:            score.grade,
    rr_ratio:         rr,
    raw_scores:       score.breakdown,
    htf_bias:         payload.htf_bias,
    timeframe:        payload.timeframe,
    timestamp:        payload.timestamp,
  };
}

function computeRR(p: RawEnginePayload): number {
  const risk   = Math.abs(p.entry - p.stop_loss);
  const reward = Math.abs(p.take_profit - p.entry);
  if (risk === 0) return 0;
  return parseFloat((reward / risk).toFixed(2));
}

function buildExplanation(
  payload: RawEnginePayload,
  score: ReturnType<typeof computeConfluenceScore>
): string {
  const dir = payload.type === 'BUY' ? 'bullish' : 'bearish';
  const parts: string[] = [
    `${payload.pair} showing a ${dir} SMC setup on ${payload.timeframe}.`,
  ];

  if (payload.factors.liquiditySweep) {
    parts.push(`Price swept ${dir === 'bullish' ? 'sell-side' : 'buy-side'} liquidity, suggesting institutional accumulation.`);
  }
  if (payload.factors.orderBlockTap) {
    parts.push(`Entry aligns with a ${payload.htf_bias} order block — a key institutional footprint zone.`);
  }
  if (payload.factors.bosChoch) {
    parts.push(`Structure shift (BOS/CHOCH) confirmed on LTF, validating directional intent.`);
  }
  if (payload.factors.fvgPresent) {
    parts.push(`An unmitigated Fair Value Gap exists in the entry zone — price imbalance targeting.`);
  }
  parts.push(`Confluence score: ${score.total}/100 (${score.grade}). Risk-reward is favorable for this setup.`);

  return parts.join(' ');
}