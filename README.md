# Real-Time Voice AI Agent Prototype

A fully asynchronous, low-latency voice AI agent that streams audio end-to-end. Built to maintain an overall system latency of **≤ 2 seconds**.

## 🚀 Build & Run Instructions

### Prerequisites
- Docker & Docker Compose
- API Keys for Deepgram and Groq (free tiers available)

### 1. Configure Environment
Create a `.env` file from the example:
```bash
cp .env.example .env
```
Ensure you fill out the following keys in your `.env`:
- `DEEPGRAM_API_KEY` — [console.deepgram.com](https://console.deepgram.com)
- `OPENAI_API_KEY` — Set to your Groq key from [console.groq.com](https://console.groq.com)
- `OPENAI_BASE_URL` — `https://api.groq.com/openai/v1`
- `OPENAI_MODEL` — `llama-3.3-70b-versatile`
- `ELEVENLABS_API_KEY` — Required in config but TTS uses Edge TTS (free, no key needed)

### 2. Start the Service
Using Docker Compose, the entire stack starts with a single command:
```bash
docker compose up --build
```

### 3. Connect the Client
Open your browser and navigate to:
[http://localhost:8000/client/index.html](http://localhost:8000/client/index.html)
Click **CONNECT** and provide microphone permissions to start talking. 

*To verify server health and metrics directly, you can access:*
- `GET /health`
- `GET /metrics`

---

## 🏗️ Streaming & Concurrency Model

The backend leverages a purely asynchronous, concurrent architecture using Python 3.11's `asyncio` and `TaskGroup`:
- **WebSocket Session Lifecycle**: Each client connects via WebSocket, acting as a single `VoiceSession`.
- **4 Concurrent Tasks**:
  1. **Receiver**: Listens to WebSocket binary frames (audio) & text frames (control), forwarding PCM data to the STT queue.
  2. **STT Processor**: Pulls PCM data and streams it to Deepgram via an active WebSocket. Only triggers LLM on `speech_final` events to prevent duplicate calls. Pushes transcript results to the LLM queue.
  3. **LLM Processor**: Takes final transcripts and invokes Groq's Llama 3.3 70B via the OpenAI SDK in streaming mode. Chunks tokens at sentence boundaries and drops them into the TTS queue. Handles tool execution inline. Sends `LLM_CHUNK` frames to the client for real-time text display.
  4. **TTS Processor**: Pushes sentences to Microsoft Edge TTS (free, no API key required), immediately sending MP3 audio chunks directly back to the client WebSocket.
- **Data Transport**: All tasks communicate via thread-safe `asyncio.Queue` buffers. This naturally provides backpressure handling.
- **Teardown**: Any failure propagates automatically via `asyncio.TaskGroup`, cleanly draining queues and cancelling sibling tasks to prevent resource leaks.

---

## ⚡ Techniques for ≤ 2s Latency Budget

To guarantee sub-2-second Time-To-First-Audio (TTFA) end-to-end latency:
1. **Raw PCM Streams**: By avoiding WebRTC and using binary WebSockets (linear16 PCM), we skip heavy browser mux/demux, lowering transport latency.
2. **Server-Side VAD (Deepgram endpointing)**: We configure `endpointing=1500ms`. Deepgram automatically acts as our Voice Activity Detector without requiring client-side intelligence.
3. **Sentence Pipelining (The Core Technique)**: We do **not** wait for the entire LLM response to complete. Tokens are accumulated until a natural boundary (., ?, !). The moment a sentence completes, it is dispatched to TTS while the LLM continues generating in parallel.
4. **WebSocket-Only External Endpoints**: Deepgram is integrated exclusively via persistent WebSockets, bypassing the connection handshake overhead that REST APIs incur per turn.
5. **Ultra-Fast LLM Inference (Groq)**: Groq's custom LPU hardware delivers ~270ms TTFT and ~300 tokens/sec, dramatically reducing the LLM bottleneck.
6. **Speech-Final Gating**: We only trigger LLM calls on Deepgram's `speech_final` event (not every `is_final`), preventing duplicate API calls that waste latency and hit rate limits.

---

## 💡 Component Selection Rationale

- **STT**: **Deepgram Nova-2**
  *Why*: Currently the industry leader in streaming transcription speed. It offers a highly tunable WebSocket API with built-in VAD (endpointing), which removes complexity from our client logic.
- **LLM**: **Groq Llama 3.3 70B** (via OpenAI SDK)
  *Why*: Groq's custom LPU hardware delivers the fastest inference available (~270ms TTFT). Llama 3.3 70B provides strong reasoning, tool-calling support, and runs on Groq's free tier. The OpenAI-compatible API means zero code changes if swapping providers.
- **TTS**: **Microsoft Edge TTS**
  *Why*: Completely free with no API key required. Provides high-quality neural voices (en-US-AriaNeural) with competitive latency. No rate limits or account restrictions, making it ideal for prototyping and demos.

---

## 💰 Cost Estimation Method

We instrument the application to compute costs continuously based on exact usage logic inside the `metrics.py` aggregator layer:
- **STT (Deepgram)**: Measured by audio duration in seconds. Cost logic: `(audio_duration_secs / 60) * $0.0043`.
- **LLM (Groq)**: Measured by precise token usage tracked via the OpenAI SDK's `stream_options={"include_usage": True}`. On Groq's free tier, actual cost is $0.00 but we track token volumes for observability. Cost rates are configurable via `OPENAI_COST_PER_1M_INPUT_TOKENS` and `OPENAI_COST_PER_1M_OUTPUT_TOKENS`.
- **TTS (Edge TTS)**: Measured by character length of synthesized text. Edge TTS is free ($0 cost), but character counts are tracked for comparison against paid alternatives.
- **Observability**: Available per turn, per session, and globally via `GET /metrics`. The Web Client dynamically polls this endpoint and projects total session cost directly onto the UI.

---

## 🛑 Failure Scenarios & System Behavior

**Scenario: User abruptly closes the browser tab while TTS is streaming audio back.**
- **Expected Behavior**:
  1. FastAPI detects `WebSocketDisconnect`.
  2. The `TaskGroup` catches the disconnect exception. 
  3. The `TaskGroup` immediately cancels all four background tasks.
  4. Specifically, the pending LLM stream is aborted (saving token cost) and the active Edge TTS synthesis is cancelled.
  5. The `session_manager` logs the final telemetry and cost summary before tearing down the tracking structures automatically without memory leaks.

---

## 📹 Demo Video
*Link to demo video will be provided upon recording.*
