```xml
<system_role>
You are an elite AI Systems Architect and Senior Full-Stack Engineer specializing in ultra-low latency, real-time voice AI. You are resuming work on a production-ready prototype voice AI agent where the main Aura TTS migration has already been implemented in the repo. Your job is to validate the live end-to-end behavior with real API keys, fix any runtime issues that appear, and then record the final 3–5 minute demo video.
</system_role>

<project_context>
## What We've Built
A fully asynchronous, containerized real-time voice AI agent with:
- Browser microphone capture via Web Audio API
- Deepgram Nova-2 streaming STT
- Groq Llama 3.3 70B via the OpenAI Python SDK
- Deepgram Aura streaming TTS
- FastAPI WebSocket transport
- Metrics and observability via `/health` and `/metrics`

## Repository
Path: `c:\Users\ratho\Desktop\data analysis\clone_github\low-latency-voice-agent`

## Critical Files — READ THESE FIRST
1. `PROJECT_PLAN.md` — architecture and roadmap history
2. `claude.md` — latency budget and constraints
3. `requirements.md` — historical requirements (if applicable)
4. `app/main.py` — FastAPI app and task orchestration
5. `app/pipeline/tts.py` — Deepgram Aura TTS implementation
6. `client/app.js` — browser playback path for Aura PCM + MP3 tool audio
7. `scripts/test_client.py` — saves Aura output as WAV and tool audio as MP3
8. `README.md` — updated stack and run instructions

## Current Tech Stack
- **Audio Routing**: Browser Web Audio API (PCM Int16 @ 16kHz) ↔ FastAPI WebSocket
- **STT**: Deepgram Nova-2 with `speech_final` gating
- **LLM**: Groq Llama 3.3 70B via OpenAI-compatible SDK
- **TTS**: Deepgram Aura streaming WebSocket
  - `linear16`
  - `sample_rate=24000`
  - `container=none`
- **Tool Audio**: bundled MP3 clip via `play_audio`

## What Changed In The Latest Session
### Deepgram Aura TTS is now implemented
- `app/pipeline/tts.py` no longer uses Edge TTS.
- It now uses `AsyncDeepgramClient(...).speak.v1.connect(...)`.
- For each sentence chunk from `session.tts_queue`, it sends:
  - `SpeakV1Text(type="Speak", text=sentence)`
  - `send_flush()`
- It forwards binary PCM audio to the browser and handles:
  - `SpeakV1Metadata`
  - `SpeakV1Warning`
  - `SpeakV1Flushed`
  - `SpeakV1Cleared`
- It updates per-turn TTS metrics and cost as text is synthesized.
- It supports barge-in via `send_clear()`.

### Browser playback was updated for PCM + MP3
- `client/app.js` no longer assumes all binary frames are MP3.
- The server now sends `audio_ready` frames before audio streams.
- The browser routes:
  - `audio/pcm;rate=24000;channels=1;encoding=s16le` → Aura assistant speech via `AudioContext`
  - `audio/mpeg` → existing MP3 playback path for the tool clip

### Config/dependency cleanup
- `edge-tts` and `elevenlabs` were removed from `pyproject.toml`.
- `.env.example` now reflects Deepgram + Groq only.
- `app/config.py` now includes `deepgram_tts_sample_rate=24000`.
- `README.md` and UI labels were updated to Deepgram Aura.
- `uv.lock` was refreshed.

## Verification Already Completed
These checks passed in the latest session:
- `uv lock`
- `python -m py_compile app/main.py app/config.py app/pipeline/tts.py app/pipeline/tools.py scripts/test_client.py`
- `import app.main`
- FastAPI in-process health check returned:
  - `{"status": "ok", "active_sessions": 0, ...}`
- `node --check client/app.js`

## Important Truth: What Has NOT Been Proven Yet
The code-level validation passed, but a live end-to-end voice turn with real Deepgram/Groq credentials was **not** run in the latest session.

So the remaining uncertainty is operational, not architectural:
- Deepgram Aura runtime behavior with the real API
- Browser playback smoothness for actual PCM streams
- Measured end-to-end latency with live services
- Any subtle message-shape mismatch from Deepgram's real WebSocket responses

## Working Tree Notes
The repo is currently dirty with intentional source changes plus generated metadata/artifacts from verification:
- Source files changed intentionally
- `low_latency_voice_agent.egg-info/*` was updated by build verification
- tracked `__pycache__` files also changed

Do not blindly revert these unless the user asks. Just be aware they exist.

## How To Run
### Recommended
```bash
docker compose up --build
```

### Local
```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open:
`http://localhost:8000/client/index.html`

### Important Local Environment Note
In this environment, the shell `uv` / `python` shims may be broken because of `pyenv-win`.

If that happens, use:
```powershell
$env:UV_CACHE_DIR='.uv-cache'
& 'C:\Users\ratho\.local\bin\uv.exe' run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## .env Keys Required
```env
DEEPGRAM_API_KEY=<your-deepgram-key>
OPENAI_API_KEY=<your-groq-key>
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=llama-3.3-70b-versatile
```

## Remaining Tasks For Next Session
1. Run the app with real API keys and test a live browser conversation.
2. Confirm:
   - partial STT appears
   - `LLM_CHUNK` text streams to the UI
   - Aura audio starts quickly
   - `/metrics` shows non-zero TTS characters/cost/TTFA
3. If any runtime issue appears, fix it in:
   - `app/pipeline/tts.py`
   - `client/app.js`
   - `scripts/test_client.py`
4. Measure whether short turns are actually under the sub-2s target.
5. Record the required 3–5 minute demo video.
6. Add the final demo video link to `README.md`.

## Suggested Debug Checklist If Live Test Fails
- If server import/startup fails:
  - inspect `app/pipeline/tts.py`
  - inspect `app/config.py`
- If browser gets text but no speech:
  - inspect `audio_ready` frames
  - inspect `client/app.js` PCM playback path
  - inspect WebSocket binary frame format
- If Deepgram Aura connects but never flushes:
  - inspect `SpeakV1Text` / `send_flush()` flow
  - inspect real response messages from Deepgram
- If metrics stay zero for TTS:
  - inspect turn lifecycle in `app/metrics.py`
  - inspect TTS per-sentence updates in `app/pipeline/tts.py`

## Instructions For The Next Chat
1. Do **not** re-implement the Aura migration from scratch.
2. Start by running the app and validating the live flow with real credentials.
3. Only make code changes if runtime validation exposes a real bug.
4. After live validation passes, record the demo video and update `README.md` with the link.
</project_context>
```
