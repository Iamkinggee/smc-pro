// api/src/routes/signals.ts

import { FastifyPluginAsync } from 'fastify';

export const signalRoutes: FastifyPluginAsync = async (fastify) => {
  // GET /signals — paginated signal feed
  fastify.get<{
    Querystring: {
      pair?: string;
      type?: 'BUY' | 'SELL';
      min_score?: number;
      limit?: number;
      offset?: number;
    }
  }>('/', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const { pair, type, min_score = 0, limit = 20, offset = 0 } = req.query;

    let query = fastify.supabase
      .from('signals')
      .select('*')
      .eq('status', 'active')
      .gte('confidence_score', min_score)
      .order('created_at', { ascending: false })
      .range(offset, offset + limit - 1);

    if (pair) query = query.eq('pair', pair);
    if (type) query = query.eq('type', type);

    const { data, error, count } = await query;
    if (error) return reply.status(500).send({ error: error.message });
    return { signals: data, total: count, limit, offset };
  });

  // GET /signals/:id — single signal detail
  fastify.get<{ Params: { id: string } }>('/:id', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const { data, error } = await fastify.supabase
      .from('signals')
      .select('*')
      .eq('id', req.params.id)
      .single();
    if (error) return reply.status(404).send({ error: 'Signal not found' });
    return data;
  });

  // POST /signals/internal — called by Python engine (service-key protected)
  fastify.post<{ Body: any }>('/internal', {
    preHandler: async (req, reply) => {
      if (req.headers['x-internal-key'] !== process.env.INTERNAL_API_KEY) {
        return reply.status(403).send({ error: 'Forbidden' });
      }
    },
  }, async (req, reply) => {
    const { buildSignal } = await import('../services/signal.js');
    const signal = buildSignal(req.body);

    if (!signal) {
      return reply.status(422).send({ error: 'Signal rejected: below threshold or RR' });
    }

    // Persist to DB
    const { data, error } = await fastify.supabase
      .from('signals')
      .insert({
        pair:             signal.pair,
        type:             signal.type,
        entry:            signal.entry,
        stop_loss:        signal.stop_loss,
        take_profit:      signal.take_profit,
        confidence_score: signal.confidence_score,
        confluences:      signal.confluences,
        ai_explanation:   signal.ai_explanation,
        htf_bias:         signal.htf_bias,
        raw_scores:       signal.raw_scores,
        timeframe:        signal.timeframe,
        expires_at:       new Date(Date.now() + 4 * 60 * 60 * 1000).toISOString(), // 4hr TTL
      })
      .select()
      .single();

    if (error) return reply.status(500).send({ error: error.message });

    // Publish to Redis for WebSocket broadcast
    await fastify.redis.publish('signals:new', JSON.stringify(data));

    return reply.status(201).send(data);
  });

  // POST /signals/:id/interact — save/take/review a signal
  fastify.post<{
    Params: { id: string };
    Body: { action: 'saved' | 'taken' | 'reviewed' | 'ignored'; notes?: string };
  }>('/:id/interact', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const user = (req as any).user;
    const { action, notes } = req.body;

    const { data, error } = await fastify.supabase
      .from('user_signals')
      .upsert({
        user_id:   user.id,
        signal_id: req.params.id,
        action,
        notes,
      }, { onConflict: 'user_id,signal_id' })
      .select()
      .single();

    if (error) return reply.status(500).send({ error: error.message });
    return data;
  });
};