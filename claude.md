# Voice AI Agent — Project Context

## Project Overview
Real-time voice AI agent prototype with ≤ 2s end-to-end latency.
**Assignment**: `adiiva_r.md`

## Tech Stack
| Component | Technology | Why |
|-----------|-----------|-----|
| **Runtime** | Python 3.11+ (asyncio) | Async-first, TaskGroup support |
| **Framework** | FastAPI + uvicorn | Native WebSocket, async, fast |
| **STT** | Deepgram Nova-2 (streaming WS) | Fastest streaming STT, server-side VAD |
| **LLM** | Google Gemini 2.0 Flash | Fastest TTFT, native tool-calling |
| **TTS** | ElevenLabs (streaming WS) | Best quality, lowest latency TTS |
| **Logging** | structlog | Structured JSON logging |
| **Container** | Docker + docker-compose | Single-command deployment |

## Architecture Pattern
- 4 async tasks per WebSocket session connected by `asyncio.Queue`
- Tasks: `audio_receiver` → `stt_processor` → `llm_processor` → `tts_processor`
- Cancellation via `asyncio.TaskGroup` 
- Sentence-level pipelining: LLM tokens chunked at sentence boundaries, streamed to TTS immediately

## Latency Budget (Target ≤ 2000ms)
| Stage | Budget |
|-------|--------|
| VAD/Endpoint | ~200ms |
| STT Final | ~100-200ms |
| LLM TTFT | ~300-500ms |
| Sentence chunk | ~50-100ms |
| TTS First Chunk | ~200-400ms |
| Network | ~100-200ms |
| **Total** | **~950-1600ms** ✅ |

## Key Design Decisions
1. **No WebRTC** — WebSocket with PCM audio frames is sufficient
2. **No client-side VAD** — Deepgram handles endpointing server-side
3. **Sentence pipelining** — Don't wait for full LLM response; TTS starts on first sentence
4. **In-memory only** — No database, no persistent state
5. **Pre-bundled audio clip** — `play_audio` tool plays a notification.mp3

## File Structure
```
app/
  main.py, config.py, session.py, models.py, metrics.py
  pipeline/
    stt.py, llm.py, tts.py, tools.py, prompts.py
client/
  index.html, app.js
scripts/
  test_client.py
Dockerfile, docker-compose.yml, .env.example, README.md
```

## API Keys Required
- `DEEPGRAM_API_KEY`
- `GEMINI_API_KEY`  
- `ELEVENLABS_API_KEY`

## Progress Tracking
- [ ] Phase 1: Foundation & Transport (Hours 0-4)
- [ ] Phase 2: STT Pipeline (Hours 4-7)
- [ ] Phase 3: LLM + Tool Calling (Hours 7-12)
- [ ] Phase 4: TTS Pipeline (Hours 12-16)
- [x] Phase 5: Metrics & Observability (Hours 16-19)
- [ ] Phase 6: Docker, Client & Demo (Hours 19-24)

## Commands
```bash
# Setup (uv)
uv init
uv venv
uv add fastapi uvicorn websockets structlog pydantic-settings deepgram-sdk google-genai elevenlabs aiohttp

# Development
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Docker
docker compose up --build

# Test client
uv run python scripts/test_client.py
```
