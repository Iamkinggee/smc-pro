# SMC Pro — AI-Powered Trading Signal Engine

## Monorepo Structure

```
smc-pro/
├── engine/          # Python SMC signal engine (Phase 1)
├── api/             # Node.js Fastify API (Phase 2)
├── mobile/          # React Native / Expo app (Phase 3)
└── ai/              # AI mentor service (Phase 4)
```

## Phase 1 — Quick Start

### Prerequisites
- Python 3.11+
- An [Upstash Redis](https://upstash.com) account (free)

### Setup

```bash
cd engine
pip install -r requirements.txt
cp ../.env.example .env
# Fill in your UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN
python main.py
```

### What Phase 1 Does
1. Connects to Binance public WebSocket (no API key needed)
2. Backfills 500 candles per timeframe via Binance REST
3. Builds live 1m / 5m / 1H / 4H OHLCV candles in memory
4. Detects: Order Blocks, Fair Value Gaps, Liquidity Pools, BOS, CHOCH
5. Publishes SMC events to Redis pub/sub channel `smc:signals`

### Verifying It Works
You should see console output like:
```
[WS] Connected to BTCUSDT
[HTF] Bullish OB detected: 42150.00–42380.00 (1H)
[LTF] CHOCH bullish confirmed at 42210.00
[SIGNAL] Published to Redis: BTCUSDT BUY 87%
```