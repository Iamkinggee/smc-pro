// // api/src/plugins/redis.ts

// import fp from 'fastify-plugin';
// import { createClient, RedisClientType } from 'redis';

// declare module 'fastify' {
//   interface FastifyInstance {
//     redis: RedisClientType;
//     redisSub: RedisClientType; // dedicated subscriber client
//   }
// }

// export const redisPlugin = fp(async (fastify) => {
//   const client = createClient({ url: process.env.REDIS_URL }) as RedisClientType;
//   const subClient = client.duplicate() as RedisClientType;

//   await client.connect();
//   await subClient.connect();

//   fastify.decorate('redis', client);
//   fastify.decorate('redisSub', subClient);

//   fastify.addHook('onClose', async () => {
//     await client.quit();
//     await subClient.quit();
//   });
// });







/**
 * plugins/redis.ts — Redis pub/sub plugin for Fastify.
 *
 * Creates two clients:
 *   fastify.redis     — for publishing and key-value ops
 *   fastify.redisSub  — dedicated subscriber (Redis protocol requires a
 *                       separate connection once subscribe() is called)
 *
 * Reads REDIS_URL from env. Falls back gracefully if Redis is unavailable
 * so the API still starts in dev without Redis configured.
 */

import fp from 'fastify-plugin'
import { createClient } from 'redis'
import type { RedisClientType } from 'redis'

declare module 'fastify' {
  interface FastifyInstance {
    redis:    RedisClientType
    redisSub: RedisClientType
  }
}

export const redisPlugin = fp(async (fastify) => {
  const url = process.env.REDIS_URL

  if (!url) {
    fastify.log.warn('[Redis] REDIS_URL not set — pub/sub disabled. WebSocket push will not work.')
    // Decorate with no-op stubs so the rest of the codebase doesn't crash
    const noop: any = new Proxy({}, {
      get: () => async () => {},
    })
    fastify.decorate('redis',    noop)
    fastify.decorate('redisSub', noop)
    return
  }

  const client    = createClient({ url }) as RedisClientType
  const subClient = client.duplicate()    as RedisClientType

  client.on('error',    (e) => fastify.log.error({ err: e }, '[Redis] client error'))
  subClient.on('error', (e) => fastify.log.error({ err: e }, '[Redis] sub client error'))

  await client.connect()
  await subClient.connect()

  fastify.log.info('[Redis] Connected')

  fastify.decorate('redis',    client)
  fastify.decorate('redisSub', subClient)

  fastify.addHook('onClose', async () => {
    await client.quit().catch(() => {})
    await subClient.quit().catch(() => {})
    fastify.log.info('[Redis] Disconnected')
  })
})