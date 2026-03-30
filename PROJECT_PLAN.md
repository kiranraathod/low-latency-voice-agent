# PROJECT PLAN — Real-Time Voice AI Agent

---

## 📋 Progress Tracker

### Phase 1 — Transport & Skeleton (Hours 0–4)
- [x] Project scaffold (`pyproject.toml`, `app/` package, uv setup)
- [x] FastAPI app (`main.py` with uvicorn)
- [x] WebSocket handler (`/ws/talk` — accept, echo binary frames)
- [x] Session manager (create/track/destroy, session IDs)
- [x] Message models (Pydantic models for JSON control frames)
- [x] Config (`pydantic-settings`, env vars for API keys)
- [x] Health endpoint (`GET /health`)
- [x] Structured logging (`structlog` JSON output)

### Phase 2 — STT Integration (Hours 4–7)
- [x] Deepgram streaming WebSocket client
- [x] Audio forwarding (`audio_receiver` → Deepgram)
- [x] Transcript handling (partials + finals parsing)
- [x] Endpointing config (`endpointing=300`)
- [x] STT latency logging
- [x] Send partial transcripts to client as JSON

### Phase 3 — LLM + Tool Calling (Hours 7–12)
- [x] Gemini Flash streaming client (`google-genai` SDK)
- [x] System prompt (voice assistant persona)
- [x] Sentence chunker (buffer tokens, emit on boundaries)
- [x] `play_audio` tool definition for Gemini
- [x] Tool executor (load clip, send via WS)
- [x] Conversation memory (last 10 turns, in-memory)
- [x] LLM latency logging (TTFT + total)
- [x] Token counting for cost estimation

### Phase 4 — TTS Integration (Hours 12–16)
- [ ] ElevenLabs streaming WebSocket client
- [ ] Text streaming (sentence chunks → ElevenLabs)
- [ ] Audio forwarding (MP3 chunks → client WS)
- [ ] Voice selection (fast, natural voice)
- [ ] TTS latency logging
- [ ] Character counting for cost estimation
- [x] Full pipeline end-to-end test

### Phase 5 — Metrics & Observability (Hours 16–19)
- [x] Metrics collector (per-session + global)
- [x] Cost calculator (STT $/min, LLM $/token, TTS $/char)
- [x] `GET /metrics` endpoint
- [x] Error handling (timeouts, graceful disconnect, queue drain)
- [x] Structured JSON logs (per-stage timing + costs)
- [x] Clean session teardown on disconnect

