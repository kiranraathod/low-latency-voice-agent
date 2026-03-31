"""
app/pipeline/stt.py — Deepgram Nova-2 streaming STT processor.

Architecture:
  stt_processor(session) reads bytes from session.stt_queue
  → opens a Deepgram WebSocket connection
  → forwards audio chunks
  → receives transcript events (partial + final)
  → on final transcript, pushes to session.llm_queue
  → logs per-stage timing to session.metrics
"""
from __future__ import annotations

import asyncio
import time

import structlog
from app.models import FrameType, TranscriptFrame, TranscriptKind
from deepgram.client import AsyncDeepgramClient
from deepgram.listen.v1.types.listen_v1results import ListenV1Results
from app.session import QUEUE_SENTINEL, VoiceSession

logger = structlog.get_logger(__name__)

async def stt_processor(session: VoiceSession) -> None:
    """Implement Deepgram STT Phase 2 logic."""
    log = logger.bind(session_id=str(session.id), task="stt_processor")
    log.info("stt.start")

    client = AsyncDeepgramClient(api_key=session.settings.deepgram_api_key.get_secret_value() if hasattr(session.settings.deepgram_api_key, "get_secret_value") else str(session.settings.deepgram_api_key))
    endpointing_ms = int(session.settings.deepgram_endpointing_ms)
    
    try:
        async with client.listen.v1.connect(
            model=session.settings.deepgram_model,
            encoding="linear16",
            sample_rate="16000",
            channels="1",
            endpointing=str(endpointing_ms),
            interim_results="true", # Must be lowercase string to avoid URL encode 'True'
            smart_format="true",
        ) as dg_connection:
            
            # --- 1. Background Receiver Task ---
            async def _receive_from_deepgram():
                try:
                    async for result in dg_connection:
                        if isinstance(result, ListenV1Results):
                            is_final = result.is_final or False
                            speech_final = result.speech_final or False
                            event_received_s = time.monotonic()
                            
                            text = ""
                            confidence = 0.0
                            if result.channel and result.channel.alternatives:
                                alt = result.channel.alternatives[0]
                                text = alt.transcript
                                confidence = alt.confidence
                            
                            # Always send the frame back to client if we have text or it's final
                            if text.strip() or is_final:
                                kind = TranscriptKind.FINAL if is_final else TranscriptKind.PARTIAL
                                
                                frame = TranscriptFrame(
                                    type=FrameType.TRANSCRIPT,
                                    session_id=session.id,
                                    kind=kind,
                                    text=text,
                                    confidence=confidence,
                                    start_s=result.start,
                                    duration_s=result.duration,
                                )
                                
                                # Use silent error dropping to mimic _send_json in main.py
                                try:
                                    await session.websocket.send_json(frame.model_dump(mode="json"))
                                except Exception:
                                    pass
                                
                            if is_final and text.strip():
                                turn = session.metrics.current_turn
                                if turn:
                                    dur = getattr(result, "duration", 0.0)
                                    turn.stt_audio_seconds += dur
                                    turn.stt_cost_usd += (dur / 60.0) * session.settings.deepgram_cost_per_minute

                                log.debug("stt.final", text=text)

                            # Only send to LLM on speech_final (end of full utterance)
                            # This prevents multiple LLM calls per spoken phrase
                            if speech_final and text.strip():
                                turn = session.metrics.current_turn
                                if turn and turn.stt_final_received_s is None:
                                    turn.stt_final_received_s = event_received_s
                                log.info("stt.speech_final", text=text)
                                await session.llm_queue.put(text)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.error("stt.receive_error", error=str(e))

            receiver_task = asyncio.create_task(_receive_from_deepgram())
            
            # --- 2. Foreground Sender Loop ---
            total_bytes = 0
            try:
                while True:
                    chunk = await session.stt_queue.get()
                    if chunk is QUEUE_SENTINEL:
                        log.info("stt.sentinel_received")
                        break
                        
                    if total_bytes == 0 and chunk:
                        log.debug("stt.first_audio")
                        
                    await dg_connection.send_media(chunk)
                    total_bytes += len(chunk)
            except asyncio.CancelledError:
                pass
            finally:
                # Close the Deepgram connection cleanly
                await dg_connection.send_close_stream()
                receiver_task.cancel()
                try:
                    await receiver_task
                except asyncio.CancelledError:
                    pass

    except asyncio.CancelledError:
        log.info("stt.cancelled")
    except Exception as e:
        log.error("stt.fatal_error", error=str(e), exc_info=True)
    finally:
        await session.llm_queue.put(QUEUE_SENTINEL)
        log.info("stt.done")
