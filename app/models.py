"""
app/models.py — Pydantic models for WebSocket message frames.

All JSON control frames exchanged over /ws/talk conform to these models.
Binary frames (raw audio) bypass these models and are handled directly.

Frame flow:
  client → server:  AudioFrame (binary), ControlFrame (json)
  server → client:  TranscriptFrame, LLMChunkFrame, AudioReadyFrame,
                    ToolCallFrame, ErrorFrame, MetricsFrame, StatusFrame
"""
from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ── Enumerations ─────────────────────────────────────────────────────────────


class FrameType(str, Enum):
    """Discriminator field for all JSON control frames."""

    # Client → Server
    CONTROL = "control"

    # Server → Client
    TRANSCRIPT = "transcript"       # Partial or final STT result
    LLM_CHUNK = "llm_chunk"         # Streaming LLM text token(s)
    AUDIO_READY = "audio_ready"     # TTS audio chunk is following (or embedded)
    TOOL_CALL = "tool_call"         # LLM invoked a tool
    STATUS = "status"               # Session lifecycle events
    METRICS = "metrics"             # Per-turn latency + cost snapshot
    ERROR = "error"                 # Non-fatal error notification


class ControlAction(str, Enum):
    """Actions the client can send via ControlFrame."""

    START = "start"     # Client is ready to begin streaming audio
    STOP = "stop"       # Client signals end of audio stream (graceful)
    BARGE_IN = "barge_in"  # Client wants to interrupt current TTS playback


class TranscriptKind(str, Enum):
    PARTIAL = "partial"
    FINAL = "final"


# ── Base Frame ────────────────────────────────────────────────────────────────


class BaseFrame(BaseModel):
    """All frames share a type discriminator and session id."""

    type: FrameType
    session_id: UUID | None = Field(
        default=None,
        description="Populated by server on all outgoing frames",
    )


# ── Client → Server Frames ────────────────────────────────────────────────────


class ControlFrame(BaseFrame):
    """JSON control message sent from client to server."""

    type: FrameType = FrameType.CONTROL
    action: ControlAction
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Server → Client Frames ────────────────────────────────────────────────────


class TranscriptFrame(BaseFrame):
    """Partial or final speech-to-text result."""

    type: FrameType = FrameType.TRANSCRIPT
    kind: TranscriptKind
    text: str
    confidence: float | None = None
    # Timestamp (seconds from utterance start) reported by Deepgram
    start_s: float | None = None
    duration_s: float | None = None


class LLMChunkFrame(BaseFrame):
    """Incremental LLM token stream chunk."""

    type: FrameType = FrameType.LLM_CHUNK
    text: str
    is_sentence_end: bool = False  # True when this chunk ends a sentence boundary


class AudioReadyFrame(BaseFrame):
    """Signals that an audio chunk (binary) follows on the same connection."""

    type: FrameType = FrameType.AUDIO_READY
    chunk_index: int = 0
    # mime_type e.g. "audio/mpeg" or "audio/pcm"
    mime_type: str = "audio/mpeg"


class ToolCallFrame(BaseFrame):
    """Notifies the client that the LLM invoked a tool."""

    type: FrameType = FrameType.TOOL_CALL
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: dict[str, Any] | None = None


class StatusFrame(BaseFrame):
    """Lifecycle status events."""

    type: FrameType = FrameType.STATUS
    event: str  # e.g. "session_start", "session_end", "stt_connected"
    detail: str = ""


class StageTiming(BaseModel):
    """Latency breakdown per stage in milliseconds."""

    vad_endpointing_ms: float | None = None
    stt_ms: float | None = None
    llm_ttft_ms: float | None = None      # Time-to-first-token
    llm_total_ms: float | None = None
    tts_ttfa_ms: float | None = None      # Time-to-first-audio-chunk
    tts_total_ms: float | None = None
    end_to_end_ms: float | None = None


class TurnCost(BaseModel):
    """Estimated USD cost for one conversational turn."""

    stt_usd: float = 0.0
    llm_usd: float = 0.0
    tts_usd: float = 0.0

    @property
    def total_usd(self) -> float:
        return self.stt_usd + self.llm_usd + self.tts_usd


class MetricsFrame(BaseFrame):
    """Per-turn latency and cost snapshot sent to client after each response."""

    type: FrameType = FrameType.METRICS
    turn_index: int
    timing: StageTiming
    cost: TurnCost
    # Token / character counts for auditability
    stt_audio_seconds: float | None = None
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    tts_characters: int | None = None


class ErrorFrame(BaseFrame):
    """Non-fatal error notification."""

    type: FrameType = FrameType.ERROR
    code: str
    message: str
    recoverable: bool = True
