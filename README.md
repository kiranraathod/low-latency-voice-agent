# Real-Time Voice AI Agent Prototype

A fully asynchronous, low-latency voice AI agent that pipes browser microphone audio through a 4-stage streaming pipeline:

**Deepgram Nova-2 STT → Groq Llama 3.3 70B (via OpenAI SDK) → Sentence Chunker → Deepgram Aura TTS**

All six phases of the assignment architecture have been implemented and validated end-to-end.

---

## 📹 Demo Videos

Two recordings are provided to document the implementation at different optimisation stages.

### Demo 1 — Stable Build (5–6s latency)

Full pipeline: Docker startup → live voice interaction → `play_audio` tool call → `/metrics` output.

https://github.com/user-attachments/assets/demo_1.mp4

> **Local file**: [`assets/demo_1.mp4`](assets/demo_1.mp4)

### Demo 2 — Experimental Build (~1.5s latency)

Edge TTS variant achieving lower latency. Local machine bottlenecks were present during recording.

https://github.com/user-attachments/assets/demo_2.mp4

> **Local file**: [`assets/demo_2.mp4`](assets/demo_2.mp4)

> **Audio recording note**: The screen recording software did not capture the notification sound for the `play_audio` tool call. The tool is fully functional — the server pushes `assets/notification.mp3` over the WebSocket and the browser plays it via an `<audio>` element.

---

## Status

| Requirement | Status |
|---|---|
| Streaming STT with partial transcripts | ✅ Deepgram Nova-2 |
| LLM with tool-calling (`play_audio`) | ✅ Groq Llama 3.3 70B |
| Streaming TTS audio output | ✅ Deepgram Aura WebSocket |
| Fully async & concurrency-safe | ✅ `asyncio.TaskGroup` |
| Dockerized, single-command startup | ✅ `docker compose up --build` |
| `/health` and `/metrics` endpoints | ✅ |
| Per-turn & per-session cost tracking | ✅ |
| End-to-end latency ≤ 2s | ⚠️ Validated at 2–4s (see Technical Notes) |

---

## 🚀 Build & Run Instructions

### Prerequisites
- Docker & Docker Compose, **or** Python 3.11+ with `uv`
- API keys for **Deepgram** and **Groq**

### 1. Configure Environment

Copy the template and fill in your keys:

```bash
cp .env.example .env
```

Edit `.env` — the required variables are:

```env
# Deepgram — used for both STT (Nova-2) and TTS (Aura)
DEEPGRAM_API_KEY="your_deepgram_api_key_here"

# Groq — accessed via the OpenAI-compatible SDK
OPENAI_API_KEY="your_groq_api_key_here"
OPENAI_BASE_URL="https://api.groq.com/openai/v1"
OPENAI_MODEL="llama-3.3-70b-versatile"
```

See [`.env.example`](.env.example) for all tunable parameters (model names, pricing constants, timeouts, sample rates, etc.).

### 2. Start the Service

```bash
docker compose up --build
```

For local development without Docker:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3. Connect the Client

Open your browser and navigate to:

```
http://localhost:8000/client/index.html
```

Click **CONNECT**, allow microphone access, and speak.

*Observability endpoints:*
- `GET /health` → `{"status": "ok", "active_sessions": N, ...}`
- `GET /metrics` → per-session and global latency + cost breakdown

---

## 🏗️ Streaming & Concurrency Model

Each WebSocket session (`/ws/talk`) creates a **session object** with four concurrent async tasks connected by `asyncio.Queue`:

```
Browser
  │
  │  PCM Int16 @ 16 kHz (binary frames)
  ▼
audio_receiver
  │  stt_queue
  ▼
stt_processor  ──── Deepgram Nova-2 WebSocket (streaming)
  │  llm_queue  ← gated on speech_final only (prevents duplicate LLM calls)
  ▼
llm_processor  ──── Groq Llama 3.3 70B  (sentence-chunked streaming)
  │               └─ play_audio tool → MP3 clip pushed directly to browser
  │  tts_queue
  ▼
tts_processor  ──── Deepgram Aura WebSocket (PCM 24 kHz streaming)
  │
  ▼
Browser  (PCM → AudioContext · MP3 → <audio> element)
```

- **`asyncio.TaskGroup`** cancels all sibling tasks on disconnect or error — no zombie API connections.
- **Queues** provide natural backpressure between stages.
- **Session isolation** — each session has its own queues and tasks; no shared mutable state.
- The browser receives explicit `audio_ready` JSON frames specifying content type before each binary stream:
  - `audio/pcm;rate=24000;channels=1;encoding=s16le` → Aura speech via `AudioContext`
  - `audio/mpeg` → tool audio clip via `<audio>` element

---

## ⚡ Techniques for ≤ 2s Latency

1. **Raw PCM over WebSockets** — avoids browser media-stack overhead vs. WebRTC.
2. **Server-side VAD & endpointing** — Deepgram handles turn detection (`endpointing=300ms`); no client-side VAD required.
3. **`speech_final` gating** — the LLM is only invoked on Deepgram's finalized utterance, preventing duplicate or premature generation.
4. **Sentence-level pipelining** — LLM tokens are buffered until a sentence boundary is detected, then dispatched to TTS while the LLM continues generating. TTS starts before the full response finishes.
5. **Persistent Deepgram Aura WebSocket** — reuses a single socket per session instead of opening a new REST request per turn.
6. **Groq LPU inference** — Groq's Language Processing Units provide ~270–400ms time-to-first-token for Llama 3.3 70B.

