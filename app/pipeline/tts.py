"""
app/pipeline/tts.py — Deepgram Aura streaming TTS processor.

Architecture:
  tts_processor(session) reads sentence chunks from session.tts_queue
  → uses Deepgram Aura streaming WebSockets
  → streams PCM audio chunks to the client WebSocket
  → logs time-to-first-audio, total TTS time, character count, and cost
  → respects barge_in_event to clear in-flight synthesis
"""
from __future__ import annotations

import asyncio
import time

import structlog
from deepgram import AsyncDeepgramClient
from deepgram.speak.v1 import (
    SpeakV1Cleared,
    SpeakV1Flushed,
    SpeakV1Metadata,
    SpeakV1Text,
    SpeakV1Warning,
)
from starlette.websockets import WebSocketState

from app.models import AudioReadyFrame, FrameType
from app.session import QUEUE_SENTINEL, VoiceSession

logger = structlog.get_logger(__name__)

PCM_MIME_TYPE = "audio/pcm;rate=24000;channels=1;encoding=s16le"
_BARGE_IN_MARKER = object()


async def tts_processor(session: VoiceSession) -> None:
    """Deepgram Aura TTS processor — reads text from tts_queue, synthesizes, and streams audio."""
    log = logger.bind(session_id=str(session.id), task="tts")
    log.info("tts.start")

    client = AsyncDeepgramClient(
        api_key=str(session.settings.deepgram_api_key),
        session_id=str(session.id),
    )

    try:
        async with client.speak.v1.connect(
            model=session.settings.deepgram_tts_model,
            encoding="linear16",
            sample_rate=session.settings.deepgram_tts_sample_rate,
            request_options={
                "additional_query_parameters": {
                    "container": "none",
                }
            },
        ) as dg_connection:
            try:
                while True:
                    if session.barge_in_event.is_set():
                        await _clear_active_synthesis(dg_connection, session, log)

                    sentence = await session.tts_queue.get()

                    if sentence is QUEUE_SENTINEL:
                        log.info("tts.sentinel_received")
                        break

                    if not sentence or not sentence.strip():
                        continue

                    turn = session.metrics.current_turn
                    if turn:
                        turn.tts_characters += len(sentence)
                        turn.tts_cost_usd += (
                            len(sentence) / 1000.0
                        ) * session.settings.deepgram_tts_cost_per_1k_chars

                    await _send_audio_ready(session, PCM_MIME_TYPE)
                    await dg_connection.send_text(SpeakV1Text(type="Speak", text=sentence))
                    await dg_connection.send_flush()
                    completed = await _stream_sentence_audio(dg_connection, session, log)

                    if completed and turn:
                        turn.tts_done_s = time.monotonic()
            finally:
                try:
                    await dg_connection.send_close()
                except Exception as exc:
                    log.debug("tts.close_ignored", error=str(exc))

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("tts.fatal_error", error=str(exc), exc_info=True)
    finally:
        log.info("tts.done")


async def _stream_sentence_audio(
    dg_connection,
    session: VoiceSession,
    log: structlog.BoundLogger,  # type: ignore[type-arg]
) -> bool:
    """Forward one sentence worth of Deepgram audio until Flushed or Cleared."""
    first_chunk = True
    clearing = False

    while True:
        message = await _recv_with_barge_poll(dg_connection, session)

        if message is _BARGE_IN_MARKER:
            if not clearing:
                clearing = True
                log.info("tts.barge_in_mid_stream")
                await dg_connection.send_clear()
            continue

        if isinstance(message, bytes):
            if clearing or not message:
                continue

            turn = session.metrics.current_turn
            if first_chunk and turn and turn.tts_first_chunk_s is None:
                turn.tts_first_chunk_s = time.monotonic()
                first_chunk = False

            if not await _send_audio_chunk(session, message, log):
                return False
            continue

        if isinstance(message, SpeakV1Metadata):
            log.debug("tts.metadata", metadata=message.dict())
            continue

        if isinstance(message, SpeakV1Warning):
            log.warning("tts.warning", warning=message.dict())
            continue

        if isinstance(message, SpeakV1Cleared):
            session.barge_in_event.clear()
            log.info("tts.cleared")
            return False

        if isinstance(message, SpeakV1Flushed):
            if clearing:
                session.barge_in_event.clear()
                log.info("tts.flushed_after_clear")
                return False
            log.debug("tts.flushed")
            return True


async def _clear_active_synthesis(
    dg_connection,
    session: VoiceSession,
    log: structlog.BoundLogger,  # type: ignore[type-arg]
) -> None:
    """Clear any in-flight Aura synthesis before the next sentence starts."""
    log.info("tts.barge_in_detected")
    await dg_connection.send_clear()

    while True:
        message = await _recv_with_barge_poll(dg_connection, session, poll_barge_in=False)

        if isinstance(message, bytes):
            continue

        if isinstance(message, SpeakV1Cleared):
            session.barge_in_event.clear()
            log.info("tts.cleared")
            return

        if isinstance(message, SpeakV1Flushed):
            session.barge_in_event.clear()
            log.info("tts.flushed_after_clear")
            return

        if isinstance(message, SpeakV1Warning):
            log.warning("tts.warning", warning=message.dict())
            continue

        if isinstance(message, SpeakV1Metadata):
            log.debug("tts.metadata", metadata=message.dict())
            continue


async def _recv_with_barge_poll(
    dg_connection,
    session: VoiceSession,
    *,
    poll_barge_in: bool = True,
):
    """Poll the Deepgram socket while still reacting promptly to barge-in."""
    while True:
        if poll_barge_in and session.barge_in_event.is_set():
            return _BARGE_IN_MARKER
        try:
            return await asyncio.wait_for(dg_connection.recv(), timeout=0.1)
        except asyncio.TimeoutError:
            continue


async def _send_audio_ready(session: VoiceSession, mime_type: str) -> None:
    """Announce the format of the next binary audio stream to the client."""
    try:
        if session.websocket.client_state == WebSocketState.CONNECTED:
            await session.websocket.send_json(
                AudioReadyFrame(
                    type=FrameType.AUDIO_READY,
                    session_id=session.id,
                    chunk_index=0,
                    mime_type=mime_type,
                ).model_dump(mode="json")
            )
    except Exception:
        pass


async def _send_audio_chunk(
    session: VoiceSession,
    audio_data: bytes,
    log: structlog.BoundLogger,  # type: ignore[type-arg]
) -> bool:
    """Send one binary audio chunk to the browser, returning False if the socket is closed."""
    try:
        if session.websocket.client_state == WebSocketState.CONNECTED:
            await session.websocket.send_bytes(audio_data)
            return True
    except Exception as exc:
        log.warning("tts.send_bytes_error", error=str(exc))
    return False
