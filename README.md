# Real-Time Voice AI Agent Prototype

A fully asynchronous, low-latency voice AI agent that streams microphone audio through a 4-stage pipeline:
**Deepgram Nova-2 STT → Groq Llama 3.3 (via OpenAI SDK) → Sentence Chunker → Deepgram Aura TTS**

All six phases of the assignment architecture have been implemented and validated.

---

## Status

| Requirement | Status |
|---|---|
| Streaming STT (partial transcripts) | ✅ Deepgram Nova-2 |
| LLM with tool-calling | ✅ Groq Llama 3.3 70B |
| Audio playback tool (`play_audio`) | ✅ Bundled MP3 clip |
| Streaming TTS output | ✅ Deepgram Aura WebSocket |
| Fully async & concurrency-safe | ✅ `asyncio.TaskGroup` |
| Dockerized, single-command startup | ✅ `docker compose up --build` |
| `/health` and `/metrics` endpoints | ✅ |
| Per-turn & per-session cost tracking | ✅ |
| End-to-end latency target ≤ 2s | ⚠️ Validated at 2–6s (see Technical Notes) |

---

## Demo Videos

Two recordings are provided to document the implementation at different optimization stages:

| Video | Description | Latency |
|---|---|---|
| **Demo 1 (Stable Build)** | Full pipeline with Docker startup, voice interaction, tool call, and `/metrics` | ~5–6s |
| **Demo 2 (Experimental)** | Edge TTS variant achieving lower latency, with local machine bottleneck caveat | ~1.5s |

> **Note on audio recording**: There was a minor issue with the screen recording software where the notification sound for the `play_audio` tool call was not captured in the audio track. The tool is functioning correctly in the code — the server pushes the MP3 clip over the WebSocket and the browser plays it.

---

## Build & Run

### Prerequisites
- Docker and Docker Compose, **or** Python 3.11+ with `uv`
- API keys for Deepgram and Groq

### 1 — Configure environment

Copy the template and fill in your keys:

```bash
cp .env.example .env
```

Then edit `.env`. The required variables are:

```env
# Deepgram (STT + TTS)
DEEPGRAM_API_KEY="your_deepgram_api_key_here"

# Groq — accessed via the OpenAI-compatible SDK
OPENAI_API_KEY="your_groq_api_key_here"
OPENAI_BASE_URL="https://api.groq.com/openai/v1"
OPENAI_MODEL="llama-3.3-70b-versatile"
```

See [`.env.example`](.env.example) for all tunable parameters (model names, pricing constants, timeouts, etc.).

### 2 — Start the service

```bash
docker compose up --build
```

Or, for local development without Docker:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3 — Open the client

```
http://localhost:8000/client/index.html
```

Click **CONNECT**, allow microphone access, and speak.

### Observability endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | `{"status": "ok", "active_sessions": N, ...}` |
| `GET /metrics` | Per-session and global latency + cost breakdown |

---

## Streaming & Concurrency Model

Each WebSocket session (`/ws/talk`) creates a **session object** with four concurrent async tasks connected by `asyncio.Queue`:

```
Browser
  │
  │ PCM Int16 @ 16 kHz (binary frames)
  ▼
audio_receiver
  │ stt_queue
  ▼
stt_processor  ──── Deepgram Nova-2 WebSocket (streaming)
  │ llm_queue  (speech_final only — prevents duplicate LLM calls)
  ▼
llm_processor  ──── Groq Llama 3.3 70B  (sentence-chunked streaming)
  │               └─ play_audio tool → MP3 clip pushed directly to browser
  │ tts_queue
  ▼
tts_processor  ──── Deepgram Aura WebSocket (streaming PCM 24 kHz)
  │
  ▼
Browser (PCM audio → AudioContext playback)
```

- `asyncio.TaskGroup` cancels all sibling tasks on disconnect or error, preventing zombie API connections.
- Queues provide natural backpressure between stages.
- Each session is fully isolated — no shared mutable state across sessions.
- The browser receives explicit `audio_ready` JSON frames that specify the content type before each binary stream, so it can route:
  - `audio/pcm;rate=24000;channels=1;encoding=s16le` → Aura speech via `AudioContext`
  - `audio/mpeg` → tool audio clip (direct `<audio>` element)

---

## Techniques Used to Minimize Latency

1. **Raw PCM over WebSockets** — avoids browser media-stack overhead vs. WebRTC.
2. **Server-side VAD & endpointing** — Deepgram handles turn detection; no client-side VAD required.
3. **`speech_final` gating** — the LLM is only invoked on Deepgram's finalized utterance, preventing duplicate or premature generation.
4. **Sentence-level pipelining** — the LLM streams tokens; the sentence chunker emits to the TTS queue as soon as a sentence boundary is detected. TTS starts before the full LLM response finishes.
5. **Persistent Deepgram Aura WebSocket** — reuses a single socket per session instead of opening a new REST request per turn.
6. **Groq LPU inference** — Groq's Language Processing Units provide sub-400ms time-to-first-token for Llama 3.3 70B.

---

## Component Choices

### STT — Deepgram Nova-2
- Native streaming WebSocket API with server-side VAD and endpointing.
- Returns partial transcripts (displayed live in the UI) and final `speech_final` events.
- Measured latency to final transcript: ~100–250ms.
- **Cost**: $0.0043 / minute of audio.

