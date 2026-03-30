"""
app/pipeline/tools.py — Tool definitions and executor for Gemini tool-calling.

Tools available:
  play_audio — plays a pre-bundled notification audio clip to the client.

The tool definition follows the google-genai SDK's FunctionDeclaration format.
The executor sends the audio clip as binary frames over the WebSocket.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
from google.genai import types as genai_types

from app.models import AudioReadyFrame, FrameType, ToolCallFrame
from app.session import VoiceSession

logger = structlog.get_logger(__name__)

# Path to the bundled audio clip (created in Phase 6 / assets setup)
NOTIFICATION_CLIP_PATH = Path("assets/notification.mp3")

# ── Tool Definitions (Gemini FunctionDeclaration format) ──────────────────────

PLAY_AUDIO_DECLARATION = genai_types.FunctionDeclaration(
    name="play_audio",
    description=(
        "Play a short audio notification or alert sound to the user. "
        "Use this when the user asks to hear a sound, chime, or notification."
    ),
    parameters=genai_types.Schema(
        type=genai_types.Type.OBJECT,
        properties={
            "clip_name": genai_types.Schema(
                type=genai_types.Type.STRING,
                description=(
                    "The name of the audio clip to play. "
                    "Currently only 'notification' is supported."
                ),
                enum=["notification"],
            ),
        },
        required=["clip_name"],
    ),
)

# All tools exposed to Gemini
ALL_TOOLS = [genai_types.Tool(function_declarations=[PLAY_AUDIO_DECLARATION])]


# ── Tool Executor ─────────────────────────────────────────────────────────────


async def execute_tool(
    tool_name: str,
    tool_args: dict,
    session: VoiceSession,
) -> dict:
    """
    Dispatch a tool call from Gemini and return the result dict.

    The result is passed back to Gemini so it can incorporate it into its
    continued response generation.
    """
    log = logger.bind(session_id=str(session.id), tool=tool_name, args=tool_args)
    log.info("tool.executing")

    match tool_name:
        case "play_audio":
            return await _play_audio(tool_args, session, log)
        case _:
            log.warning("tool.unknown")
            return {"error": f"Unknown tool: {tool_name}"}


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
