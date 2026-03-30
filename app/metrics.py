"""
app/metrics.py — Per-session and global metrics collector.

Design goals:
- Zero-blocking: all updates are synchronous in-memory operations.
- Thread-safe via asyncio single-thread guarantee (no locks needed).
- Exposed via GET /metrics endpoint.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    pass


# ── Per-Turn Accumulator ─────────────────────────────────────────────────────


@dataclass
class TurnMetrics:
    """Accumulated measurements for one conversational turn."""

    turn_index: int = 0

    # Timing (monotonic seconds; converted to ms on output)
    utterance_start_s: float = field(default_factory=time.monotonic)
    stt_final_received_s: float | None = None
    llm_first_token_s: float | None = None
    llm_done_s: float | None = None
    tts_first_chunk_s: float | None = None
    tts_done_s: float | None = None
    turn_end_s: float | None = None

    # Volume counters
    stt_audio_seconds: float = 0.0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    tts_characters: int = 0

    # Costs (USD)
    stt_cost_usd: float = 0.0
    llm_cost_usd: float = 0.0
    tts_cost_usd: float = 0.0

    # ── Derived ────────────────────────────────────────────────────────────

    @property
    def stt_ms(self) -> float | None:
        if self.stt_final_received_s is None:
            return None
        return (self.stt_final_received_s - self.utterance_start_s) * 1000

    @property
    def llm_ttft_ms(self) -> float | None:
        if self.llm_first_token_s is None or self.stt_final_received_s is None:
            return None
        return (self.llm_first_token_s - self.stt_final_received_s) * 1000

    @property
    def llm_total_ms(self) -> float | None:
        if self.llm_done_s is None or self.stt_final_received_s is None:
            return None
        return (self.llm_done_s - self.stt_final_received_s) * 1000

    @property
    def tts_ttfa_ms(self) -> float | None:
        """Time-to-first-audio from when the first sentence was sent to TTS."""
        if self.tts_first_chunk_s is None or self.llm_first_token_s is None:
            return None
        return (self.tts_first_chunk_s - self.llm_first_token_s) * 1000

    @property
    def end_to_end_ms(self) -> float | None:
        ref = self.tts_first_chunk_s or self.turn_end_s
        if ref is None:
            return None
        return (ref - self.utterance_start_s) * 1000

    @property
    def total_cost_usd(self) -> float:
        return self.stt_cost_usd + self.llm_cost_usd + self.tts_cost_usd

    def to_dict(self) -> dict:
        return {
            "turn_index": self.turn_index,
            "timing_ms": {
                "stt": self.stt_ms,
                "llm_ttft": self.llm_ttft_ms,
                "llm_total": self.llm_total_ms,
                "tts_ttfa": self.tts_ttfa_ms,
                "end_to_end": self.end_to_end_ms,
            },
            "volume": {
                "stt_audio_seconds": self.stt_audio_seconds,
                "llm_input_tokens": self.llm_input_tokens,
                "llm_output_tokens": self.llm_output_tokens,
                "tts_characters": self.tts_characters,
            },
            "cost_usd": {
                "stt": round(self.stt_cost_usd, 8),
                "llm": round(self.llm_cost_usd, 8),
                "tts": round(self.tts_cost_usd, 8),
                "total": round(self.total_cost_usd, 8),
            },
        }


# ── Per-Session Metrics ───────────────────────────────────────────────────────


@dataclass
class SessionMetrics:
    """Lifetime metrics for one WebSocket session."""

    session_id: UUID
    connected_at: float = field(default_factory=time.monotonic)
    disconnected_at: float | None = None

    turns: list[TurnMetrics] = field(default_factory=list)
    current_turn: TurnMetrics | None = None

    def start_turn(self) -> TurnMetrics:
        """Create a fresh TurnMetrics and set it as current."""
        if self.current_turn is not None:
            self.finish_turn()
        turn = TurnMetrics(turn_index=len(self.turns))
        self.current_turn = turn
        return turn

    def finish_turn(self) -> TurnMetrics | None:
        """Commit the current turn to history and return it."""
        if self.current_turn is None:
            return None
        if self.current_turn.turn_end_s is None:
            self.current_turn.turn_end_s = time.monotonic()
        self.turns.append(self.current_turn)
        finished = self.current_turn
        self.current_turn = None
        
        logger.info("metrics.turn_finished", session_id=str(self.session_id), turn_index=finished.turn_index, metrics=finished.to_dict())
        
        return finished

    # ── Aggregates ────────────────────────────────────────────────────────

    @property
    def total_turns(self) -> int:
        return len(self.turns) + (1 if self.current_turn else 0)

    @property
    def avg_end_to_end_ms(self) -> float | None:
        vals = [t.end_to_end_ms for t in self.turns if t.end_to_end_ms is not None]
        if self.current_turn and self.current_turn.end_to_end_ms is not None:
            vals.append(self.current_turn.end_to_end_ms)
        return sum(vals) / len(vals) if vals else None

    @property
    def total_cost_usd(self) -> float:
        cost = sum(t.total_cost_usd for t in self.turns)
        if self.current_turn:
            cost += self.current_turn.total_cost_usd
        return cost

    @property
    def session_duration_s(self) -> float:
        end = self.disconnected_at or time.monotonic()
        return end - self.connected_at

    def to_dict(self) -> dict:
        turns_list = [t.to_dict() for t in self.turns]
        if self.current_turn:
            turns_list.append(self.current_turn.to_dict())
            
        return {
            "session_id": str(self.session_id),
            "duration_s": round(self.session_duration_s, 2),
            "total_turns": self.total_turns,
            "avg_end_to_end_ms": (
                round(self.avg_end_to_end_ms, 1)
                if self.avg_end_to_end_ms is not None
                else None
            ),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "turns": turns_list,
        }


# ── Global Metrics Registry ───────────────────────────────────────────────────


class MetricsRegistry:
    """Singleton accumulator for all sessions (no locks — asyncio single-thread)."""

    def __init__(self) -> None:
        self._active: dict[UUID, SessionMetrics] = {}
        self._completed: list[SessionMetrics] = []

    def register(self, session_id: UUID) -> SessionMetrics:
        sm = SessionMetrics(session_id=session_id)
        self._active[session_id] = sm
        return sm

    def complete(self, session_id: UUID) -> None:
        sm = self._active.pop(session_id, None)
        if sm is not None:
            sm.disconnected_at = time.monotonic()
            self._completed.append(sm)
            # Cap history to last 1000 sessions to avoid unbounded growth
            if len(self._completed) > 1000:
                self._completed = self._completed[-1000:]

    def get(self, session_id: UUID) -> SessionMetrics | None:
        return self._active.get(session_id)

    def global_summary(self) -> dict:
        all_sessions = list(self._active.values()) + self._completed
        all_turns = []
        for s in all_sessions:
            all_turns.extend(s.turns)
            if s.current_turn:
                all_turns.append(s.current_turn)
        e2e_vals = [t.end_to_end_ms for t in all_turns if t.end_to_end_ms is not None]

        return {
            "active_sessions": len(self._active),
            "completed_sessions": len(self._completed),
            "total_turns": len(all_turns),
            "avg_end_to_end_ms": (
                round(sum(e2e_vals) / len(e2e_vals), 1) if e2e_vals else None
            ),
            "p95_end_to_end_ms": _percentile(e2e_vals, 95),
            "total_cost_usd": round(sum(s.total_cost_usd for s in all_sessions), 6),
            "active_session_details": [
                s.to_dict() for s in self._active.values()
            ],
        }


def _percentile(data: list[float], pct: float) -> float | None:
    if not data:
        return None
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    f, c = int(k), min(int(k) + 1, len(sorted_data) - 1)
    return round(sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f), 1)


# ── Module-level singleton ────────────────────────────────────────────────────

metrics_registry = MetricsRegistry()
