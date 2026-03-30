"""
app/pipeline/llm.py — Gemini 2.0 Flash streaming LLM processor.

Architecture:
  llm_processor(session) reads final transcripts from session.llm_queue
  → calls Gemini Flash (streaming, with tool-calling)
  → buffers tokens until sentence boundary
  → pushes sentence chunks to session.tts_queue
  → handles tool_call responses inline (calls execute_tool)
  → logs TTFT, total generation time, input/output token counts
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import AsyncGenerator

import structlog
from google import genai
from google.genai import types as genai_types

from app.pipeline.prompts import SYSTEM_PROMPT
from app.pipeline.tools import ALL_TOOLS, execute_tool
from app.session import QUEUE_SENTINEL, VoiceSession

logger = structlog.get_logger(__name__)


async def llm_processor(session: VoiceSession) -> None:
    """Read STT final transcripts, call Gemini, and stream chunks to TTS."""
    log = logger.bind(session_id=str(session.id), task="llm")
    log.info("llm.start")

    # Initialize client (uses API key from settings)
    client = genai.Client(api_key=session.settings.gemini_api_key)

    config = genai_types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.3,
        tools=ALL_TOOLS,
    )

    try:
        while True:
            transcript = await session.llm_queue.get()
            if transcript is QUEUE_SENTINEL:
                log.info("llm.sentinel_received")
                break

            if not transcript.strip():
                continue

            log.info("llm.got_transcript", text=transcript)

            turn = session.metrics.current_turn
            if turn:
                turn.llm_start_s = time.monotonic()

            session.add_user_turn(transcript)

            try:
                response_stream = await client.aio.models.generate_content_stream(
                    model=session.settings.gemini_model,
                    contents=session.history,
                    config=config,
                )
            except Exception as e:
                log.error("llm.generate_error", error=str(e))
                await session.tts_queue.put("I'm sorry, I'm having trouble thinking right now.")
                continue

            buffer = ""
            ttft_logged = False
            total_text = ""
            total_input_tokens = 0
            total_output_tokens = 0

            async for chunk in response_stream:
                if not ttft_logged and turn:
                    turn.llm_first_token_s = time.monotonic()
                    ttft_logged = True
                    log.info("llm.ttft", ms=(turn.llm_first_token_s - turn.llm_start_s) * 1000)

                if chunk.usage_metadata:
                    total_input_tokens = chunk.usage_metadata.prompt_token_count
                    total_output_tokens = chunk.usage_metadata.candidates_token_count

                if chunk.text:
                    text_part = chunk.text
                    total_text += text_part
                    buffer += text_part

                    # Split on sentence boundaries
                    while True:
                        match = re.search(r'([.!?]+(?:\s+|\n|$))', buffer)
                        if not match:
                            break
                        
                        split_idx = match.end()
                        sentence = buffer[:split_idx].strip()
                        buffer = buffer[split_idx:]
                        
                        if sentence:
                            await session.tts_queue.put(sentence)

                if chunk.function_calls:
                    for tool_call in chunk.function_calls:
                        tool_name = tool_call.name
                        tool_args = tool_call.args
                        log.info("llm.tool_call", tool=tool_name, args=tool_args)
                        
                        result = await execute_tool(tool_name, tool_args, session)
                        
                        # Add tool interaction to history for context
                        session.history.append({
                            "role": "model",
                            "parts": [{"functionCall": {"name": tool_name, "args": tool_args}}]
                        })
                        session.history.append({
                            "role": "user",
                            "parts": [{"functionResponse": {"name": tool_name, "response": result}}]
                        })

            if buffer.strip():
                await session.tts_queue.put(buffer.strip())

            if total_text.strip():
                session.add_model_turn(total_text.strip())

            if turn:
                turn.llm_done_s = time.monotonic()
                turn.llm_input_tokens = total_input_tokens
                turn.llm_output_tokens = total_output_tokens
                turn.llm_cost_usd = (
                    (total_input_tokens / 1_000_000) * session.settings.gemini_cost_per_1m_input_tokens
                    + (total_output_tokens / 1_000_000) * session.settings.gemini_cost_per_1m_output_tokens
                )

            log.info(
                "llm.turn_complete",
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cost=turn.llm_cost_usd if turn else 0.0,
            )

    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error("llm.unhandled_error", error=str(e), exc_info=True)
    finally:
        await session.tts_queue.put(QUEUE_SENTINEL)
        log.info("llm.done")
