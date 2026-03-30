"""
app/pipeline/llm.py — Gemini 2.0 Flash streaming LLM processor.

Implemented in Phase 3. This file is a placeholder for Phase 1.

Architecture:
  llm_processor(session) reads final transcripts from session.llm_queue
  → calls Gemini Flash (streaming, with tool-calling)
  → buffers tokens until sentence boundary
  → pushes sentence chunks to session.tts_queue
  → handles tool_call responses inline (calls execute_tool)
  → logs TTFT, total generation time, input/output token counts
"""
from __future__ import annotations

# Phase 3 implementation goes here.
# See PROJECT_PLAN.md Phase 3 checklist.
