import os
import logging
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv
from groq import AsyncGroq
from typing import AsyncGenerator

load_dotenv()

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
MODEL = "llama-3.1-70b-versatile"
MAX_OUTPUT_TOKENS = 1024
TEMPERATURE = 0.4

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is not set")

client = AsyncGroq(api_key=GROQ_API_KEY)


# ── Streaming ──────────────────────────────────────────────────────────
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


# ── Context Dataclasses ────────────────────────────────────────────────
@dataclass
class UserContext:
    user_id:     str
    skill_level: str = "beginner"          # beginner | intermediate | advanced
    memory:      list = field(default_factory=list)   # past Q&A summaries
    trade_stats: dict = field(default_factory=dict)   # win_rate, avg_rr, etc.


@dataclass
class SignalContext:
    pair:             str
    type:             str    # BUY | SELL
    entry:            float
    stop_loss:        float
    take_profit:      float
    confidence_score: float
    confluences:      list = field(default_factory=list)
    htf_bias:         str  = ""
    timeframe:        str  = "5M"


# ── Prompt Builder ─────────────────────────────────────────────────────
class PromptBuilder:
    """
    Builds (system_prompt, user_message) tuples for each AI mode.
    Modes: chat | explain | review | mentor
    """

    # Shared SMC persona injected into every system prompt
    _PERSONA = """You are SMC Pro AI — an elite Smart Money Concepts trading mentor and signal analyst.
You ONLY use SMC and price action concepts: order blocks, liquidity sweeps, BOS/CHOCH, fair value gaps (FVG), HTF/LTF confluence.
You NEVER mention RSI, MACD, Bollinger Bands, or any lagging indicators.
Be concise, precise, and educational. Adapt your language to the user's skill level.
Always ground your answers in the current market context when provided."""

    def build(
        self,
        mode:          str,
        user_message:  str,
        user_ctx:      Optional[UserContext]  = None,
        signal_ctx:    Optional[SignalContext] = None,
        convo_history: list                   = [],
    ) -> tuple[str, str]:
        """
        Returns (system_prompt, user_message_with_context).
        """
        builder = {
            "chat":    self._build_chat,
            "explain": self._build_explain,
            "review":  self._build_review,
            "mentor":  self._build_mentor,
        }.get(mode, self._build_chat)

        return builder(user_message, user_ctx, signal_ctx, convo_history)

    # ── Chat mode ──────────────────────────────────────────────────────
    def _build_chat(self, message, user_ctx, signal_ctx, history):
        skill = user_ctx.skill_level if user_ctx else "beginner"

        system = f"""{self._PERSONA}

SKILL LEVEL: {skill}
{"- Use simple language, define SMC terms when you use them." if skill == "beginner" else ""}
{"- Use intermediate SMC vocabulary. Brief definitions where needed." if skill == "intermediate" else ""}
{"- Use full professional SMC terminology. No hand-holding." if skill == "advanced" else ""}

{self._format_signal_context(signal_ctx)}
{self._format_memory(user_ctx)}
{self._format_history(history)}"""

        return system, message

    # ── Explain signal mode ────────────────────────────────────────────
    def _build_explain(self, message, user_ctx, signal_ctx, history):
        if not signal_ctx:
            return self._build_chat(message, user_ctx, signal_ctx, history)

        skill = user_ctx.skill_level if user_ctx else "beginner"
        rr = round(
            abs(signal_ctx.take_profit - signal_ctx.entry) /
            max(abs(signal_ctx.entry - signal_ctx.stop_loss), 0.0001),
            2
        )

        system = f"""{self._PERSONA}

SKILL LEVEL: {skill}

You are explaining a specific trading signal. Cover ALL of the following in order:
1. What the setup is and why it qualifies as high-probability SMC
2. Each confluence factor and why it matters
3. Why entry, SL, and TP are placed where they are
4. What could invalidate this setup
5. Confidence score justification

Be educational but efficient. No fluff."""

        user_msg = f"""Explain this {signal_ctx.type} signal on {signal_ctx.pair}:

Entry:            {signal_ctx.entry}
Stop Loss:        {signal_ctx.stop_loss}
Take Profit:      {signal_ctx.take_profit}
Risk/Reward:      1:{rr}
Confidence:       {signal_ctx.confidence_score}%
HTF Bias:         {signal_ctx.htf_bias}
Timeframe:        {signal_ctx.timeframe}
Confluences:      {', '.join(signal_ctx.confluences) if signal_ctx.confluences else 'None listed'}

User question: {message if message else 'Please explain this full setup.'}"""

        return system, user_msg

    # ── Trade review mode ──────────────────────────────────────────────
    def _build_review(self, message, user_ctx, signal_ctx, history):
        skill = user_ctx.skill_level if user_ctx else "beginner"

        system = f"""{self._PERSONA}

SKILL LEVEL: {skill}

You are reviewing a completed trade. Your job:
1. Identify what the trader did correctly (SMC principles)
2. Identify mistakes: entry timing, SL placement, TP target, confluence misread
3. Explain what a textbook SMC execution would have looked like
4. Give 1-2 actionable improvements for next time
Be honest but constructive. Focus on process, not just outcome."""

        user_msg = f"""Please review this trade:

{self._format_signal_context(signal_ctx)}

Trader's notes / question:
{message}"""

        return system, user_msg

    # ── Mentor mode ────────────────────────────────────────────────────
    def _build_mentor(self, message, user_ctx, signal_ctx, history):
        skill      = user_ctx.skill_level if user_ctx else "beginner"
        stats      = user_ctx.trade_stats if user_ctx else {}
        win_rate   = stats.get("win_rate", "unknown")
        avg_rr     = stats.get("avg_rr", "unknown")
        total      = stats.get("total_trades", 0)

        system = f"""{self._PERSONA}

MENTOR MODE — USER PROFILE:
Skill level:    {skill}
Total trades:   {total}
Win rate:       {win_rate}
Avg RR:         {avg_rr}

{self._format_memory(user_ctx)}

As their personal mentor:
- Adapt teaching depth to their level
- Reference their actual stats when relevant
- Guide them toward better SMC habits
- Ask clarifying questions if the situation is ambiguous
- Celebrate progress, address weaknesses directly"""

        user_msg = f"""{self._format_history(history)}

Student: {message}"""

        return system, user_msg

    # ── Helpers ────────────────────────────────────────────────────────
    def _format_signal_context(self, ctx: Optional[SignalContext]) -> str:
        if not ctx:
            return ""
        return f"""
CURRENT SIGNAL CONTEXT:
  Pair:       {ctx.pair}
  Direction:  {ctx.type}
  Entry:      {ctx.entry}
  Stop Loss:  {ctx.stop_loss}
  Take Profit:{ctx.take_profit}
  Confidence: {ctx.confidence_score}%
  HTF Bias:   {ctx.htf_bias}
  Timeframe:  {ctx.timeframe}
  Confluences:{', '.join(ctx.confluences) if ctx.confluences else 'None'}"""

    def _format_memory(self, ctx: Optional[UserContext]) -> str:
        if not ctx or not ctx.memory:
            return ""
        memories = "\n".join(f"  - {m}" for m in ctx.memory[-5:])  # last 5
        return f"\nUSER MEMORY (recent context):\n{memories}"

    def _format_history(self, history: list) -> str:
        if not history:
            return ""
        lines = []
        for msg in history[-6:]:  # last 6 turns
            role = "User" if msg.get("role") == "user" else "Assistant"
            lines.append(f"{role}: {msg.get('content', '')}")
        return "\nCONVERSATION HISTORY:\n" + "\n".join(lines)