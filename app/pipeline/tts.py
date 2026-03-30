"""
app/pipeline/tts.py — Edge TTS streaming processor.

Architecture:
  tts_processor(session) reads sentence chunks from session.tts_queue
  → uses Microsoft Edge TTS (100% free, no API key required)
  → streams MP3 audio chunks to the client WebSocket
  → logs time-to-first-audio, total TTS time, character count
  → respects barge_in_event to cancel mid-stream

Replaces ElevenLabs due to free-tier blocking issues.
Edge TTS provides high-quality, low-latency voices via Microsoft's service.
"""
from __future__ import annotations

import asyncio
import io
import time

import edge_tts
import structlog

from app.session import QUEUE_SENTINEL, VoiceSession

logger = structlog.get_logger(__name__)

# Microsoft Edge TTS voice — natural, clear, fast
EDGE_TTS_VOICE = "en-US-AriaNeural"


async def tts_processor(session: VoiceSession) -> None:
    """Edge TTS streaming processor — reads text from tts_queue, synthesizes, sends audio."""
    log = logger.bind(session_id=str(session.id), task="tts")
    log.info("tts.start")

    total_chars = 0

    try:
        while True:
            if session.barge_in_event.is_set():
                log.info("tts.barge_in_detected")
                break

            sentence = await session.tts_queue.get()

            if sentence is QUEUE_SENTINEL:
                log.info("tts.sentinel_received")
                break

            if not sentence or not sentence.strip():
                continue

            total_chars += len(sentence)

            turn = session.metrics.current_turn
            first_chunk = True

            try:
                communicate = edge_tts.Communicate(sentence, EDGE_TTS_VOICE)

                async for chunk in communicate.stream():
                    if session.barge_in_event.is_set():
                        log.info("tts.barge_in_mid_stream")
                        break

                    if chunk["type"] == "audio":
                        audio_data = chunk["data"]
                        if audio_data:
                            # Record time-to-first-audio
                            if first_chunk and turn:
                                turn.tts_first_chunk_s = time.monotonic()
                                first_chunk = False

                            # Send MP3 audio bytes to client
                            try:
                                await session.websocket.send_bytes(audio_data)
                            except Exception as e:
                                log.warning("tts.send_bytes_error", error=str(e))
                                break

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("tts.synthesis_error", error=str(e), sentence=sentence[:50])

    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error("tts.fatal_error", error=str(e), exc_info=True)
    finally:
        # Record metrics
        turn = session.metrics.current_turn
        if turn:
            turn.tts_characters += total_chars
            # Edge TTS is free — $0 cost
            turn.tts_cost_usd += 0.0
            turn.tts_done_s = time.monotonic()

        log.info("tts.done", characters=total_chars)
