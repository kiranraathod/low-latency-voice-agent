# OBJECTIVE

Prototype a low-latency, real-time voice AI agent with end-to-end latency ≤ 2 seconds (audio input
→ audio output) that:
● Accepts streamed audio input
● Performs streaming STT (partial transcripts early)
● Routes text through an LLM with tool-calling
● Executes a real audio playback tool
● Produces TTS audio output with minimal delay
● Is fully async, concurrency-safe, and Dockerized
● Exposes a minimal API extensible to production

# PART 1 – CORE SYSTEM

Build a Python service that:
● Streams audio input (real or simulated)
● Emits partial transcripts via STT
● Sends transcripts to an LLM with tool-calling support
● Executes the required tool:
○ Play Audio: triggers immediate audio playback to the client as a side-effect
● Streams TTS audio output with minimal delay
● Logs per-stage latency (STT, LLM, tool, TTS)
● Logs and exposes estimated cost per turn and per conversation (STT, LLM, TTS)
● Handles timeouts, disconnects, and cancellations correctly

# PART 2 – API (FASTAPI)

**Required Endpoints**
WebSocket: /ws/talk
● Bidirectional streaming (audio + JSON frames)
● Supports multiple concurrent sessions
Health Check: GET /health
{ "status": "ok" }
Metrics: GET /metrics
● Active sessions
● Average end-to-end latency


```
● Estimated cost per turn and per conversation, broken down by STT / LLM / TTS (logged and
exposed)
```
# PART 3 – DEPLOYMENT

```
● Provide a Dockerfile with all dependencies
● App must run with one command after cloning
● No platform-specific dependencies
● Must run on Windows, macOS, Linux
```
# PART 4 – OBSERVABILITY & COST VISIBILITY

Include:
● Structured logging
● Clean session teardown on disconnect
● Logged and exposed per-turn and per-conversation cost estimation

# PART 5 – DOCUMENTATION

README.md must include:
● Build & run instructions
● Streaming and concurrency model
● Techniques used to maintain ≤ 2s end-to-end latency
● Rationale for STT, LLM, and TTS choices
● Method used to estimate, log, and expose cost per turn and per conversation
● One failure scenario and expected system behavior

# DEMO VIDEO (REQUIRED)

Provide a short demo video (3–5 minutes) showing:
● Service startup via Docker
● A live or simulated voice interaction
● Evidence of streaming behavior (partial STT and early TTS)
● Invocation of the audio playback tool
● /metrics output including latency and cost
Requirements:
● Screen recording with audio
● Single take preferred
● Link included in README.md


# DELIVERABLES

```
● Python code for the streaming voice agent
● FastAPI service with /ws/talk, /health, /metrics
● Dockerfile
● README with design rationale
● Demo video link
```
# EVALUATION CRITERIA

```
● End-to-end latency ≤ 2s (primary)
● Streaming & async design quality
● Correctness under concurrency
● Tool-calling implementation quality
● Cost awareness and estimation clarity
● Production readiness & observability
● Code quality and maintainability
```

