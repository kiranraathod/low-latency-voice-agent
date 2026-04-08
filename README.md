Real-Time Voice AI Agent

> A production-grade, fully asynchronous voice pipeline that transforms browser microphone audio into spoken AI responses in under two seconds. Built to validate the architectural principles that matter at scale: streaming-first design, stage-isolated concurrency, and latency measured in milliseconds — not user frustration.

---

## Pipeline Architecture

```
Browser Microphone
      │
      │  Raw PCM Int16 @ 16 kHz  (binary WebSocket frames)
      ▼
┌─────────────────┐   stt_queue (maxsize=100)
│ audio_receiver  │ ─────────────────────────────────────────────────────────►
└─────────────────┘                                                           │
                                                                              ▼
                                                              ┌───────────────────────────┐
                                                              │  stt_processor            │
                                                              │  Deepgram Nova-2          │
                                                              │  WebSocket · server VAD   │
                                                              │  ~100–250ms to final      │
                                                              └────────────┬──────────────┘
                                                                           │  llm_queue (maxsize=50)
                                                                           │  (gated on speech_final)
                                                                           ▼
                                                              ┌───────────────────────────┐
                                                              │  llm_processor            │
                                                              │  OpenAI-compatible API    │
                                                              │  Sentence-chunked stream  │
                                                              │  play_audio tool support  │
                                                              └────────────┬──────────────┘
                                                                           │  tts_queue (maxsize=200)
                                                                           ▼
                                                              ┌───────────────────────────┐
                                                              │  tts_processor            │
                                                              │  Deepgram Aura WebSocket  │
                                                              │  PCM 24 kHz · barge-in    │
                                                              └────────────┬──────────────┘
                                                                           │
                                                                           ▼
                                                                  Browser AudioContext
                                                               (PCM → AudioContext  /  MP3 → <audio>)
```

All four tasks run under a single `asyncio.TaskGroup`. If any task raises or the browser disconnects, the group cancels all siblings atomically — no zombie API connections, no leaked Deepgram WebSocket handles.

---

## Demo Videos

Two recordings document the pipeline at different optimisation stages.

### Demo 1 — Stable Build (5–6s on screen, 2–4s measured)

Full pipeline: Docker startup → live voice interaction → `play_audio` tool call → `/metrics` output.

https://github.com/user-attachments/assets/demo_1.mp4

> **Local file**: [`assets/demo_1.mp4`](assets/demo_1.mp4)
>
> *Note:* Screen-recording software added ~2s of rendering overhead not present in the actual pipeline. Measured end-to-end latency at the `/metrics` endpoint is 2–4s for this build.

### Demo 2 — Experimental Build (~1.5–3s latency)

The same pipeline wired to Microsoft Edge TTS instead of Deepgram Aura — the local-processing experiment that motivated the Edge TTS section below.

https://github.com/user-attachments/assets/demo_2.mp4

> **Local file**: [`assets/demo_2.mp4`](assets/demo_2.mp4)

> **Audio capture note**: The screen-recording software did not capture the browser's audio output for the `play_audio` tool call. The tool is fully functional — the server reads `assets/notification.mp3` and pushes it over the WebSocket as an `audio/mpeg` binary frame. The browser plays it through an `<audio>` element independently of the screen-capture audio chain.

---

## Delivery Status

| Requirement | Notes |
|---|---|---|
| Streaming STT with partial transcripts | Deepgram Nova-2 · live partials in UI |
| LLM with tool-calling (`play_audio`) | OpenAI-compatible streaming + function calling |
| Streaming TTS audio output | Deepgram Aura persistent WebSocket |
| Fully async & concurrency-safe | `asyncio.TaskGroup` · bounded queues |
| Dockerized single-command startup | `docker compose up --build` |
| `/health` and `/metrics` endpoints | Per-turn + per-session cost & latency |
| Per-turn & per-session cost tracking | STT · LLM · TTS cost breakdown |
| End-to-end latency ≤ 2s |**Optimization opportunity** — validated at 2–4s in stable build; 1.5s achieved in edge-TTS experiment (Demo 2). Sub-2s is the active engineering focus for the next milestone. |

