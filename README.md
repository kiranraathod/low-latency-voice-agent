# Real-Time Voice AI Agent Prototype

A fully asynchronous, low-latency voice AI agent built to keep end-to-end latency at **<= 2 seconds** for short turns. The pipeline streams microphone audio into Deepgram STT, routes finalized utterances through Groq's Llama 3.3 via the OpenAI SDK, and streams assistant speech back through Deepgram Aura.

## Build & Run

### Prerequisites
- Docker and Docker Compose, or Python 3.11 with `uv`
- API keys for Deepgram and Groq

### Environment
Create a `.env` file from the example:

```bash
cp .env.example .env
```

Required keys:
- `DEEPGRAM_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL=https://api.groq.com/openai/v1`
- `OPENAI_MODEL=llama-3.3-70b-versatile`

### Start the service

```bash
docker compose up --build
```

For local development:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Client
Open:

`http://localhost:8000/client/index.html`

Observability endpoints:
- `GET /health`
- `GET /metrics`

## Streaming And Concurrency Model

Each WebSocket session creates four concurrent async tasks connected by `asyncio.Queue`:

1. `audio_receiver`: accepts browser PCM frames and control frames.
2. `stt_processor`: streams audio into Deepgram Nova-2 and forwards finalized utterances to the LLM queue.
3. `llm_processor`: calls Groq's Llama 3.3 in streaming mode, emits sentence chunks, and handles tool calls inline.
4. `tts_processor`: streams sentence chunks into Deepgram Aura and forwards PCM audio back to the browser.

The browser client receives explicit `audio_ready` frames so it can route:
- Aura assistant speech as `audio/pcm;rate=24000;channels=1;encoding=s16le`
- Tool audio clips as `audio/mpeg`

`asyncio.TaskGroup` handles teardown so disconnects and failures cancel sibling tasks cleanly.

## Techniques Used To Maintain <= 2s Latency

1. Raw PCM over WebSockets avoids heavier browser transport stacks.
2. Deepgram endpointing handles turn detection server-side.
3. The LLM is sentence-chunked, so TTS starts before the full assistant response finishes.
4. Deepgram Aura uses a persistent streaming socket instead of a per-turn REST TTS request.
5. Groq provides low time-to-first-token for the LLM stage.
6. STT only triggers the LLM on Deepgram `speech_final`, avoiding duplicate generations.

## Component Choices

- **STT**: Deepgram Nova-2
  - Fast streaming transcription with endpointing and partials.
- **LLM**: Groq Llama 3.3 70B via the OpenAI SDK
  - Very fast first-token latency with an OpenAI-compatible API.
- **TTS**: Deepgram Aura
  - Streaming WebSocket TTS with low-latency PCM output that fits the real-time pipeline.

## Cost Estimation

Costs are tracked per turn and per session:
- **STT**: audio seconds converted to Deepgram per-minute pricing
- **LLM**: prompt and completion tokens from the OpenAI-compatible usage fields
- **TTS**: synthesized characters converted to Deepgram Aura per-1K-character pricing

All values are exposed through `GET /metrics` and surfaced in the browser telemetry panel.

## Failure Scenario

If the browser disconnects while Aura is still streaming:
- FastAPI detects the WebSocket disconnect.
- The session `TaskGroup` cancels all pipeline tasks.
- The Deepgram STT and TTS sockets are closed during teardown.
- Metrics collected so far remain available in the completed session summary.

## Demo Video

Demo video link: `TBD`

The final demo should show:
- Docker startup
- A live or simulated voice interaction
- Partial STT and early TTS
- A tool invocation that plays the bundled notification audio
- `/metrics` output with latency and cost
