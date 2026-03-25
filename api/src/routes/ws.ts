// src/routes/ws.ts
import { FastifyPluginAsync } from 'fastify'
import fp from 'fastify-plugin'
import jwt from 'jsonwebtoken'

const JWT_SECRET = process.env.JWT_SECRET ?? 'dev-secret'

interface JwtPayload {
  userId: string
  email: string
}

function verifyToken(token: string): JwtPayload | null {
  try {
    return jwt.verify(token, JWT_SECRET) as JwtPayload
  } catch {
    return null
  }
}

const wsPlugin: FastifyPluginAsync = async (fastify) => {
  fastify.get('/', { websocket: true }, (socket, req) => {
    const ws = socket.socket

    const token = (req.query as Record<string, string>)['token']

    if (!token) {
      ws.close()
      return
    }

    const user = verifyToken(token)

    if (!user) {
      ws.close()
      return
    }

    fastify.log.info(`[WS] Connected: ${user.email}`)

    // Heartbeat — ping every 30s to keep Render proxy from killing idle connections
    const heartbeat = setInterval(() => {
      if (ws.readyState === ws.OPEN) {
        ws.ping()
      }
    }, 30_000)

    ws.on('pong', () => {
      fastify.log.debug('[WS] Pong received — connection alive')
    })

    ws.on('message', (raw) => {
      try {
        const data = JSON.parse(raw.toString())
        fastify.log.info({ data }, '[WS] Message received')
        ws.send(JSON.stringify({ ok: true }))
      } catch (err) {
        fastify.log.error(err, '[WS] Message parse error')
      }
    })

    ws.on('close', () => {
      clearInterval(heartbeat)
      fastify.log.info(`[WS] Disconnected: ${user.email}`)
    })

    ws.on('error', (err) => {
      clearInterval(heartbeat)
      fastify.log.error(err, '[WS] Socket error')
    })
  })
}

export const wsRoutes = fp(wsPlugin)