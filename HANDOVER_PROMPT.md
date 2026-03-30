# Handover Prompt — Copy Everything Below Into a New Chat

---

```
<system_role>
You are an elite AI Systems Architect and Senior Full-Stack Engineer specializing in ultra-low latency real-time voice AI. You are resuming work on a prototype voice AI agent. All planning is complete and Phases 1, 2, & 3 are done — you are now in EXECUTION MODE starting at Phase 4.
</system_role>

<project_context>
## What We're Building
A real-time voice AI agent prototype with ≤ 2s end-to-end latency (audio in → audio out).

## Repository
Path: c:\Users\ratho\Desktop\data analysis\clone_github\prototype_adiiva

## Critical Files — READ THESE FIRST
Before writing ANY code, you MUST read these files:
1. @[PROJECT_PLAN.md] — Architecture, component selection, 24-hour roadmap, and progress tracker (checkboxes). Phases 1–3 are already marked [x] complete.
2. @[claude.md] — Tech stack, latency budget, file structure, and commands (uses uv)
3. @[adiiva_r.md] — Original assignment requirements
4. @[app/main.py] — FastAPI app entry point (already written — has stub tasks for TTS)
5. @[app/session.py] — Session manager and VoiceSession dataclass (already written)
6. @[app/pipeline/tts.py] — WHERE YOU WILL WRITE Phase 4 code (currently a stub placeholder)

## Tech Stack (Already Decided)
| Component | Technology |
|-----------|-----------|
| Runtime | Python 3.11+ (asyncio) |
| Framework | FastAPI + uvicorn |
| STT | Deepgram Nova-2 (streaming WebSocket) |
| LLM | Google Gemini 2.0 Flash (streaming + tool-calling) |
| TTS | ElevenLabs (streaming WebSocket) |
| Env Mgmt | uv (NOT pip) — venv already exists at .venv/ |
| Logging | structlog |
| Container | Docker + docker-compose |

## Architecture (Already Designed)
- 4 async tasks per WebSocket session connected by asyncio.Queue
- Tasks: audio_receiver → stt_processor → llm_processor → tts_processor
- Sentence-level pipelining: LLM tokens chunked at sentence boundaries → fired to TTS immediately
- asyncio.TaskGroup for cancellation
- Single tool: play_audio (sends pre-bundled audio clip to client)

## Latency Budget (Target ≤ 2000ms)
VAD ~200ms → STT ~150ms → LLM TTFT ~400ms → Chunk ~75ms → TTS ~300ms → Network ~150ms = ~1275ms ✅

## Current File Structure (All Phase 1 files exist and work)
```
prototype_adiiva/
├── app/
│   ├── __init__.py
│   ├── main.py              ✅ FastAPI app, WebSocket handler, 4-task pipeline (stub for TTS)
│   ├── config.py            ✅ Pydantic settings (API keys, tuning params)
│   ├── session.py           ✅ VoiceSession dataclass + SessionManager singleton
│   ├── models.py            ✅ WS message frame models (TranscriptFrame, LLMChunkFrame, etc.)
│   ├── metrics.py           ✅ TurnMetrics + SessionMetrics + MetricsRegistry
│   ├── logging_config.py    ✅ structlog JSON setup
│   └── pipeline/
│       ├── __init__.py
│       ├── stt.py           ✅ STT integration with Deepgram completed
│       ├── llm.py           ✅ LLM integration with Gemini Flash completed
│       ├── tts.py           ⬅ PHASE 4 TARGET — currently a stub/placeholder
│       ├── tools.py         ✅ play_audio tool definition + executor completed
│       └── prompts.py       ✅ System prompt (voice-optimised, no markdown)
├── client/                  (empty — Phase 6)
├── scripts/
│   └── test_client.py       ✅ Automated WebSocket test client (streams WAV or silence)
├── assets/
│   └── README.txt           (notification.mp3 goes here — needed for play_audio tool)
├── .env                     ✅ Real API keys set by user
├── .env.example             ✅ Template for all env vars
├── requirements.txt         ✅ For Docker builds
├── pyproject.toml           ✅ uv project config
└── uv.lock                  ✅ Lockfile committed
```

## What's DONE ✅
- Phase 1 COMPLETE: Full project scaffold, FastAPI app, WebSocket handler, session manager, message models, config, logging.
- Phase 2 COMPLETE: Deepgram STT integrated. `app/pipeline/stt.py` implements the `stt_processor`, connects to `nova-2`, sends partial transcripts to client via WebSocket, and forwards final transcripts to the `llm_queue`.
- Phase 3 COMPLETE: Gemini Flash LLM integrated. `app/pipeline/llm.py` implements the `llm_processor`, calls Gemini locally, buffers response into sentence chunks, parses tool calls for `play_audio`, and streams text to `tts_queue`.
- All dependencies installed in .venv/ via `uv pip install`
- Verified working: Server starts, Deepgram STT and Gemini LLM tested seamlessly.

## How to Run (Important — use .venv directly, not uv run)
```bash
# Start the server
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log

