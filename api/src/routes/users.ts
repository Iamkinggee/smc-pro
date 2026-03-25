/**
 * routes/users.ts — User profile endpoints.
 *
 * All routes require authentication via fastify.authenticate.
 * Profiles are stored in a `profiles` table in Supabase that mirrors
 * auth.users with extra fields (skill_level, etc.).
 */

import { FastifyPluginAsync } from 'fastify'

export const userRoutes: FastifyPluginAsync = async (fastify) => {

  // GET /users/me — fetch authenticated user's profile
  fastify.get('/me', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const user = (req as any).user

    // Try to fetch extended profile from profiles table
    const { data: profile, error } = await fastify.supabase
      .from('profiles')
      .select('*')
      .eq('id', user.id)
      .single()

    if (error && error.code !== 'PGRST116') {
      // PGRST116 = row not found — we'll create it below
      return reply.status(500).send({ error: error.message })
    }

    if (!profile) {
      // Auto-create profile on first login
      const { data: created, error: createErr } = await fastify.supabase
        .from('profiles')
        .insert({
          id:          user.id,
          email:       user.email,
          skill_level: 'beginner',
        })
        .select()
        .single()

      if (createErr) return reply.status(500).send({ error: createErr.message })
      return { user: created }
    }

    return { user: profile }
  })

  // PATCH /users/me — update profile
  fastify.patch<{
    Body: {
      skill_level?: 'beginner' | 'intermediate' | 'advanced'
      display_name?: string
    }
  }>('/me', {
    preHandler: [fastify.authenticate],
    schema: {
      body: {
        type: 'object',
        properties: {
          skill_level:  { type: 'string', enum: ['beginner', 'intermediate', 'advanced'] },
          display_name: { type: 'string', maxLength: 50 },
        },
      },
    },
  }, async (req, reply) => {
    const user = (req as any).user

    const { data, error } = await fastify.supabase
      .from('profiles')
      .update({ ...req.body, updated_at: new Date().toISOString() })
      .eq('id', user.id)
      .select()
      .single()

    if (error) return reply.status(500).send({ error: error.message })
    return { user: data }
  })

  // POST /users/me/fcm-token — register push notification token
  fastify.post<{
    Body: { token: string }
  }>('/me/fcm-token', {
    preHandler: [fastify.authenticate],
    schema: {
      body: {
        type: 'object',
        required: ['token'],
        properties: {
          token: { type: 'string' },
        },
      },
    },
  }, async (req, reply) => {
    const user = (req as any).user

    const { error } = await fastify.supabase
      .from('fcm_tokens')
      .upsert({
        user_id:    user.id,
        token:      req.body.token,
        updated_at: new Date().toISOString(),
      }, { onConflict: 'user_id' })

    if (error) return reply.status(500).send({ error: error.message })
    return { ok: true }
  })
}