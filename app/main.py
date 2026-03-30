"""
app/main.py — FastAPI application entry-point.

Endpoints:
  GET  /health           → liveness check
  GET  /metrics          → global + per-session metrics (JSON)
  WS   /ws/talk          → bidirectional voice streaming

WebSocket session lifecycle:
  1. Client connects → VoiceSession created, 4 tasks spawned via TaskGroup
  2. audio_receiver reads raw binary audio frames, pushes to stt_queue
  3. stt_processor  (Phase 2)  reads stt_queue → Deepgram → emits to llm_queue
  4. llm_processor  (Phase 3)  reads llm_queue → Gemini  → emits to tts_queue
  5. tts_processor  (Phase 4)  reads tts_queue → ElevenLabs → sends audio back
  6. Any task raises CancelledError → TaskGroup cancels the rest → teardown

In Phase 1 the stt/llm/tts processors are stubs that echo gracefully.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from app.config import get_settings
from app.logging_config import setup_logging
from app.metrics import metrics_registry
from app.models import (
    ControlAction,
    ControlFrame,
    ErrorFrame,
    FrameType,
    StatusFrame,
)
from app.pipeline.llm import llm_processor
from app.pipeline.stt import stt_processor
from app.session import QUEUE_SENTINEL, VoiceSession, session_manager

logger = structlog.get_logger(__name__)

# ── Application Lifespan ──────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    log = structlog.get_logger("startup")
    log.info(
        "voice_agent.starting",
        host=settings.host,
        port=settings.port,
        gemini_model=settings.gemini_model,
        deepgram_model=settings.deepgram_model,
        elevenlabs_model=settings.elevenlabs_model_id,
    )
    yield
    log.info("voice_agent.shutting_down")


# ── FastAPI App ───────────────────────────────────────────────────────────────

settings = get_settings()

app = FastAPI(
    title="Voice AI Agent",
    description="Real-time voice AI agent with ≤ 2s end-to-end latency",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve browser client from /client directory
try:
    app.mount("/client", StaticFiles(directory="client", html=True), name="client")
except RuntimeError:
    pass  # client/ dir may not exist yet in early phases


# ── REST Endpoints ────────────────────────────────────────────────────────────


@app.get("/health", tags=["observability"])
async def health() -> dict:
    """Liveness check — always returns 200 if the server is up."""
    return {
        "status": "ok",
        "active_sessions": session_manager.active_count,
        "timestamp_ms": int(time.time() * 1000),
    }


@app.get("/metrics", tags=["observability"])
async def get_metrics() -> dict:
    """
    Global and per-session latency / cost metrics.

    Returns:
      - active_sessions: number of live WebSocket connections
      - avg_end_to_end_ms: rolling average E2E latency across all turns
      - p95_end_to_end_ms: 95th percentile E2E latency
      - total_cost_usd: cumulative cost across all sessions
      - active_session_details: per-session breakdown
    """
    return metrics_registry.global_summary()


# ── WebSocket Handler ─────────────────────────────────────────────────────────


@app.websocket("/ws/talk")
async def ws_talk(websocket: WebSocket) -> None:
    """
    Main WebSocket endpoint for the voice pipeline.

    Frame protocol:
      binary frames → raw audio (PCM / WebM / Opus from browser)
      text frames   → JSON control frames (ControlFrame model)

    The server uses asyncio.TaskGroup so that if ANY of the 4 pipeline tasks
    raises an exception (or is cancelled), all others are cancelled immediately,
    preventing resource leaks.
    """
    await websocket.accept()
    session = session_manager.create(websocket)
    log = logger.bind(session_id=str(session.id))
    log.info("ws.connected")

    try:
        # Send session_start status to client
        await _send_json(
            websocket,
            StatusFrame(
                type=FrameType.STATUS,
                session_id=session.id,
                event="session_start",
                detail=f"Session {session.id} established",
            ).model_dump(mode="json"),
        )

        async with asyncio.TaskGroup() as tg:
            # Task 1: receive audio/control frames from client
            t_recv = tg.create_task(
                _audio_receiver(session, websocket),
                name=f"audio_receiver:{session.id}",
            )
            session.register_task(t_recv)

            # Tasks 2–4: pipeline processors (stubs for Phase 1)
            t_stt = tg.create_task(
                stt_processor(session),
                name=f"stt:{session.id}",
            )
            session.register_task(t_stt)

            t_llm = tg.create_task(
                llm_processor(session),
                name=f"llm:{session.id}",
            )
            session.register_task(t_llm)

            t_tts = tg.create_task(
                _tts_processor_stub(session),
                name=f"tts_stub:{session.id}",
            )
            session.register_task(t_tts)

    except* WebSocketDisconnect:
        log.info("ws.client_disconnected")
    except* asyncio.CancelledError:
        log.info("ws.cancelled")
    except* Exception as eg:
        for exc in eg.exceptions:
            log.error("ws.error", exc_info=exc)
    finally:
        await session_manager.destroy(session.id)
        log.info("ws.closed")


# ── Pipeline Task: Audio Receiver ─────────────────────────────────────────────


async def _audio_receiver(session: VoiceSession, websocket: WebSocket) -> None:
    """
    Reads frames from the WebSocket and routes them:
      - binary frames → stt_queue (raw audio bytes)
      - text frames   → parsed as ControlFrame, handled inline
    """
    log = logger.bind(session_id=str(session.id), task="audio_receiver")
    log.info("audio_receiver.start")

    try:
        while True:
            # Respect turn timeout: if client goes silent for TURN_TIMEOUT_S, abort
            try:
                raw = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=session.settings.turn_timeout_s,
                )
            except asyncio.TimeoutError:
                log.warning("audio_receiver.turn_timeout")
                await _send_json(
                    websocket,
                    ErrorFrame(
                        type=FrameType.ERROR,
                        session_id=session.id,
                        code="TURN_TIMEOUT",
                        message=(
                            f"No audio received for "
                            f"{session.settings.turn_timeout_s:.0f}s"
                        ),
                        recoverable=True,
                    ).model_dump(mode="json"),
                )
                continue

            if raw.get("type") == "websocket.disconnect":
                log.info("audio_receiver.disconnect_signal")
                break

            if "bytes" in raw and raw["bytes"] is not None:
                # Raw audio frame — push to STT queue
                chunk: bytes = raw["bytes"]
                if chunk:
                    try:
                        session.stt_queue.put_nowait(chunk)
                    except asyncio.QueueFull:
                        log.warning(
                            "audio_receiver.stt_queue_full",
                            dropped_bytes=len(chunk),
                        )

            elif "text" in raw and raw["text"] is not None:
                # JSON control frame
                await _handle_control_frame(session, websocket, raw["text"], log)

    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        # Signal downstream tasks to shut down
        await session.stt_queue.put(QUEUE_SENTINEL)
        log.info("audio_receiver.done")


async def _handle_control_frame(
    session: VoiceSession,
    websocket: WebSocket,
    text: str,
    log: structlog.BoundLogger,  # type: ignore[type-arg]
) -> None:
    """Parse and dispatch a JSON control frame from the client."""
    try:
        data = json.loads(text)
        frame = ControlFrame.model_validate(data)
    except Exception as exc:
        log.warning("control_frame.parse_error", error=str(exc), raw=text[:200])
        return

    match frame.action:
        case ControlAction.START:
            log.info("control.start")
            # Start a new turn in metrics
            session.metrics.start_turn()

        case ControlAction.STOP:
            log.info("control.stop")
            # Graceful stop — send sentinel, tasks will drain and terminate
            await session.stt_queue.put(QUEUE_SENTINEL)

        case ControlAction.BARGE_IN:
            log.info("control.barge_in")
            session.barge_in_event.set()

        case _:
            log.warning("control_frame.unknown_action", action=frame.action)


# ── Pipeline Stubs (Phase 1 — replaced in Phases 2–4) ────────────────────────


async def _stt_processor_stub(session: VoiceSession) -> None:
    """
    Phase 1 stub: drain stt_queue and log received audio bytes.
    Replaced by the real Deepgram client in Phase 2.
    """
    log = logger.bind(session_id=str(session.id), task="stt_stub")
    log.info("stt_stub.start")
    total_bytes = 0
    try:
        while True:
            chunk = await session.stt_queue.get()
            if chunk is QUEUE_SENTINEL:
                log.info("stt_stub.sentinel_received")
                break
            total_bytes += len(chunk)
            log.debug("stt_stub.received_chunk", bytes=len(chunk), total=total_bytes)
    except asyncio.CancelledError:
        pass
    finally:
        await session.llm_queue.put(QUEUE_SENTINEL)
        log.info("stt_stub.done", total_bytes=total_bytes)


async def _llm_processor_stub(session: VoiceSession) -> None:
    """
    Phase 1 stub: drain llm_queue.
    Replaced by the real Gemini client in Phase 3.
    """
    log = logger.bind(session_id=str(session.id), task="llm_stub")
    log.info("llm_stub.start")
    try:
        while True:
            transcript = await session.llm_queue.get()
            if transcript is QUEUE_SENTINEL:
                log.info("llm_stub.sentinel_received")
                break
            log.info("llm_stub.got_transcript", text=transcript)
    except asyncio.CancelledError:
        pass
    finally:
        await session.tts_queue.put(QUEUE_SENTINEL)
        log.info("llm_stub.done")


async def _tts_processor_stub(session: VoiceSession) -> None:
    """
    Phase 1 stub: drain tts_queue.
    Replaced by the real ElevenLabs client in Phase 4.
    """
    log = logger.bind(session_id=str(session.id), task="tts_stub")
    log.info("tts_stub.start")
    try:
        while True:
            sentence = await session.tts_queue.get()
            if sentence is QUEUE_SENTINEL:
                log.info("tts_stub.sentinel_received")
                break
            log.info("tts_stub.got_sentence", text=sentence)
    except asyncio.CancelledError:
        pass
    finally:
        log.info("tts_stub.done")


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _send_json(websocket: WebSocket, data: dict) -> None:
    """Send a JSON frame, silently dropping if the connection is closed."""
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(data)
    except Exception:
        pass


# ── Dev entry-point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_config=None,  # Suppress uvicorn's default log config; we use structlog
    )
