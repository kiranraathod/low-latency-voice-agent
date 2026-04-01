"""
app/pipeline/tools.py — Tool definitions and executor for OpenAI-compatible tool-calling.

Tools available:
  play_audio — plays a pre-bundled notification audio clip to the client.

The tool definition follows the google-genai SDK's FunctionDeclaration format.
The executor sends the audio clip as binary frames over the WebSocket.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import structlog
    # (Removed google.genai import)

from app.models import AudioReadyFrame, FrameType, ToolCallFrame
from app.session import VoiceSession

logger = structlog.get_logger(__name__)

# Path to the bundled audio clip (created in Phase 6 / assets setup)
NOTIFICATION_CLIP_PATH = Path("assets/notification.mp3")

# ── Tool Definitions (OpenAI Function Calling format) ──────────────────────

PLAY_AUDIO_DECLARATION = {
    "type": "function",
    "function": {
        "name": "play_audio",
        "description": (
            "Play a short audio notification or alert sound to the user. "
            "Use this when the user asks to hear a sound, chime, or notification."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "clip_name": {
                    "type": "string",
                    "description": (
                        "The name of the audio clip to play. "
                        "Currently only 'notification' is supported."
                    ),
                    "enum": ["notification"],
                },
            },
            "required": ["clip_name"],
        },
    }
}

# All tools exposed to OpenAI
ALL_TOOLS = [PLAY_AUDIO_DECLARATION]


# ── Tool Executor ─────────────────────────────────────────────────────────────


async def execute_tool(
    tool_name: str,
    tool_args: dict,
    session: VoiceSession,
) -> dict:
    """
    Dispatch a tool call from the LLM and return the result dict.

    The result is passed back to the LLM so it can incorporate it into its
    continued response generation.
    """
    log = logger.bind(session_id=str(session.id), tool=tool_name, args=tool_args)
    log.info("tool.executing")
    started_s = time.monotonic()

    try:
        match tool_name:
            case "play_audio":
                result = await _play_audio(tool_args, session, log)
            case _:
                log.warning("tool.unknown")
                result = {"error": f"Unknown tool: {tool_name}"}
    finally:
        elapsed_ms = max(0.0, (time.monotonic() - started_s) * 1000)
        turn = session.metrics.current_turn
        if turn:
            turn.tool_calls += 1
            turn.tool_total_ms_accum += elapsed_ms
        log.info("tool.completed", duration_ms=round(elapsed_ms, 2))

    return result


async def _play_audio(
    args: dict,
    session: VoiceSession,
    log: structlog.BoundLogger,  # type: ignore[type-arg]
) -> dict:
    """
    Stream the notification.mp3 clip to the client via WebSocket.

    Protocol:
      1. Send ToolCallFrame (JSON) to notify client a tool is firing
      2. Send AudioReadyFrame (JSON) to signal incoming binary audio
      3. Send binary audio data in chunks
    """
    clip_name = args.get("clip_name", "notification")
    ws = session.websocket

    # Notify client about tool invocation
    try:
        await ws.send_json(
            ToolCallFrame(
                type=FrameType.TOOL_CALL,
                session_id=session.id,
                tool_name="play_audio",
                tool_args=args,
            ).model_dump(mode="json")
        )
    except Exception as exc:
        log.error("tool.play_audio.send_frame_error", error=str(exc))
        return {"error": "Failed to send tool call frame"}

    # Load and stream the audio clip
    clip_path = NOTIFICATION_CLIP_PATH
    if not clip_path.exists():
        log.warning("tool.play_audio.clip_not_found", path=str(clip_path))
        return {
            "success": False,
            "error": f"Clip '{clip_name}' not found at {clip_path}",
        }

    try:
        audio_data = clip_path.read_bytes()
        chunk_size = 8192  # 8KB chunks
        turn = session.metrics.current_turn
        if turn:
            turn.tool_audio_bytes += len(audio_data)

        # Signal that audio data follows
        await ws.send_json(
            AudioReadyFrame(
                type=FrameType.AUDIO_READY,
                session_id=session.id,
                chunk_index=0,
                mime_type="audio/mpeg",
            ).model_dump(mode="json")
        )

        # Stream audio in chunks
        for i in range(0, len(audio_data), chunk_size):
            chunk = audio_data[i : i + chunk_size]
            await ws.send_bytes(chunk)
            # Yield control to allow other tasks to run
            await asyncio.sleep(0)

        log.info(
            "tool.play_audio.sent",
            clip=clip_name,
            bytes=len(audio_data),
        )
        return {"success": True, "clip": clip_name, "bytes_sent": len(audio_data)}

    except Exception as exc:
        log.error("tool.play_audio.error", error=str(exc))
        return {"success": False, "error": str(exc)}
