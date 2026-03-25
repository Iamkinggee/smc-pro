// api/src/routes/auth.ts

import { FastifyPluginAsync } from 'fastify';

export const authRoutes: FastifyPluginAsync = async (fastify) => {
  // POST /auth/signup
  fastify.post<{
    Body: { email: string; password: string }
  }>('/sign-up', {
    schema: {
      body: {
        type: 'object',
        required: ['email', 'password'],
        properties: {
          email:    { type: 'string', format: 'email' },
          password: { type: 'string', minLength: 8 },
        },
      },
    },
  }, async (req, reply) => {
    const { email, password } = req.body;
    const { data, error } = await fastify.supabase.auth.signUp({ email, password });
    if (error) return reply.status(400).send({ error: error.message });
    return reply.status(201).send({
      user:    data.user,
      session: data.session,
    });
  });

  // POST /auth/signin
  fastify.post<{
    Body: { email: string; password: string }
  }>('/sign-in', {
    schema: {
      body: {
        type: 'object',
        required: ['email', 'password'],
        properties: {
          email:    { type: 'string', format: 'email' },
          password: { type: 'string' },
        },
      },
    },
  }, async (req, reply) => {
    const { email, password } = req.body;
    const { data, error } = await fastify.supabase.auth.signInWithPassword({ email, password });
    if (error) return reply.status(401).send({ error: error.message });
    return {
      user:         data.user,
      access_token: data.session?.access_token,
      expires_at:   data.session?.expires_at,
    };
  });

  // POST /auth/signout
  fastify.post('/sign-out', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const token = req.headers.authorization!.slice(7);
    await fastify.supabase.auth.admin.signOut(token);
    return { message: 'Signed out' };
  });

  // POST /auth/refresh
  fastify.post<{
    Body: { refresh_token: string }
  }>('/refresh', async (req, reply) => {
    const { refresh_token } = req.body;
    const { data, error } = await fastify.supabase.auth.refreshSession({ refresh_token });
    if (error) return reply.status(401).send({ error: error.message });
    return {
      access_token: data.session?.access_token,
      expires_at:   data.session?.expires_at,
    };
  });
};