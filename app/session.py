"""
app/session.py — Session manager for WebSocket connections.

Each session owns:
  - 4 asyncio.Task handles (audio_receiver, stt, llm, tts)
  - 3 asyncio.Queue bridges between the tasks
  - Conversation history (last N turns)
  - A SessionMetrics instance

The manager (SessionManager) is the single source of truth for all active
sessions. It is created once at app startup and injected via FastAPI's
dependency system.

Sentinel values:
  QUEUE_SENTINEL = None  →  signals a queue consumer to shut down cleanly.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from fastapi import WebSocket

from app.config import Settings, get_settings
from app.metrics import MetricsRegistry, SessionMetrics, metrics_registry

logger = structlog.get_logger(__name__)

# Sentinel placed on queues to signal shutdown
QUEUE_SENTINEL: None = None


@dataclass
class VoiceSession:
    """All state for one active WebSocket connection."""

    # Required fields (no defaults) MUST come first in a dataclass
    websocket: WebSocket = field(repr=False)

    # Optional fields with defaults
    id: UUID = field(default_factory=uuid.uuid4)
    settings: Settings = field(default_factory=get_settings, repr=False)

    # ── Inter-task queues (init=False — set in __post_init__) ─────────────
    # Audio bytes from client → STT processor
    stt_queue: asyncio.Queue[bytes | None] = field(init=False)
    # Final transcript strings → LLM processor
    llm_queue: asyncio.Queue[str | None] = field(init=False)
    # Sentence text chunks → TTS processor
    tts_queue: asyncio.Queue[str | None] = field(init=False)

    # ── Task handles ──────────────────────────────────────────────────────
    _tasks: list[asyncio.Task] = field(default_factory=list, init=False, repr=False)

    # ── Conversation memory ───────────────────────────────────────────────
    # List of {"role": "user"|"model", "parts": [{"text": "..."}]}
    history: list[dict[str, Any]] = field(default_factory=list, init=False)

    # ── Metrics ───────────────────────────────────────────────────────────
    metrics: SessionMetrics = field(init=False, repr=False)

    # ── Barge-in control ──────────────────────────────────────────────────
    # Set this event to interrupt ongoing TTS playback
    barge_in_event: asyncio.Event = field(
        default_factory=asyncio.Event, init=False, repr=False
    )

    def __post_init__(self) -> None:
        cfg = self.settings
        self.stt_queue = asyncio.Queue(maxsize=cfg.stt_queue_maxsize)
        self.llm_queue = asyncio.Queue(maxsize=cfg.llm_queue_maxsize)
        self.tts_queue = asyncio.Queue(maxsize=cfg.tts_queue_maxsize)
        self.metrics = metrics_registry.register(self.id)

    # ── Conversation history helpers ──────────────────────────────────────

    def add_user_turn(self, text: str) -> None:
        self.history.append({"role": "user", "parts": [{"text": text}]})
        self._trim_history()

    def add_model_turn(self, text: str) -> None:
        self.history.append({"role": "model", "parts": [{"text": text}]})
        self._trim_history()

    def _trim_history(self) -> None:
        """Keep only the last N turn-pairs (user + model = 1 pair)."""
        max_messages = self.settings.gemini_max_history_turns * 2
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]

    # ── Task management ───────────────────────────────────────────────────

    def register_task(self, task: asyncio.Task) -> None:
        self._tasks.append(task)

    async def teardown(self) -> None:
        """Cancel all tasks and drain queues on disconnect."""
        log = logger.bind(session_id=str(self.id))
        log.info("session.teardown.start", task_count=len(self._tasks))

        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Give tasks a moment to handle cancellation
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Drain queues (prevents tasks blocked on queue.get() from hanging)
        for q in (self.stt_queue, self.llm_queue, self.tts_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break

        self.metrics.finish_turn()
        metrics_registry.complete(self.id)
        log.info("session.teardown.done")


class SessionManager:
    """Thread-safe (asyncio-safe) registry of active VoiceSessions."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, VoiceSession] = {}

    def create(self, websocket: WebSocket) -> VoiceSession:
        session = VoiceSession(websocket=websocket)
        self._sessions[session.id] = session
        logger.info(
            "session.created",
            session_id=str(session.id),
            active_count=len(self._sessions),
        )
        return session

    def get(self, session_id: UUID) -> VoiceSession | None:
        return self._sessions.get(session_id)

    async def destroy(self, session_id: UUID) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.teardown()
            logger.info(
                "session.destroyed",
                session_id=str(session_id),
                active_count=len(self._sessions),
            )

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    @property
    def active_sessions(self) -> list[VoiceSession]:
        return list(self._sessions.values())


# Module-level singleton — created once at app startup
session_manager = SessionManager()