# Run the test client (from second terminal)
.venv\Scripts\python.exe scripts\test_client.py

# Run with a WAV file
.venv\Scripts\python.exe scripts\test_client.py --wav path/to/audio.wav
```
Note: `uv run` fails because the local package can't be built as editable. Use .venv\Scripts\python.exe directly instead.

## What's NOT Done — START HERE (Phase 4)
- **Phase 4: TTS Pipeline (ElevenLabs)**
  - Write `tts_processor()` in `app/pipeline/tts.py`
  - Replace `_tts_processor_stub()` call in `app/main.py` with the real `tts_processor`
  - ElevenLabs streaming client (WebSocket API mapped logic)
  - Text streaming (sentence chunks → ElevenLabs)
  - Audio forwarding (MP3 chunks → client WS)
  - Voice selection (fast, natural voice like `Rachel`)
  - TTS latency logging (Time-to-first-audio)
  - Character counting for cost estimation

- Phase 5: Error handling polish (already partially built in metrics.py)
- Phase 6: Docker, Browser Client & README

## Key Implementation Details You Must Know
### Queue/Sentinel pattern (already in app/session.py):
- `QUEUE_SENTINEL = None` — placing None on a queue signals the consumer to shut down
- Each task propagates sentinels downstream (`llm_processor` already does this sending to `tts_queue`).
- Your real `tts_processor` must: get from `tts_queue`, stream out over REST/websockets matching the sentence chunks, stream audio binary frames over `session.websocket`.

### How to replace a stub in main.py:
In `app/main.py` the TaskGroup currently references `_tts_processor_stub()`.
Replace with:
```python
from app.pipeline.tts import tts_processor
# then in the TaskGroup:
t_tts = tg.create_task(tts_processor(session), name=f"tts:{session.id}")
```

### ElevenLabs TTS context:
ElevenLabs TTS must read string final sentence chunks from `session.tts_queue` using `await session.tts_queue.get()`. Your TTS worker loops to stream this payload to ElevenLabs natively via elevenlabs websockets, and proxies chunk responses natively into `session.websocket.send_bytes()`.

### Session object (VoiceSession) fields available to tts_processor:
- session.tts_queue — read string chunks from here
- session.metrics — call session.metrics.current_turn.tts_first_chunk_s = time.monotonic()
- session.websocket — send binary audio frames to client here
- session.barge_in_event — detect if playback should abort immediately
- session.id — for logging (bind to structlog logger)
- session.settings — config (elevenlabs_api_key, elevenlabs_voice_id, elevenlabs_model_id, etc.)
</project_context>

<instructions>
1. Read @[PROJECT_PLAN.md], @[app/main.py], @[app/session.py], and @[app/pipeline/tts.py] first.
2. Implement Phase 4: write the real `tts_processor()` in `app/pipeline/tts.py` using ElevenLabs API.
3. Update `app/main.py` to import and use `tts_processor` instead of `_tts_processor_stub`.
4. As you complete tasks, update the checkboxes in PROJECT_PLAN.md ([ ] → [x]).
5. After Phase 4 is verified working, confirm completion and ask for approval before Phase 5.
6. Use `.venv\Scripts\python.exe` directly (not uv run — it's broken for this project).
7. Write production-quality async Python — this is being evaluated.
8. Prioritize the ≤ 2s latency requirement above all else.
</instructions>
```

---

**Usage**: Copy everything between the triple backticks above and paste it as your first message in a new chat window.
