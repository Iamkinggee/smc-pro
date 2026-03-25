export default async function tradeReviewRoutes(fastify) {
  fastify.post('/ai/trade-review', {
    preHandler: [fastify.authenticate],
  }, async (request, reply) => {
    const { trade, user_context } = request.body;
    const { spawn } = await import('node:child_process');
    const path      = await import('node:path');

    const AI_WORKER = path.resolve(process.cwd(), '../mobile-ai/ai/chat_worker.py');
    const payload   = JSON.stringify({
      mode:         'review',
      message:      `Review this trade: ${JSON.stringify(trade)}`,
      user_context,
      signal_context: {
        pair:        trade.pair,
        type:        trade.type,
        entry:       trade.entry_price,
        stop_loss:   trade.entry_price,
        take_profit: trade.exit_price,
        confidence_score: 0,
        confluences: [],
      },
    });

    return new Promise((resolve, reject) => {
      const worker = spawn('python3', [AI_WORKER], { stdio: ['pipe','pipe','pipe'] });
      worker.stdin.write(payload);
      worker.stdin.end();

      let output = '';
      worker.stdout.on('data', d => output += d.toString());
      worker.on('close', () => {
        // Parse score and skill_tags from output
        const scoreMatch = output.match(/Overall Score[^\d]*(\d+)/i);
        const score = scoreMatch ? parseInt(scoreMatch[1]) : 60;
        const skillTags = extractSkillTags(output);
        resolve({ review: output, score, skill_tags: skillTags });
      });
      worker.on('error', reject);
    });
  });
}

function extractSkillTags(text) {
  const tags = [];
  if (/order block/i.test(text))  tags.push('Order Blocks');
  if (/liquidity/i.test(text))    tags.push('Liquidity');
  if (/bos|choch/i.test(text))    tags.push('BOS/CHOCH');
  if (/fvg|fair value/i.test(text)) tags.push('FVG');
  if (/stop loss|sl/i.test(text)) tags.push('SL Placement');
  if (/risk|rr ratio/i.test(text)) tags.push('Risk Management');
  return tags;
}