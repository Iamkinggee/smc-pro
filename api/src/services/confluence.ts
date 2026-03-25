// api/src/services/confluence.ts

export interface ConfluentFactors {
  liquiditySweep:   boolean;
  orderBlockTap:    boolean;
  bosChoch:         boolean;
  fvgPresent:       boolean;
  htfBias:          boolean;
  // enriched from Phase 1 engine output
  obStrength?:      number;   // 0-1, how deep into OB
  fvgFillPercent?:  number;   // 0-1
  sweepDepth?:      number;   // how far price swept past liquidity
}

export interface WeightConfig {
  liquiditySweep: number;
  orderBlockTap:  number;
  bosChoch:       number;
  fvg:            number;
  htfBias:        number;
}

export interface ScoreResult {
  total:      number;          // 0-100
  breakdown:  Record<string, number>;
  passed:     boolean;
  grade:      'A' | 'B' | 'C' | 'D' | 'F';
  confluences: string[];
}

const DEFAULT_WEIGHTS: WeightConfig = {
  liquiditySweep: 30,
  orderBlockTap:  25,
  bosChoch:       20,
  fvg:            15,
  htfBias:        10,
};

const DEFAULT_THRESHOLD = 65;

export function computeConfluenceScore(
  factors: ConfluentFactors,
  weights: WeightConfig = DEFAULT_WEIGHTS,
  threshold: number = DEFAULT_THRESHOLD
): ScoreResult {
  const breakdown: Record<string, number> = {};
  const confluences: string[] = [];
  let total = 0;

  // --- Liquidity Sweep (max 30pts) ---
  if (factors.liquiditySweep) {
    // Bonus: deeper sweep = higher score
    const depthBonus = factors.sweepDepth
      ? Math.min(factors.sweepDepth * 5, 5) // up to +5 bonus
      : 0;
    const score = Math.min(weights.liquiditySweep + depthBonus, 35);
    breakdown.liquiditySweep = score;
    total += score;
    confluences.push('Liquidity Sweep');
  } else {
    breakdown.liquiditySweep = 0;
  }

  // --- Order Block Tap (max 25pts) ---
  if (factors.orderBlockTap) {
    // Bonus: tapping body vs wick, strength of OB
    const strengthBonus = factors.obStrength
      ? factors.obStrength * 5  // up to +5
      : 0;
    const score = Math.min(weights.orderBlockTap + strengthBonus, 30);
    breakdown.orderBlockTap = score;
    total += score;
    confluences.push('Order Block Tap');
  } else {
    breakdown.orderBlockTap = 0;
  }

  // --- BOS / CHOCH (max 20pts) ---
  if (factors.bosChoch) {
    breakdown.bosChoch = weights.bosChoch;
    total += weights.bosChoch;
    confluences.push('BOS/CHOCH Confirmation');
  } else {
    breakdown.bosChoch = 0;
  }

  // --- FVG (max 15pts) ---
  if (factors.fvgPresent) {
    // Partial fill FVGs score higher (more likely to be targeted)
    const fillBonus = factors.fvgFillPercent
      ? (1 - factors.fvgFillPercent) * 5  // unfilled = higher score
      : 0;
    const score = Math.min(weights.fvg + fillBonus, 20);
    breakdown.fvg = score;
    total += score;
    confluences.push('Fair Value Gap');
  } else {
    breakdown.fvg = 0;
  }

  // --- HTF Bias (max 10pts) ---
  if (factors.htfBias) {
    breakdown.htfBias = weights.htfBias;
    total += weights.htfBias;
    confluences.push('HTF Bias Aligned');
  } else {
    breakdown.htfBias = 0;
  }

  // Cap at 100
  total = Math.min(Math.round(total), 100);

  return {
    total,
    breakdown,
    passed: total >= threshold,
    grade: gradeScore(total),
    confluences,
  };
}

function gradeScore(score: number): 'A' | 'B' | 'C' | 'D' | 'F' {
  if (score >= 85) return 'A';
  if (score >= 75) return 'B';
  if (score >= 65) return 'C';
  if (score >= 50) return 'D';
  return 'F';
}

// Dynamic weight update (for user-configurable weights via API)
export function buildWeightConfig(
  overrides: Partial<WeightConfig>
): WeightConfig {
  const merged: WeightConfig = { ...DEFAULT_WEIGHTS, ...overrides };

  const total = Object.values(merged).reduce((a, b) => a + b, 0);
  const factor = 100 / total;

  return {
    liquiditySweep: Math.round(merged.liquiditySweep * factor),
    orderBlockTap:  Math.round(merged.orderBlockTap * factor),
    bosChoch:       Math.round(merged.bosChoch * factor),
    fvg:            Math.round(merged.fvg * factor),
    htfBias:        Math.round(merged.htfBias * factor),
  };
}