### LLM — Groq Llama 3.3 70B (via OpenAI-compatible SDK)
- Groq's LPU hardware delivers ultra-low time-to-first-token (~270–400ms).
- The OpenAI Python SDK is used unchanged — `base_url` is pointed at Groq's endpoint.
- Supports tool-calling natively; `play_audio` is registered as a function tool.
- Conversation history (last 10 turns) is kept in-memory per session.
- **Cost**: $0.15 / 1M input tokens · $0.60 / 1M output tokens.

### TTS — Deepgram Aura (Streaming WebSocket)
- The primary TTS provider: streams PCM audio at 24 kHz over a persistent WebSocket.
- Each sentence chunk is sent as `SpeakV1Text` + `send_flush()` to minimise per-sentence delay.
- Supports barge-in via `send_clear()`.
- **Cost**: $0.015 / 1K characters synthesized.

> **Latency note & model pivot**: The original target was ≤ 2s using Gemini 2.0 Flash. Due to credit availability, the LLM was migrated to Groq, which delivers comparable or better TTFT. The current validated end-to-end latency is **2–4 seconds** in the stable build (5–6s with screen-recording overhead). An experimental Edge TTS variant achieved ~1.5s but encountered local machine bottlenecks during recording. The architectural pipeline is designed for sub-2s once hosted on cloud infrastructure with co-located services.

---

## Cost Estimation

Costs are tracked per-turn and accumulated per-session:

| Stage | Metric | Rate |
|---|---|---|
| STT | Audio seconds → minutes | $0.0043 / min |
| LLM | Prompt + completion tokens (from `usage` field) | $0.15 / 1M in · $0.60 / 1M out |
| TTS | Characters sent to Aura | $0.015 / 1K chars |

All values are exposed at `GET /metrics` and shown in the browser's telemetry panel in real time.

---

## Failure Scenario — Browser Disconnect Mid-Stream

**Trigger**: Browser tab closed or network dropped while Deepgram Aura is streaming audio.

**Expected behavior**:
1. FastAPI detects the WebSocket disconnect event.
2. The session's `asyncio.TaskGroup` raises `CancelledError` and cancels all four pipeline tasks.
3. Each task's `finally` block closes its external WebSocket (Deepgram STT and Aura).
4. Metrics collected up to the point of disconnect are finalized and remain readable at `GET /metrics`.
5. No zombie tasks or leaked API connections remain.

---

## Technical Notes

### Audio Recording Caveat
The screen recording software did not capture the notification sound from the `play_audio` tool invocation. The tool is fully functional in the codebase — the server reads `assets/notification.mp3` and pushes it over the WebSocket as an `audio/mpeg` binary frame, and the browser plays it via an `<audio>` element.

### Latency & Model Pivot
- **Original plan**: Gemini 2.0 Flash for the LLM stage, targeting ≤ 2s end-to-end.
- **Pivot**: Migrated to Groq (Llama 3.3 70B) via the OpenAI-compatible SDK due to API credit constraints. Groq's TTFT is comparable (~270–400ms).
- **Measured latency**: 2–4s in the stable build. Screen recording software added overhead in Demo 1 (5–6s visible).
- **Edge TTS experiment**: A variant using Microsoft Edge TTS (no API key required) achieved ~1.5s in isolated tests but was not stable enough for the final submission build.

### `.env.example` & API Keys
The [`.env.example`](.env.example) file is the canonical reference for all configuration. Copy it to `.env` and populate:
- `DEEPGRAM_API_KEY` — from [console.deepgram.com](https://console.deepgram.com)
- `OPENAI_API_KEY` — your **Groq** API key from [console.groq.com](https://console.groq.com)
- `OPENAI_BASE_URL` — set to `https://api.groq.com/openai/v1` (do not change)
- `OPENAI_MODEL` — `llama-3.3-70b-versatile` (or any Groq model with tool-calling support)

Pricing constants (`DEEPGRAM_COST_PER_MINUTE`, `OPENAI_COST_PER_1M_INPUT_TOKENS`, etc.) in `.env.example` are pre-filled with current public rates and are used directly by the `/metrics` cost calculator.

---

## Project Structure

```
prototype_adiiva/
├── app/
│   ├── main.py            # FastAPI app, WebSocket handler, TaskGroup orchestration
│   ├── config.py          # pydantic-settings — reads all env vars
│   ├── session.py         # Session lifecycle and queue management
│   ├── models.py          # Pydantic models for JSON control frames
│   ├── metrics.py         # Per-turn and per-session cost + latency tracking
│   ├── logging_config.py  # Structured JSON logging (structlog)
│   └── pipeline/
│       ├── stt.py         # Deepgram Nova-2 streaming client
│       ├── llm.py         # Groq Llama 3.3 streaming + sentence chunker
│       ├── tts.py         # Deepgram Aura streaming TTS
│       ├── tools.py       # play_audio tool executor
│       └── prompts.py     # System prompt
├── client/
│   ├── index.html         # Browser UI
│   └── app.js             # Web Audio API capture + PCM/MP3 playback routing
├── scripts/
│   └── test_client.py     # Headless test: sends pre-recorded WAV, saves audio output
├── assets/
│   └── notification.mp3   # Bundled audio clip for the play_audio tool
├── doc/
│   ├── adiiva_r.md        # Original assignment specification
│   ├── project_plan.md    # Phase-by-phase plan and architecture
│   ├── DEMO_PREP.md       # Demo video script and checklist
│   └── HANDOVER_PROMPT.md # Context document for AI-assisted development sessions
├── .env.example           # Configuration template (copy to .env, fill in keys)
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```