### Phase 6 — Docker, Client & Demo (Hours 19–24)
- [x] Dockerfile (Python 3.11-slim)
- [x] `docker-compose.yml` (single-command startup)
- [x] `.env.example` (API key template)
- [x] Browser client (`index.html` + `app.js`, Web Audio API)
- [x] Test script (`test_client.py` with pre-recorded WAV)
- [x] `README.md` (all required sections)
- [ ] Demo video recorded (3–5 min)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        FastAPI Server                               │
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐      │
│  │  Audio    │    │   STT    │    │   LLM    │    │   TTS    │      │
│  │ Receiver  │───▶│Processor │───▶│Processor │───▶│Processor │      │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘      │
│       ▲          asyncio.Queue   asyncio.Queue   asyncio.Queue      │
│       │               │               │               │             │
│       │          Deepgram WS     Gemini Flash    ElevenLabs WS      │
│       │          (Nova-2)        (Streaming)     (Streaming)        │
│       │                                │                            │
│  WebSocket                        Tool Executor                     │
│  /ws/talk                         (play_audio)                      │
│       │                               │                             │
│       ▼                               ▼                             │
│  ┌──────────┐                  ┌──────────────┐                     │
│  │  Client   │◀────────────────│ Audio Clip   │                     │
│  │ (Browser) │                 │ (side-effect) │                     │
│  └──────────┘                  └──────────────┘                     │
│                                                                     │
│  GET /health ──▶ {"status": "ok"}                                  │
│  GET /metrics ─▶ {sessions, latency, cost}                         │
└─────────────────────────────────────────────────────────────────────┘
```

### Async Pipeline Detail

Each WebSocket connection spawns a **session** with 4 concurrent tasks:

1. **audio_receiver**: Reads binary frames from client → pushes to `stt_queue`
2. **stt_processor**: Reads from `stt_queue` → streams to Deepgram → emits final transcript to `llm_queue`
3. **llm_processor**: Reads from `llm_queue` → calls Gemini Flash (streaming) → chunks sentences → pushes to `tts_queue`. Handles tool calls inline.
4. **tts_processor**: Reads from `tts_queue` → streams to ElevenLabs → sends audio chunks back to client WS

**Concurrency Safety:**
- Each session is isolated (own queues, own tasks)
- No shared mutable state between sessions
- `asyncio.TaskGroup` ensures all tasks are cancelled together on disconnect
- Queues provide natural backpressure

---

## Component Selection

### STT: Deepgram Nova-2 (Streaming WebSocket)
- **Latency**: ~100-200ms for final transcript
- **Why**: Native WebSocket API, server-side VAD/endpointing, fastest streaming STT available
- **Cost**: $0.0043/minute

### LLM: Google Gemini 2.0 Flash
- **TTFT**: ~300-500ms (fastest major LLM)
- **Why**: Native streaming + tool-calling, generous free tier, lowest TTFT
- **Cost**: $0.075/1M input tokens, $0.30/1M output tokens

### TTS: ElevenLabs (Streaming WebSocket)
- **Latency**: ~200-300ms to first audio chunk
- **Why**: WebSocket streaming API, best voice quality, streams audio as text arrives
- **Cost**: ~$0.30/1K characters

### Latency Budget
| Stage | Target | Running Total |
|-------|--------|--------------|
| Deepgram endpointing | ~200ms | 200ms |
| STT final transcript | ~150ms | 350ms |
| LLM TTFT | ~400ms | 750ms |
| Sentence chunking | ~75ms | 825ms |
| TTS first chunk | ~300ms | 1125ms |
| Network overhead | ~150ms | 1275ms |
| **TOTAL** | | **~1275ms** ✅ |

---

## The 24-Hour Roadmap

### Phase 1 — Transport & Skeleton (Hours 0–4)
**Objective**: FastAPI app with bidirectional WebSocket

| Task | Details |
|------|---------|
| Project scaffold | `requirements.txt`, `pyproject.toml`, `app/` package |
| FastAPI app | `main.py` with uvicorn |
| WebSocket handler | `/ws/talk` — accept connections, echo binary frames |
| Session manager | Create/track/destroy sessions, generate session IDs |
| Message models | Pydantic models for JSON control frames |
| Config | `pydantic-settings` with env vars for API keys |
| Health endpoint | `GET /health` → `{"status": "ok"}` |
| Structured logging | `structlog` setup with JSON output |

**Milestone**: Can connect via `websocat` or browser, send/receive binary audio frames.

---

### Phase 2 — STT Integration (Hours 4–7)
**Objective**: Live audio → Deepgram → partial/final transcripts

| Task | Details |
|------|---------|
| Deepgram client | WebSocket connection to Deepgram streaming API |
| Audio forwarding | `audio_receiver` task pushes PCM frames to Deepgram |
| Transcript handling | Parse Deepgram responses (partials + finals) |
| Endpointing config | Set `endpointing=300` for 300ms silence detection |
| Latency logging | Measure time from first audio frame to final transcript |
| Client feedback | Send partial transcripts as JSON frames to client |

**Milestone**: Speak into client → see partial transcripts updating → final transcript logged.

---

### Phase 3 — LLM + Tool Calling (Hours 7–12)
**Objective**: Transcript → Gemini Flash → streaming response + tool execution

| Task | Details |
|------|---------|
| Gemini client | `google-genai` SDK, streaming mode |
| System prompt | Voice assistant persona, concise responses |
| Sentence chunker | Buffer LLM tokens, emit on sentence boundaries |
| Tool definition | `play_audio` function declaration for Gemini |
| Tool executor | Load audio clip, send via WS on tool call |
| Conversation memory | Last 10 turns per session (in-memory list) |
| Latency logging | TTFT + total generation time |
| Token counting | Track input/output tokens for cost |

**Milestone**: Send text → get streaming LLM response → tool calls work → audio clip plays.

---

### Phase 4 — TTS Integration (Hours 12–16)
**Objective**: LLM sentence chunks → ElevenLabs → audio chunks → client

| Task | Details |
|------|---------|
| ElevenLabs client | WebSocket streaming API connection |
| Text streaming | Send sentence chunks as they arrive from LLM |
| Audio forwarding | Receive MP3/PCM chunks, forward to client WS |
| Voice selection | Use a fast, natural voice (e.g., "Rachel") |
| Latency logging | Time from text sent to first audio chunk received |
| Char counting | Track characters for cost estimation |
| Full pipeline test | Audio in → STT → LLM → TTS → audio out, end-to-end |

**Milestone**: Full voice conversation works. Speak → hear response within ~1.5s.

---

### Phase 5 — Metrics & Observability (Hours 16–19)
**Objective**: `/metrics` endpoint, cost tracking, error handling

| Task | Details |
|------|---------|
| Metrics collector | Track per-session and global metrics |
| Cost calculator | STT: $/min, LLM: $/token, TTS: $/char |
| `/metrics` endpoint | Return JSON with sessions, latency, costs |
| Error handling | Timeouts (30s turn), graceful disconnect, queue drain |
| Structured logs | Per-stage timing, costs, errors in JSON format |
| Session teardown | Clean up all resources on disconnect |

**Milestone**: `/metrics` returns real data. Logs show per-stage timing and costs.

---

### Phase 6 — Docker, Client & Demo (Hours 19–24)
**Objective**: Containerized deployment, browser client, documentation

| Task | Details |
|------|---------|
| Dockerfile | Python 3.11-slim, multi-stage build |
| docker-compose.yml | Service + env vars, single `docker compose up` |
| `.env.example` | Template for API keys |
| Browser client | HTML + JS with Web Audio API for mic + playback |
| Test script | Python script to send pre-recorded WAV |
| README.md | All required sections per spec |
| Demo prep | Script the demo flow, record video |

**Milestone**: `docker compose up` → open browser → full voice interaction → demo recorded.

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| ElevenLabs rate limit on free tier | Fall back to Deepgram TTS (beta) or Google Cloud TTS |
| Deepgram WebSocket disconnect | Auto-reconnect with exponential backoff |
| Gemini TTFT spikes | Set 2s timeout, return "I'm thinking..." fallback |
| Browser mic permissions | Provide `test_client.py` as alternative demo path |
| Docker build issues | Provide `requirements.txt` for direct `pip install` fallback |

---

## What's NOT in Scope (MVP Cuts)

- ❌ WebRTC (using WebSocket + PCM instead)
- ❌ Client-side VAD (Deepgram handles it)
- ❌ Authentication / authorization
- ❌ Persistent storage / database
- ❌ HTTPS / TLS (plain HTTP for prototype)
- ❌ Multiple tools (only `play_audio`)
- ❌ Automatic reconnection logic in client
- ❌ Unit test suite (integration test via `test_client.py` only)
- ❌ CI/CD pipeline
