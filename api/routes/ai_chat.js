// Phase 4 — AI Chat Route (Fastify)
// POST /ai/chat — receives user message + history, streams SSE response token-by-token
// Uses Python AI service via child_process spawn (or HTTP if running separately)

import { execFile }   from "node:child_process";
import { promisify }  from "node:util";
import path           from "node:path";

const execFileAsync = promisify(execFile);

// ── Python bridge helper ──────────────────────────────────────────────────────
// Calls ai/chat_worker.py which reads stdin JSON and streams to stdout
const AI_WORKER = path.resolve(process.cwd(), "../ai/chat_worker.py");

/**
 * @param {import('fastify').FastifyInstance} fastify
 */
export default async function aiChatRoutes(fastify) {
  // ── POST /ai/chat (streaming SSE) ─────────────────────────────────────────
  fastify.post("/ai/chat", {
    schema: {
      body: {
        type: "object",
        required: ["message"],
        properties: {
          message:  { type: "string", maxLength: 2000 },
          history:  { type: "array",  items: {
            type: "object",
            properties: {
              role:    { type: "string", enum: ["user", "assistant"] },
              content: { type: "string" },
            },
          }},
          signal_context: { type: "object" },   // optional: attached signal
          mode: {
            type: "string",
            enum: ["chat", "signal_explanation", "mentor", "market_commentary"],
            default: "chat",
          },
        },
      },
    },
    preHandler: [fastify.authenticate],   // JWT guard
  }, async (request, reply) => {
    const { message, history = [], signal_context = null, mode = "chat" } = request.body;
    const user = request.user;            // from JWT

    // Fetch user context from DB (skill level + memory)
    const userCtx = await fastify.db.getUserContext(user.id);

    // Build the payload for the Python worker
    const payload = JSON.stringify({
      mode,
      message,
      history,
      signal_context,
      user_context: userCtx,
    });

    // ── Set SSE headers ──────────────────────────────────────────────────────
    reply.raw.writeHead(200, {
      "Content-Type":  "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection":    "keep-alive",
      "X-Accel-Buffering": "no",    // disable nginx buffering
    });

    // ── Spawn Python AI worker ───────────────────────────────────────────────
    const { spawn } = await import("node:child_process");
    const worker = spawn("python3", [AI_WORKER], { stdio: ["pipe", "pipe", "pipe"] });

    worker.stdin.write(payload);
    worker.stdin.end();

    let fullResponse = "";

    // Stream each token chunk to the client as SSE
    worker.stdout.on("data", (chunk) => {
      const text = chunk.toString();
      fullResponse += text;
      // SSE format: "data: <token>\n\n"
      reply.raw.write(`data: ${JSON.stringify({ token: text })}\n\n`);
    });

    worker.stderr.on("data", (data) => {
      fastify.log.error(`AI worker stderr: ${data}`);
    });

    worker.on("close", async (code) => {
      // Signal stream end
      reply.raw.write(`data: ${JSON.stringify({ done: true })}\n\n`);
      reply.raw.end();

      // Persist the exchange to conversation history
      try {
        await fastify.db.saveConversationTurn(user.id, {
          role:    "user",
          content: message,
        });
        await fastify.db.saveConversationTurn(user.id, {
          role:    "assistant",
          content: fullResponse,
        });

        // Update user memory asynchronously (fire-and-forget)
        fastify.db.updateUserMemory(user.id, message, fullResponse).catch(() => {});
      } catch (err) {
        fastify.log.error(`Failed to persist chat turn: ${err.message}`);
      }
    });

    // Handle client disconnect
    request.raw.on("close", () => {
      worker.kill("SIGTERM");
    });
  });


  // ── POST /ai/explain-signal ────────────────────────────────────────────────
  // Returns cached or freshly generated signal explanation (non-streaming)
  fastify.post("/ai/explain-signal", {
    schema: {
      body: {
        type: "object",
        required: ["signal"],
        properties: {
          signal: { type: "object" },
        },
      },
    },
    preHandler: [fastify.authenticate],
  }, async (request, reply) => {
    const { signal } = request.body;

    // Delegate to Python signal_explainer via HTTP (running on port 8001)
    const aiServiceUrl = process.env.AI_SERVICE_URL ?? "http://localhost:8001";

    const res = await fetch(`${aiServiceUrl}/explain`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ signal }),
    });

    if (!res.ok) {
      return reply.status(502).send({ error: "AI service unavailable" });
    }

    const data = await res.json();
    return reply.send({ explanation: data.explanation });
  });
}