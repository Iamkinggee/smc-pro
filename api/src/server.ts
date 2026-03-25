




import Fastify from 'fastify'
import cors from '@fastify/cors'
import websocket from '@fastify/websocket'

import { redisPlugin } from './plugins/redis'
import { authPlugin } from './plugins/auth'
import { authRoutes } from './routes/auth'
import { userRoutes } from './routes/users'
import { signalRoutes } from './routes/signals'
import { wsRoutes } from './routes/ws'

const server = Fastify({
  logger: {
    level: process.env.LOG_LEVEL ?? 'info',
    transport:
      process.env.NODE_ENV === 'development'
        ? { target: 'pino-pretty' }
        : undefined,
  },
})

async function bootstrap() {
  // Plugins
  await server.register(cors, {
    origin:
      process.env.NODE_ENV === 'development'
        ? true
        : (process.env.CORS_ORIGIN ?? false),
  })

  await server.register(websocket)

  await server.register(redisPlugin)
  await server.register(authPlugin)

  // Routes
  await server.register(authRoutes, { prefix: '/auth' })
  await server.register(userRoutes, { prefix: '/users' })
  await server.register(signalRoutes, { prefix: '/signals' })
  await server.register(wsRoutes, { prefix: '/ws' })

  // Health
  server.get('/health', async () => ({
    status: 'ok',
    ts: Date.now(),
    env: process.env.NODE_ENV ?? 'production',
  }))

  // Start
  const port = parseInt(process.env.PORT ?? '3001')

  await server.listen({ port, host: '0.0.0.0' })

  server.log.info(`🚀 API running on port ${port}`)
}

bootstrap().catch((err: unknown) => {
  console.error('Fatal startup error:', err)
  process.exit(1)
})