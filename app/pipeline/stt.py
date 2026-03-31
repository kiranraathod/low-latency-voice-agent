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
from deepgram.listen.v1.types.listen_v1speech_started import ListenV1SpeechStarted
from deepgram.listen.v1.types.listen_v1utterance_end import ListenV1UtteranceEnd
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
            utterance_end_ms="1000",
            vad_events="true",
        ) as dg_connection:
            pending_final: dict | None = None
            finalize_complete = asyncio.Event()

            async def _send_transcript_frame(
                kind: TranscriptKind,
                text: str,
                confidence: float,
                start_s: float | None,
                duration_s: float | None,
            ) -> None:
                if not text.strip():
                    return
                frame = TranscriptFrame(
                    type=FrameType.TRANSCRIPT,
                    session_id=session.id,
                    kind=kind,
                    text=text,
                    confidence=confidence,
                    start_s=start_s,
                    duration_s=duration_s,
                )
                try:
                    await session.websocket.send_json(frame.model_dump(mode="json"))
                except Exception:
                    pass

            async def _commit_completed_utterance(candidate: dict, *, source: str) -> None:
                nonlocal pending_final

                text = str(candidate.get("text", "")).strip()
                if not text:
                    return

                dur = max(float(candidate.get("duration_s", 0.0) or 0.0), 0.0)
                event_received_s = float(candidate.get("event_received_s", time.monotonic()))

                turn = session.metrics.current_turn
                if turn is None or turn.stt_final_received_s is not None:
                    turn = session.metrics.start_turn()

                turn.utterance_start_s = event_received_s - dur
                turn.stt_final_received_s = event_received_s
                turn.stt_audio_seconds += dur
                turn.stt_cost_usd += (
                    dur / 60.0
                ) * session.settings.deepgram_cost_per_minute

                pending_final = None
                finalize_complete.set()
                log.info("stt.utterance_complete", source=source, text=text)
                await session.llm_queue.put(text)
            
            # --- 1. Background Receiver Task ---
            async def _receive_from_deepgram():
                nonlocal pending_final
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
                            
                            if text.strip():
                                await _send_transcript_frame(
                                    TranscriptKind.FINAL if speech_final else TranscriptKind.PARTIAL,
                                    text,
                                    confidence,
                                    result.start,
                                    result.duration,
                                )
                                
                            if is_final and text.strip():
                                finalize_complete.clear()
                                pending_final = {
                                    "text": text,
                                    "confidence": confidence,
                                    "start_s": result.start,
                                    "duration_s": result.duration,
                                    "event_received_s": event_received_s,
                                }
                                log.debug("stt.final", text=text, speech_final=speech_final)

                            if speech_final and text.strip():
                                await _commit_completed_utterance(
                                    pending_final
                                    or {
                                        "text": text,
                                        "confidence": confidence,
                                        "start_s": result.start,
                                        "duration_s": result.duration,
                                        "event_received_s": event_received_s,
                                    },
                                    source="speech_final",
                                )
                            continue

                        if isinstance(result, ListenV1UtteranceEnd):
                            if pending_final and pending_final.get("text"):
                                await _send_transcript_frame(
                                    TranscriptKind.FINAL,
                                    str(pending_final["text"]),
                                    float(pending_final.get("confidence", 0.0) or 0.0),
                                    pending_final.get("start_s"),
                                    pending_final.get("duration_s"),
                                )
                                await _commit_completed_utterance(
                                    pending_final,
                                    source="utterance_end",
                                )
                            else:
                                finalize_complete.set()
                                log.debug(
                                    "stt.utterance_end_without_pending",
                                    last_word_end=result.last_word_end,
                                )
                            continue

                        if isinstance(result, ListenV1SpeechStarted):
                            log.debug("stt.speech_started", timestamp=result.timestamp)
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
                try:
                    await dg_connection.send_finalize()
                    log.debug("stt.finalize_sent")
                    if pending_final:
                        try:
                            await asyncio.wait_for(finalize_complete.wait(), timeout=1.5)
                        except asyncio.TimeoutError:
                            log.warning("stt.finalize_timeout", pending_text=pending_final.get("text"))
                            await _send_transcript_frame(
                                TranscriptKind.FINAL,
                                str(pending_final.get("text", "")),
                                float(pending_final.get("confidence", 0.0) or 0.0),
                                pending_final.get("start_s"),
                                pending_final.get("duration_s"),
                            )
                            await _commit_completed_utterance(
                                pending_final,
                                source="finalize_timeout",
                            )
                except Exception as exc:
                    log.warning("stt.finalize_error", error=str(exc))

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
