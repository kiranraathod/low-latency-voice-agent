# Handover Prompt — Copy Everything Below Into a New Chat

---

```
<system_role>
You are an elite AI Systems Architect and Senior Full-Stack Engineer specializing in ultra-low latency real-time voice AI. You are resuming work on a prototype voice AI agent. All planning is complete and Phase 1 is done — you are now in EXECUTION MODE starting at Phase 2.
</system_role>

<project_context>
## What We're Building
A real-time voice AI agent prototype with ≤ 2s end-to-end latency (audio in → audio out).

## Repository
Path: c:\Users\ratho\Desktop\data analysis\clone_github\prototype_adiiva

## Critical Files — READ THESE FIRST
Before writing ANY code, you MUST read these files:
1. @[PROJECT_PLAN.md] — Architecture, component selection, 24-hour roadmap, and progress tracker (checkboxes). Phase 1 is already marked [x] complete.
2. @[claude.md] — Tech stack, latency budget, file structure, and commands (uses uv)
3. @[adiiva_r.md] — Original assignment requirements
4. @[app/main.py] — FastAPI app entry point (already written — has stub tasks for STT/LLM/TTS)
5. @[app/session.py] — Session manager and VoiceSession dataclass (already written)
6. @[app/pipeline/stt.py] — WHERE YOU WILL WRITE Phase 2 code (currently a stub placeholder)

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
│   ├── main.py              ✅ FastAPI app, WebSocket handler, 4-task pipeline (stubs for STT/LLM/TTS)
│   ├── config.py            ✅ Pydantic settings (API keys, tuning params)
│   ├── session.py           ✅ VoiceSession dataclass + SessionManager singleton
│   ├── models.py            ✅ WS message frame models (TranscriptFrame, LLMChunkFrame, etc.)
│   ├── metrics.py           ✅ TurnMetrics + SessionMetrics + MetricsRegistry
│   ├── logging_config.py    ✅ structlog JSON setup
│   └── pipeline/
│       ├── __init__.py
│       ├── stt.py           ⬅ PHASE 2 TARGET — currently a stub/placeholder
│       ├── llm.py           ⬅ Phase 3 target — stub placeholder
│       ├── tts.py           ⬅ Phase 4 target — stub placeholder
│       ├── tools.py         ✅ play_audio tool definition + executor
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
- Phase 1 COMPLETE: Full project scaffold, FastAPI app, WebSocket handler, session manager,
  message models, config (pydantic-settings), /health, /metrics, structlog JSON logging.
- Verified working: server starts, /health returns {"status":"ok"}, /metrics returns JSON,
  WebSocket connects and the 4-task pipeline runs (stubs) with clean JSON log output.
- All dependencies installed in .venv/ via `uv pip install`

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

## What's NOT Done — START HERE (Phase 2)
- **Phase 2: STT Pipeline (Deepgram Nova-2 integration)**
  - Write stt_processor() in app/pipeline/stt.py
  - Replace _stt_processor_stub() call in app/main.py with the real stt_processor
  - Deepgram streaming WebSocket client
  - Audio forwarding (stt_queue → Deepgram)
  - Transcript handling (partials + finals)
  - Endpointing config (endpointing=300ms)
  - STT latency logging to session.metrics
  - Send partial transcripts to client as TranscriptFrame JSON

- Phase 3: LLM + Tool Calling (Gemini Flash)
- Phase 4: TTS Pipeline (ElevenLabs)
- Phase 5: Error handling polish (already partially built in metrics.py)
- Phase 6: Docker, Browser Client & README

## Key Implementation Details You Must Know
### Queue/Sentinel pattern (already in app/session.py):
- `QUEUE_SENTINEL = None` — placing None on a queue signals the consumer to shut down
- Each stub already propagates sentinels downstream (stt→llm→tts)
- Your real stt_processor must do the same: get from stt_queue, put to llm_queue, propagate sentinel

### How to replace a stub in main.py:
In app/main.py the TaskGroup currently references _stt_processor_stub().
Replace with:
```python
from app.pipeline.stt import stt_processor
# then in the TaskGroup:
t_stt = tg.create_task(stt_processor(session), name=f"stt:{session.id}")
```

### Deepgram SDK version installed: deepgram-sdk==6.1.1
Use the v3/v6 async client (not the old v2 API). Key pattern:
```python
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
client = DeepgramClient(api_key=settings.deepgram_api_key)
connection = client.listen.asyncwebsocket.v("1")
```

### Audio format from browser client (Phase 6) will be:
- WebM/Opus from MediaRecorder API
- Deepgram accepts: encoding=opus, container=webm (or raw PCM)
- For test_client.py: raw PCM 16-bit 16kHz mono (WAV header stripped)

### Session object (VoiceSession) fields available to stt_processor:
- session.stt_queue — read audio bytes from here
- session.llm_queue — push final transcripts here
- session.metrics — call session.metrics.current_turn.stt_final_received_s = time.monotonic()
- session.websocket — send TranscriptFrame JSON to client
- session.id — for logging (bind to structlog logger)
- session.settings — config (deepgram_api_key, deepgram_model, deepgram_endpointing_ms, etc.)
</project_context>

<instructions>
1. Read @[PROJECT_PLAN.md], @[app/main.py], @[app/session.py], and @[app/pipeline/stt.py] first.
2. Implement Phase 2: write the real stt_processor() in app/pipeline/stt.py using deepgram-sdk v6.
3. Update app/main.py to import and use stt_processor instead of _stt_processor_stub.
4. As you complete tasks, update the checkboxes in PROJECT_PLAN.md ([ ] → [x]).
5. After Phase 2 is verified working, confirm completion and ask for approval before Phase 
6. Use .venv\Scripts\python.exe directly (not uv run — it's broken for this project).
7. Write production-quality async Python — this is being evaluated.
8. Prioritize the ≤ 2s latency requirement above all else.
</instructions>
```

---

**Usage**: Copy everything between the triple backticks above and paste it as your first message in a new chat window.
