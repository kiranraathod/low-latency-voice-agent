# 🎥 Voice AI Agent Demo Video Prep Guide

This guide provides a structured checklist and script to help you record the perfect 3-5 minute demo video required by the assignment specification.

## 📋 Pre-recording Checklist

- [ ] Ensure Docker Desktop (or engine) is running.
- [ ] Verify you have a valid `.env` file populated with working API keys in the root directory.
- [ ] Ensure your microphone is working and not claimed exclusively by another application.
- [ ] Close unnecessary background tabs to ensure screen recording is smooth.
- [ ] Open a terminal window positioned where it's easily visible.
- [ ] Have your web browser ready on a blank tab.
- [ ] Use a screen recording software (OBS, QuickTime, Loom, Windows Game Bar) that captures **both your microphone (input)** and **system audio (output)**.

---

## 🎬 3–5 Minute Script & Flow

### ⏱️ 0:00 - 0:30 | Introduction & Startup
1. **Action**: Open your terminal. Let the viewer see the project directory.
2. **Action**: Run `docker compose up --build`.
3. **Narration**: *"Hello, this is a demonstration of the real-time Voice AI agent prototype. I'm starting the service using Docker Compose. As you can see, it builds the multi-stage Python 3.11 environment cleanly and starts the FastAPI server on port 8000."*

### ⏱️ 0:30 - 1:30 | Connecting & Live Interaction
1. **Action**: Switch to your browser and navigate to `http://localhost:8000/client/index.html`.
2. **Action**: Click the **CONNECT** button. (Approve mic permissions if asked).
3. **Narration**: *"I'm now connecting to the WebSocket at `/ws/talk`. The frontend captures PCM audio directly from my microphone using the Web Audio API and streams it to the backend."*
4. **Action**: Speak naturally into the mic. Say: *"Hello assistant, how are you today?"*
5. **Observation**: Point out the grey *partial transcripts* updating live on screen, demonstrating the streaming STT (Deepgram).
6. **Observation**: Explain how the LLM (Gemini 2.0 Flash) is streaming its response, chunked by sentences, and sent to the TTS (ElevenLabs). The agent's voice should play out loud almost immediately (sub-2s latency).

### ⏱️ 1:30 - 2:30 | Tool Calling Demonstration
1. **Action**: Ask the agent to use its tool. Speak clearly: *"Can you play the notification sound for me?"* (or whatever prompt triggers the `play_audio` tool you defined in Phase 3).
2. **Observation**: Wait for the assistant to acknowledge and trigger the tool. The server should forcefully push the audio clip over the WebSocket.
3. **Narration**: *"Notice how the LLM successfully decided to call the `play_audio` tool. The backend executed the tool logic, which bypassed TTS and directly streamed the pre-bundled audio clip back to the browser."*

### ⏱️ 2:30 - 3:30 | Observability, Architecture & Latency
1. **Action**: Look at the right side of the UI where the Telemetry dashboard natively shows the metrics.
2. **Narration**: *"To meet the requirement of tracking latency and cost, the application has an integrated metrics system. As shown here, we are maintaining a very low End-to-End latency."*
3. **Action**: Briefly explain *how* latency is kept low: *"We hit sub-2-second latency by parallelizing sentences. Partial sentences are piped from the LLM directly into ElevenLabs, starting audio playback before the LLM has even finished its thought."*
4. **Action**: Open a new tab and hit `http://localhost:8000/metrics`.
5. **Narration**: *"Here is the raw `/metrics` endpoint. You can see the explicit breakdown of cost for STT, LLM, and TTS separately, estimated automatically based on audio duration, token count, and character length per the spec requests."*

### ⏱️ 3:30 - 4:00 | Teardown (Failure Scenario)
1. **Action**: Abruptly close the browser tab running the Client UI.
2. **Action**: Jump back to the terminal. Show the structured logs rapidly cleaning up.
3. **Narration**: *"Finally, to show the fault tolerance, I have abruptly closed the client connection. Because the entire session is wrapped in an `asyncio.TaskGroup`, the disconnection immediately propagates, cleanly canceling the STT, LLM, and TTS pipelines to preserve memory and prevent zombie API usage."*

---

> **Tip:** If the LLM behaves unexpectedly, just click **DISCONNECT** and **CONNECT** again to start a fresh sequence. Good luck with the recording!
