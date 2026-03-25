
import sys
import json
import asyncio
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Allow project root imports when spawned from api/
# sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__file__)))

from ai.llm_client     import stream_response
from ai.prompt_builder import PromptBuilder, UserContext, SignalContext


async def main():
    raw = sys.stdin.read()
    payload = json.loads(raw)

    mode            = payload.get("mode", "chat")
    message         = payload.get("message", "")
    history         = payload.get("history", [])
    signal_data     = payload.get("signal_context")
    user_data       = payload.get("user_context", {})

    # Build context objects
    user_ctx = UserContext(
        user_id     = user_data.get("user_id", "anon"),
        skill_level = user_data.get("skill_level", "beginner"),
        memory      = user_data.get("memory", []),
        trade_stats = user_data.get("trade_stats", {}),
    ) if user_data else None

    signal_ctx = SignalContext(
        pair             = signal_data.get("pair", ""),
        type             = signal_data.get("type", ""),
        entry            = float(signal_data.get("entry", 0)),
        stop_loss        = float(signal_data.get("stop_loss", 0)),
        take_profit      = float(signal_data.get("take_profit", 0)),
        confidence_score = float(signal_data.get("confidence_score", 0)),
        confluences      = signal_data.get("confluences", []),
        htf_bias         = signal_data.get("htf_bias", ""),
        timeframe        = signal_data.get("timeframe", "5M"),
    ) if signal_data else None

    builder = PromptBuilder()
    system_prompt, user_message = builder.build(
        mode         = mode,
        user_message = message,
        user_ctx     = user_ctx,
        signal_ctx   = signal_ctx,
        convo_history = history,
    )

    # Stream tokens → stdout (Node.js reads these)
    async for chunk in stream_response(system_prompt, user_message):
        sys.stdout.write(chunk)
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())