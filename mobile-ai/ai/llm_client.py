# ai/llm_client.py
import os
import logging
from typing import AsyncGenerator
from dotenv import load_dotenv
from groq import AsyncGroq

# Must load BEFORE os.getenv
load_dotenv()

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
MODEL = "llama-3.3-70b-versatile"
MAX_OUTPUT_TOKENS = 1024
TEMPERATURE  = 0.4

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is not set")

client = AsyncGroq(api_key=GROQ_API_KEY)


async def stream_response(
    system_prompt: str,
    user_message: str,
) -> AsyncGenerator[str, None]:
    try:
        stream = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=TEMPERATURE,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as e:
        logger.error(f"Groq streaming failed: {e}")
        yield "\n[Error generating response]\n"


async def complete(system_prompt: str, user_message: str) -> str:
    parts = []
    async for chunk in stream_response(system_prompt, user_message):
        parts.append(chunk)
    return "".join(parts)


__all__ = ["stream_response", "complete"]