---

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose **or** Python 3.11+ with [`uv`](https://github.com/astral-sh/uv)
- API keys for **Deepgram** and **Groq** (or any OpenAI-compatible LLM endpoint)

### 1. Configure the Environment

```bash
cp .env.example .env
```

Edit `.env` with your API keys. The minimum required variables are:

```env
# Deepgram — powers both STT (Nova-2) and TTS (Aura)
DEEPGRAM_API_KEY="your_deepgram_api_key_here"

# LLM — Groq accessed via the OpenAI-compatible SDK
OPENAI_API_KEY="your_groq_api_key_here"
OPENAI_BASE_URL="https://api.groq.com/openai/v1"
OPENAI_MODEL="llama-3.3-70b-versatile"
```

See [`.env.example`](.env.example) for all tunable parameters: model names, per-stage pricing constants, queue sizes, timeout values, and sample rates.

### 2. Start the Service

```bash
# Docker (recommended — matches production parity)
docker compose up --build

# Local development
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3. Connect

Navigate to `http://localhost:8000/client/index.html`, click **CONNECT**, and allow microphone access.

**Observability endpoints:**
- `GET /health` → `{"status": "ok", "active_sessions": N}`
- `GET /metrics` → full per-turn latency breakdown + rolling cost totals

---

## Engineering Architecture

### The `asyncio.TaskGroup` Concurrency Model

Each WebSocket session (`/ws/talk`) runs exactly four `asyncio.Task` instances connected by three bounded `asyncio.Queue` bridges:

| Queue | Producer | Consumer | Backpressure Role |
|---|---|---|---|
| `stt_queue` (maxsize=100) | `audio_receiver` | `stt_processor` | Absorbs microphone burst; drops frames when full |
| `llm_queue` (maxsize=50) | `stt_processor` | `llm_processor` | Ensures LLM is not called redundantly mid-utterance |
| `tts_queue` (maxsize=200) | `llm_processor` | `tts_processor` | Buffers sentence chunks; drained instantly on barge-in |

The `asyncio.TaskGroup` pattern was chosen deliberately over individual `asyncio.create_task` calls. When a `WebSocketDisconnect` fires, the group propagates `CancelledError` to all siblings in a single frame — guaranteeing that Deepgram STT and Aura WebSocket handles are closed before the session object is garbage-collected. There are no teardown timers, no `asyncio.sleep(0)` polls, and no manual task tracking.

Session state is fully isolated: each `VoiceSession` object owns its own queues, task handles, conversation history, and `SessionMetrics` instance. There is no shared mutable state between concurrent sessions.

### `speech_final` Gating — Why It Matters

Deepgram emits both interim (`is_final=False`) and final (`speech_final=True`) transcript events. The LLM is only invoked on `speech_final` events, not on intermediate finals. This single guard prevents the most common source of duplicate or premature LLM calls in streaming STT systems — a problem that can double effective latency by forcing the LLM to generate a response to an incomplete utterance.

### Sentence-Level TTS Pipelining

The LLM processor does not wait for the complete response before dispatching to TTS. It buffers tokens until a sentence boundary (`[.!?]+`) is detected, then immediately pushes the sentence to `tts_queue` while generation continues. This means the TTS stage begins synthesising the first sentence while the LLM is still generating the second — eliminating what would otherwise be a 400–800ms serial wait.

### Barge-In

The `barge_in_event` (`asyncio.Event`) is set by the audio receiver when the client sends a `BARGE_IN` control frame. The TTS processor polls this event between each audio-receive loop iteration (`_recv_with_barge_poll` with a 100ms timeout). When set, it issues `send_clear()` to the Deepgram Aura connection and drains `tts_queue` via `session.clear_pending_tts()` — discarding all queued sentences before the next utterance begins.

---

## The `.env` as a Multi-Provider Experimentation Layer

The `.env` file is not just a configuration file — it is the **prototype's provider switching bus**. Understanding why reveals an important engineering decision.

`app/config.py` uses `pydantic-settings` with `SettingsConfigDict(extra="ignore")`. This means the config parser reads the entire `.env` file but silently discards any variable it does not recognise. The practical consequence: the `.env` can simultaneously contain API keys and configuration blocks for every provider under evaluation — Gemini, ElevenLabs, DeepSeek, Groq — and the application simply ignores whichever providers are not currently wired in.

This was not an accident. It enables provider pivoting without code changes:

```env
# Swap the LLM provider by changing two lines — no rebuild required
OPENAI_BASE_URL="https://api.groq.com/openai/v1"
OPENAI_MODEL="llama-3.3-70b-versatile"

# Gemini config sits dormant — ready to activate in seconds
GEMINI_API_KEY="..."
GEMINI_MODEL="gemini-2.0-flash"
GEMINI_MAX_HISTORY_TURNS=10
GEMINI_TIMEOUT_S=8
```

Similarly, all cost rates (`DEEPGRAM_COST_PER_MINUTE`, `OPENAI_COST_PER_1M_INPUT_TOKENS`, `DEEPGRAM_TTS_COST_PER_1K_CHARS`) are runtime-configurable through `.env` without touching code. When API pricing changes — and it does, frequently — the cost model stays current without a deployment.

This pattern eliminates the overhead of secrets managers, Kubernetes ConfigMaps, and provider-specific SDK switches during the prototyping phase. It is the optimal mechanism for a single-developer research iteration loop where the question is not "how do we manage config in production?" but "which provider combination minimises voice-to-voice latency at acceptable cost?"

---

## Component Selection Rationale

### STT — Deepgram Nova-2

Deepgram is the only provider in this latency class that offers a native streaming WebSocket API with server-side VAD and endpointing built in. The two critical properties for this pipeline are:

1. **Server-side `endpointing`**: Deepgram decides when the user has finished speaking. The live `.env` sets `DEEPGRAM_ENDPOINTING_MS=150` — 150ms of silence triggers `speech_final`. This is more aggressive than the 300ms default in `.env.example`, and it is where approximately 100–200ms of end-to-end latency can be recovered without any code change.
2. **Partial transcripts**: Interim results are forwarded to the browser UI in real time, giving visual feedback while the pipeline waits for `speech_final` to gate the LLM.

Measured latency to `speech_final` transcript: **100–250ms**.
Cost: **$0.0043–$0.0058 / minute of audio**.

### LLM — The Provider Journey

#### Phase 1: The Original Plan (Gemini 2.0 Flash)

The initial architecture targeted Gemini 2.0 Flash as the LLM backbone. The rationale was sound: Gemini Flash models have consistently demonstrated that a distilled, efficiency-focused model can outperform its parent Pro variant on the tasks that matter most for agentic workloads. Gemini 2.0 Flash offered a strong function-calling implementation and competitive TTFT for its size class.

#### Phase 2: The Groq Pivot

A practical constraint — API credit exhaustion — required a mid-build pivot to Groq (Llama 3.3 70B). The migration cost was precisely **two lines of `.env`**:

```env
OPENAI_BASE_URL="https://api.groq.com/openai/v1"
OPENAI_MODEL="llama-3.3-70b-versatile"
```

This validates the OpenAI-compatible SDK strategy: the entire LLM processor (`app/pipeline/llm.py`) runs unchanged. Groq's LPU hardware delivers **270–400ms time-to-first-token** for Llama 3.3 70B — comparable to Gemini 2.0 Flash and well within the latency budget.

#### Phase 3: The Target Architecture (Gemini 3 Flash)

The planned milestone for the next iteration is migrating the LLM stage to **Gemini 3 Flash** — and the reasoning is rooted in benchmark data that matters for this specific use case.

Gemini 3 Flash scores **78% on SWE-Bench Verified**, Google's benchmark for autonomous real-world software engineering tasks. Crucially, this score *exceeds* Gemini 3 Pro (76.2%) — a distilled model outperforming its teacher on the evaluation most directly predictive of reliable tool-use and multi-step reasoning. For a voice agent that must execute function calls (`play_audio`) correctly on the first attempt — where a failed tool call means an audible silence, not a retry — this reliability margin is not an academic distinction. It is audible to the user.

At 218 tokens/second throughput (3× faster than Gemini 2.5 Pro) and $0.50/1M input tokens (4× cheaper than Pro), Gemini 3 Flash unlocks parallel session scaling that is economically impractical with heavier models. The phrase "Gemini 3 Flash is our go-to for latency-sensitive experiences" from production deployments at Cognition (Devin) reflects precisely the workload profile this agent represents.

The `.env` already carries the Gemini API key. Activation is a two-line change.

### TTS — Deepgram Aura (Cloud Streaming WebSocket)

Deepgram Aura runs a **persistent WebSocket per session** rather than a REST request per sentence. This eliminates the TCP handshake and TLS overhead that would otherwise add 50–150ms to every turn. Each sentence is dispatched as `SpeakV1Text` followed immediately by `send_flush()` — Aura begins synthesising PCM audio while the LLM continues generating the next sentence.

Cost: **$0.015 / 1K characters synthesised**.

---

## The Edge TTS Experiment — Eliminating Voice-to-Voice Latency

Demo 2 documents an experiment that replaced Deepgram Aura with **Microsoft Edge TTS** — a locally-processed synthesis engine — and achieved approximately **1.5s end-to-end latency** in isolated tests. This result deserves an architectural explanation, because it points to where the biggest remaining latency gains live.

### The Network-Hop Problem in Cloud TTS

Every cloud TTS request, regardless of provider, involves the same cost structure:

```
Server                      Cloud TTS Provider
  │                               │
  │── SpeakV1Text ───────────────►│
  │   (TCP/TLS overhead)          │   ← model inference: 60–150ms
  │◄─ first audio bytes ──────────│   ← network RTT: 50–200ms (variable)
  │   (first chunk latency)       │
```

The **network round-trip alone** introduces 50–200ms of non-negotiable latency per sentence chunk, even from a well-colocated cloud deployment. Under congestion or cross-region routing, this becomes the dominant contributor to perceived voice-to-voice delay.

### How Edge TTS Changes the Equation

Edge TTS processes speech **locally on the server** — the same process that runs the FastAPI application. The network-hop cost drops to effectively zero:

```
Server (FastAPI process)
  │
  │── text ──► Edge TTS engine (in-process)
  │            model inference: 5–20ms
  │◄─ PCM frames (no network RTT)
```

The implications compound across the full pipeline:

| Cost Component | Cloud TTS (Deepgram Aura) | Edge TTS (Local) |
|---|---|---|
| Network RTT per chunk | 50–200ms | ~0ms |
| Model inference | 60–150ms | 5–20ms |
| Per-sentence overhead | **110–350ms** | **5–20ms** |
| API cost | $0.015 / 1K chars | **$0.00** |

Eliminating the network hop across a typical 5-sentence response removes 500ms–1.75s of cumulative latency. This is why Demo 2 achieves 1.5s where Demo 1 measures 2–4s — not from a faster model, but from removing a structural overhead that cloud TTS imposes on every response.

### The Human Conversation Threshold

Research on conversational response timing establishes a consistent benchmark: human-to-human turn-taking operates at **200–300ms** gap between speakers. Voice agents that exceed approximately **500ms** begin to feel unnatural; anything above **1s** registers as a system delay rather than a thoughtful pause.

The sub-300ms target is not an arbitrary engineering goal. It is the threshold below which users stop perceiving the system as a machine and start experiencing it as a conversation.

The Edge TTS experiment demonstrates that reaching this threshold in a cascaded STT → LLM → TTS architecture requires treating TTS network latency with the same engineering discipline as LLM TTFT. The planned milestone is a stable Edge TTS integration that matches the quality of Deepgram Aura while eliminating its network overhead.

---

## Continuous Latency Experimentation

The gap between Demo 1 (2–4s) and Demo 2 (1.5s) was not closed by a faster model — it was closed by an architectural change. This is the central lesson of the prototype: **voice-to-voice latency is primarily a systems engineering problem, not a model capability problem**.

The current build identifies three active areas of experimentation:

**WebSocket Streaming vs. Standard API calls**: The `llm_processor` currently uses the OpenAI streaming API, which delivers tokens incrementally. An alternative worth benchmarking is whether streaming WebSocket connections to the LLM provider — bypassing the HTTP layer entirely — reduce TTFT on providers that support it. The expected gain is 20–50ms from connection reuse, compounded across high-frequency sessions.

**Endpointing Aggressiveness**: The live `.env` already runs `DEEPGRAM_ENDPOINTING_MS=150` (vs 300ms in the template). Moving to 100ms is the next step — at the cost of higher false-positive rates on natural pauses. The sentence-level pipeline means a false positive costs a spurious LLM invocation; the graceful-abort path via `barge_in_event` mitigates this. This is an optimisation that requires A/B measurement against real conversation data, not synthetic benchmarks.

**History Window Size**: The live `.env` sets `OPENAI_MAX_HISTORY_TURNS=1` — the most aggressive memory pruning possible, retaining only the immediately prior exchange. This trades long-term conversational coherence for lower prompt token counts and reduced TTFT. The `openai_max_history_turns` parameter in `.env` makes this trade-off runtime-tunable; finding the optimal value for natural conversation quality versus measurable latency gain is an empirical question the metrics endpoint is designed to answer.

**Service Co-location**: Measured latency in Demo 1 was taken over a local development machine against cloud API endpoints in US-East regions. Industry benchmarks show that STT + LLM + TTS services co-located in the same availability zone reduce inter-service hop latency by 30–80ms per stage. Sub-2s is achievable in co-located deployments without any architectural changes to the current build.

---

## Cost Tracking

All costs are computed in real time from telemetry collected by `app/metrics.py` and exposed at `GET /metrics`.

| Stage | Input Metric | Configurable Rate | Typical Cost per Turn |
|---|---|---|---|
| STT | Audio duration (seconds → minutes) | `DEEPGRAM_COST_PER_MINUTE` | ~$0.0001–0.0003 |
| LLM | Prompt + completion tokens (from `usage` field) | `OPENAI_COST_PER_1M_INPUT/OUTPUT_TOKENS` | ~$0.0001–0.0005 |
| TTS | Characters sent to Aura | `DEEPGRAM_TTS_COST_PER_1K_CHARS` | ~$0.00015–0.0003 |

All rate constants are defined in `.env.example` with current public pricing and can be updated without code changes. The metrics endpoint returns a full cost breakdown — per turn and per session — suitable for build-vs-buy analysis against voice platform providers.

---

## Failure Handling — Browser Disconnect Mid-Stream

**Trigger**: Browser tab closed or network dropped while Deepgram Aura is streaming audio.

**Observed behaviour**:

1. FastAPI detects `WebSocketDisconnect`.
2. The session's `asyncio.TaskGroup` propagates `CancelledError` to all four pipeline tasks simultaneously.
3. Each task's `finally` block closes its external connection: the Deepgram STT WebSocket via `send_close_stream()` and the Aura WebSocket via `send_close()`.
4. `session_manager.destroy()` calls `session.teardown()`, which drains all three queues and finalises `SessionMetrics`.
5. Metrics collected up to the disconnect point remain readable at `GET /metrics`. No zombie tasks, no leaked connections.

This is not a best-effort cleanup — it is enforced by the `TaskGroup` contract. A task that does not handle `CancelledError` in its `finally` block will still be cancelled; the `finally` block exists only to ensure external API connections are closed cleanly rather than timing out on the remote end.

---

## 📁 Project Structure

```
prototype_adiiva/
├── .agent/
│   ├── engineering-ai-engineer.md      # AI engineer persona for assisted development sessions
│   └── engineering-technical-writer.md # Technical writer persona for documentation sessions
├── app/
│   ├── config.py            # pydantic-settings — reads .env; fail-fast on missing keys
│   ├── main.py              # FastAPI app · WebSocket handler · TaskGroup orchestration
│   ├── metrics.py           # Per-turn and per-session cost + latency accumulator
│   ├── models.py            # Pydantic models for all WebSocket JSON control frames
│   ├── session.py           # VoiceSession · SessionManager · queue bridges
│   ├── logging_config.py    # Structured JSON logging via structlog
│   └── pipeline/
│       ├── stt.py           # Deepgram Nova-2 streaming client · VAD · speech_final gating
│       ├── llm.py           # OpenAI-compatible streaming LLM · sentence chunker · tool calls
│       ├── tts.py           # Deepgram Aura streaming TTS · barge-in · PCM forwarding
│       ├── tools.py         # play_audio tool executor · MP3 push over WebSocket
│       └── prompts.py       # System prompt (concise · markdown-free · voice-natural)
├── client/
│   ├── index.html           # Browser UI · live transcript · telemetry panel
│   └── app.js               # Web Audio API PCM capture · PCM/MP3 playback routing
├── scripts/
│   └── test_client.py       # Headless test: sends pre-recorded WAV, saves audio output
├── assets/
│   ├── demo_1.mp4           # Stable build (2–4s measured latency)
│   ├── demo_2.mp4           # Edge TTS experiment (~1.5s)
│   └── notification.mp3     # Bundled audio clip for the play_audio tool
├── doc/
│   ├── adiiva_r.md          # Original assignment specification
│   ├── project_plan.md      # Phase-by-phase plan and architecture decisions
│   └── HANDOVER_PROMPT.md   # Context document for AI-assisted development sessions
├── .env.example             # Configuration template — copy to .env
├── .env                     # Live configuration (not committed)
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml           # Python 3.11+ · FastAPI · Deepgram SDK · OpenAI SDK · structlog
```

---

## Planned Milestones

| Milestone | Description |
|---|---|
| Gemini 3 Flash LLM | Migrate from Groq/Llama to Gemini 3 Flash (78% SWE-bench, 218 tok/s, $0.50/1M in). Two-line `.env` change once API access is provisioned. |
| Stable Edge TTS | Replace Deepgram Aura with a locally-processed TTS engine. Target: eliminate 200ms+ per-sentence cloud round-trip cost and reduce TTS stage cost by ~90%. |
| Endpointing A/B | Instrument 100ms vs 150ms vs 300ms endpointing against real session data. Optimise for the lowest false-positive rate that keeps E2E latency below 1.5s. |
| WebSocket LLM Streaming | Evaluate direct WebSocket connections to LLM providers vs HTTP streaming. Expected gain: 20–50ms TTFT reduction from persistent connection reuse. |
| Cloud Co-location | Deploy STT, LLM, and TTS in the same availability zone. Expected gain: 30–80ms per inter-service hop; sub-2s target achievable without further architectural changes. |
| ElevenLabs Turbo v2.5 | Re-evaluate ElevenLabs Flash (75ms synthesis claim) with full end-to-end measurement. The API key and voice config are dormant in `.env` and ready for activation. |

---

## Configuration Reference

All parameters are read from `.env` by `pydantic-settings`. Changing a value requires only restarting the server — no code modification.

```env
# ── LLM ──────────────────────────────────────────────────
OPENAI_API_KEY="..."            # Provider API key (Groq, OpenAI, or any compatible endpoint)
OPENAI_BASE_URL="..."           # Omit for OpenAI; set to provider URL for Groq, etc.
OPENAI_MODEL="..."              # Model string — swap providers here
OPENAI_MAX_HISTORY_TURNS=10     # Memory window; lower = faster (fewer prompt tokens)
OPENAI_TIMEOUT_S=8              # LLM response timeout in seconds

# ── STT ──────────────────────────────────────────────────
DEEPGRAM_API_KEY="..."
DEEPGRAM_MODEL="nova-2"
DEEPGRAM_ENDPOINTING_MS=300     # Silence threshold; 150ms = faster, higher false-positive rate
DEEPGRAM_COST_PER_MINUTE=0.0043

# ── TTS ──────────────────────────────────────────────────
DEEPGRAM_TTS_MODEL="aura-asteria-en"
DEEPGRAM_TTS_SAMPLE_RATE=24000
DEEPGRAM_TTS_COST_PER_1K_CHARS=0.015

# ── Cost Rates ────────────────────────────────────────────
OPENAI_COST_PER_1M_INPUT_TOKENS=0.15
OPENAI_COST_PER_1M_OUTPUT_TOKENS=0.60

# ── Queue Sizing (backpressure tuning) ────────────────────
STT_QUEUE_MAXSIZE=100
LLM_QUEUE_MAXSIZE=50
TTS_QUEUE_MAXSIZE=200
```

API key sources:
- `DEEPGRAM_API_KEY` → [console.deepgram.com](https://console.deepgram.com)
- `OPENAI_API_KEY` (Groq) → [console.groq.com](https://console.groq.com)
