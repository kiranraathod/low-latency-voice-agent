"""
app/pipeline/stt.py — Deepgram Nova-2 streaming STT processor.

Implemented in Phase 2. This file is a placeholder for Phase 1.

Architecture:
  stt_processor(session) reads bytes from session.stt_queue
  → opens a Deepgram WebSocket connection
  → forwards audio chunks
  → receives transcript events (partial + final)
  → on final transcript, pushes to session.llm_queue
  → logs per-stage timing to session.metrics
"""
from __future__ import annotations

# Phase 2 implementation goes here.
# See PROJECT_PLAN.md Phase 2 checklist.
