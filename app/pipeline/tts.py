"""
app/pipeline/tts.py — ElevenLabs streaming TTS processor.

Implemented in Phase 4. This file is a placeholder for Phase 1.

Architecture:
  tts_processor(session) reads sentence chunks from session.tts_queue
  → opens an ElevenLabs WebSocket connection
  → streams sentence text as it arrives
  → forwards MP3 audio chunks to the client WebSocket
  → logs time-to-first-audio, total TTS time, character count
  → respects barge_in_event to cancel mid-stream
"""
from __future__ import annotations

# Phase 4 implementation goes here.
# See PROJECT_PLAN.md Phase 4 checklist.