---

## 💡 Component Selection Rationale

### STT — Deepgram Nova-2
- Native streaming WebSocket API with server-side VAD and endpointing.
- Returns partial transcripts (shown live in the UI) plus `speech_final` events that gate the LLM.
- Measured latency to final transcript: ~100–250ms.
- **Cost**: $0.0043 / minute of audio.

### LLM — Groq Llama 3.3 70B (via OpenAI-compatible SDK)
- Groq's LPU hardware delivers ultra-low time-to-first-token (~270–400ms).
- The standard OpenAI Python SDK is used unchanged — `base_url` is pointed at Groq's endpoint.
- Supports native tool-calling; `play_audio` is registered as a function tool.
- Conversation history (last 10 turns) kept in-memory per session.
- **Cost**: $0.15 / 1M input tokens · $0.60 / 1M output tokens.

### TTS — Deepgram Aura (Streaming WebSocket)
- Streams PCM audio at 24 kHz over a persistent WebSocket per session.
- Each sentence chunk is sent as `SpeakV1Text` + `send_flush()` to minimise per-sentence delay.
- Supports barge-in via `send_clear()`.
- **Cost**: $0.015 / 1K characters synthesized.

---

## 💰 Cost Estimation Method

Costs are tracked per-turn and accumulated per-session via `app/metrics.py`:

| Stage | Metric | Rate |
|---|---|---|
| STT | Audio seconds → minutes | $0.0043 / min |
| LLM | Prompt + completion tokens (from `usage` field) | $0.15 / 1M in · $0.60 / 1M out |
| TTS | Characters sent to Aura | $0.015 / 1K chars |

All values are exposed at `GET /metrics` and shown in the browser telemetry panel in real time.
Pricing constants are configured in [`.env.example`](.env.example) and can be updated without code changes.

---

## 🛑 Failure Scenario — Browser Disconnect Mid-Stream

**Trigger**: Browser tab closed or network dropped while Deepgram Aura is streaming audio.

**Expected behavior**:
1. FastAPI detects `WebSocketDisconnect`.
2. The session's `asyncio.TaskGroup` raises `CancelledError` and cancels all four pipeline tasks.
3. Each task's `finally` block closes its external WebSocket (Deepgram STT and Aura).
4. Metrics collected up to the disconnect are finalized and remain readable at `GET /metrics`.
5. No zombie tasks or leaked API connections remain.

---

## 📝 Technical Notes

### Latency & Model Pivot
- **Original plan**: Gemini 2.0 Flash for the LLM stage, targeting ≤ 2s end-to-end.
- **Pivot**: Migrated to Groq (Llama 3.3 70B) via the OpenAI-compatible SDK due to API credit constraints. Groq's TTFT is comparable (~270–400ms).
- **Measured latency**: 2–4s in the stable build. Screen recording software added overhead visible in Demo 1 (5–6s on screen).
- **Edge TTS experiment**: A variant using Microsoft Edge TTS (no API key required) achieved ~1.5s in isolated tests (Demo 2) but was not stable enough for the final submission build.
- Sub-2s is achievable when services are co-located in the same cloud region.

### Audio Recording Caveat
The screen recording software did not capture the notification sound from the `play_audio` tool invocation. The tool is fully functional — the server reads `assets/notification.mp3` and pushes it over the WebSocket as an `audio/mpeg` binary frame, and the browser plays it through an `<audio>` element.

### `.env.example` & API Keys
The [`.env.example`](.env.example) file is the canonical reference for all configuration:
- `DEEPGRAM_API_KEY` — from [console.deepgram.com](https://console.deepgram.com)
- `OPENAI_API_KEY` — your **Groq** API key from [console.groq.com](https://console.groq.com)
- `OPENAI_BASE_URL` — must be `https://api.groq.com/openai/v1`
- `OPENAI_MODEL` — `llama-3.3-70b-versatile` (or any Groq model with tool-calling support)

Pricing constants (`DEEPGRAM_COST_PER_MINUTE`, `OPENAI_COST_PER_1M_INPUT_TOKENS`, etc.) in `.env.example` are pre-filled with current public rates and used directly by the `/metrics` cost calculator.

---

## 📁 Project Structure

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
│   ├── demo_1.mp4         # Demo video — stable build (5–6s latency)
│   ├── demo_2.mp4         # Demo video — experimental Edge TTS build (~1.5s)
│   └── notification.mp3   # Bundled audio clip for the play_audio tool
├── doc/
│   ├── adiiva_r.md        # Original assignment specification
│   ├── project_plan.md    # Phase-by-phase plan and architecture
│   ├── DEMO_PREP.md       # Demo video script and checklist
│   └── HANDOVER_PROMPT.md # Context document for AI-assisted development sessions
├── .env.example           # Configuration template — copy to .env and fill in keys
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```
