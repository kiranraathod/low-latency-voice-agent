"""
app/pipeline/llm.py — OpenAI compatible streaming LLM processor.

Architecture:
  llm_processor(session) reads final transcripts from session.llm_queue
  → calls OpenAI standard API (streaming, with tool-calling)
  → buffers tokens until sentence boundary
  → pushes sentence chunks to session.tts_queue
  → handles tool_call responses inline (calls execute_tool)
  → logs TTFT, total generation time, input/output token counts
"""
from __future__ import annotations

import asyncio
import json
import re
import time

import structlog
from openai import AsyncOpenAI
from starlette.websockets import WebSocketState

from app.models import FrameType, LLMChunkFrame
from app.pipeline.prompts import SYSTEM_PROMPT
from app.pipeline.tools import ALL_TOOLS, execute_tool
from app.session import QUEUE_SENTINEL, VoiceSession

logger = structlog.get_logger(__name__)


async def llm_processor(session: VoiceSession) -> None:
    """Read STT final transcripts, call OpenAI-compatible API, and stream chunks to TTS."""
    log = logger.bind(session_id=str(session.id), task="llm")
    log.info("llm.start")

    # Initialize client (uses API key and optional base_url from settings)
    client = AsyncOpenAI(
        api_key=session.settings.openai_api_key,
        base_url=session.settings.openai_base_url,
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

            # Build messages list combining the system prompt + dynamic session history
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + session.history

            try:
                response_stream = await client.chat.completions.create(
                    model=session.settings.openai_model,
                    messages=messages,
                    temperature=0.3,
                    tools=ALL_TOOLS,
                    stream=True,
                    stream_options={"include_usage": True}
                )
            except Exception as e:
                log.error("llm.generate_error", error=str(e), exc_info=True)
                await session.tts_queue.put("I'm sorry, I'm having trouble thinking right now.")
                continue

            buffer = ""
            ttft_logged = False
            total_text = ""
            total_input_tokens = 0
            total_output_tokens = 0
            
            tool_calls = {}

            async for chunk in response_stream:
                if not ttft_logged and turn and chunk.choices and chunk.choices[0].delta.content:
                    turn.llm_first_token_s = time.monotonic()
                    ttft_logged = True
                    log.info("llm.ttft", ms=(turn.llm_first_token_s - turn.llm_start_s) * 1000)

                # Track usage statistics (only available if include_usage=True, commonly on the last chunk)
                if chunk.usage:
                    total_input_tokens = chunk.usage.prompt_tokens
                    total_output_tokens = chunk.usage.completion_tokens

                if chunk.choices:
                    delta = chunk.choices[0].delta
                    
                    if delta.content:
                        text_part = delta.content
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
                                # Send LLM text to client for UI display
                                await _send_llm_chunk(session, sentence, is_sentence_end=True)

                    # Accumulate streaming tool calls
                    if delta.tool_calls:
                        for tool_call in delta.tool_calls:
                            idx = tool_call.index
                            if idx not in tool_calls:
                                tool_calls[idx] = {
                                    "id": tool_call.id,
                                    "type": "function",
                                    "function": {
                                        # Handle chunk where properties might be None
                                        "name": tool_call.function.name or "",
                                        "arguments": tool_call.function.arguments or ""
                                    }
                                }
                            else:
                                if tool_call.function.name:
                                    tool_calls[idx]["function"]["name"] += tool_call.function.name
                                if tool_call.function.arguments:
                                    tool_calls[idx]["function"]["arguments"] += tool_call.function.arguments

            if buffer.strip():
                remaining = buffer.strip()
                await session.tts_queue.put(remaining)
                await _send_llm_chunk(session, remaining, is_sentence_end=True)

            if total_text.strip():
                session.add_model_turn(total_text.strip())

            # Handle fully accumulated tool calls after stream ends
            if tool_calls:
                # Add tool_calls request to history
                assistant_tool_msg = {
                    "role": "assistant",
                    "content": total_text if total_text else None,
                    "tool_calls": list(tool_calls.values())
                }
                session.history.append(assistant_tool_msg)
                
                # Execute tools
                for tc in tool_calls.values():
                    tool_name = tc["function"]["name"]
                    try:
                        tool_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        tool_args = {}
                        
                    log.info("llm.tool_call", tool=tool_name, args=tool_args)
                    result = await execute_tool(tool_name, tool_args, session)
                    
                    # Store tool response
                    session.history.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tool_name,
                        "content": json.dumps(result)
                    })

            if turn:
                turn.llm_done_s = time.monotonic()
                turn.llm_input_tokens += total_input_tokens
                turn.llm_output_tokens += total_output_tokens
                turn.llm_cost_usd += (
                    (total_input_tokens / 1_000_000) * session.settings.openai_cost_per_1m_input_tokens
                    + (total_output_tokens / 1_000_000) * session.settings.openai_cost_per_1m_output_tokens
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


async def _send_llm_chunk(session: VoiceSession, text: str, is_sentence_end: bool = False) -> None:
    """Send an LLM text chunk to the client WebSocket for UI display."""
    try:
        if session.websocket.client_state == WebSocketState.CONNECTED:
            frame = LLMChunkFrame(
                type=FrameType.LLM_CHUNK,
                session_id=session.id,
                text=text,
                is_sentence_end=is_sentence_end,
            )
            await session.websocket.send_json(frame.model_dump(mode="json"))
    except Exception:
        pass  # Silently drop if connection is closed
