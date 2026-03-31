const PCM_MIME_PREFIX = "audio/pcm";
const MP3_MIME_TYPE = "audio/mpeg";
const PCM_SAMPLE_RATE = 24000;
const PCM_PLAYBACK_LEAD_S = 0.03;
const MIC_BUFFER_SIZE = 1024;
const PCM_PLAYBACK_WINDOW_MS = 1200;
const MP3_PLAYBACK_WINDOW_MS = 5000;
const BARGE_IN_COOLDOWN_MS = 1200;

const state = {
    ws: null,
    audioContext: null,
    mediaStream: null,
    processor: null,
    isActive: false,
    currentAudioMimeType: MP3_MIME_TYPE,
    mp3SourceBuffer: null,
    mp3MediaSource: null,
    mp3Queue: [],
    mp3AudioElement: null,
    nextPcmPlaybackTime: 0,
    assistantPlaybackActive: false,
    assistantPlaybackUntilMs: 0,
    assistantPlaybackTimer: null,
    lastBargeInAtMs: 0,
    activePcmSources: new Set(),
    ignoreServerMp3: false,
    toolAudioElement: null,
};

// UI Elements
const micBtn = document.getElementById("mic-btn");
const statusDot = document.getElementById("ws-status-dot");
const statusText = document.getElementById("ws-status-text");
const transcriptContainer = document.getElementById("transcript-container");

// Metrics UI Elements
const metricLatency = document.getElementById("metric-latency");
const metricCost = document.getElementById("metric-cost");
const metricSessions = document.getElementById("metric-sessions");
const costStt = document.getElementById("cost-stt");
const costLlm = document.getElementById("cost-llm");
const costTts = document.getElementById("cost-tts");

let partialTranscriptElement = null;
let currentAssistantElement = null;

micBtn.addEventListener("click", toggleConnection);

async function toggleConnection() {
    if (state.isActive) {
        stopSession();
    } else {
        await startSession();
    }
}

async function startSession() {
    try {
        state.audioContext = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: 16000,
        });
        state.mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            },
        });

        const source = state.audioContext.createMediaStreamSource(state.mediaStream);
        state.processor = state.audioContext.createScriptProcessor(MIC_BUFFER_SIZE, 1, 1);

        source.connect(state.processor);
        state.processor.connect(state.audioContext.destination);

        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws/talk`;
        state.ws = new WebSocket(wsUrl);
        state.ws.binaryType = "arraybuffer";

        state.ws.onopen = () => {
            statusDot.classList.add("connected");
            statusText.textContent = "Connected";
            micBtn.classList.add("active");
            micBtn.textContent = "DISCONNECT";
            state.isActive = true;
            state.currentAudioMimeType = MP3_MIME_TYPE;
            state.ignoreServerMp3 = false;
            resetTranscriptSessionState({ clearView: true });

            state.ws.send(JSON.stringify({ type: "control", action: "start" }));

            initMp3Player();
            resetPcmPlayback();
        };

        state.ws.onmessage = handleWebSocketMessage;

        state.ws.onclose = () => {
            stopSession();
        };

        state.ws.onerror = (err) => {
            console.error("WebSocket error", err);
        };

        state.processor.onaudioprocess = (e) => {
            if (!state.isActive || state.ws.readyState !== WebSocket.OPEN) return;

            const float32Data = e.inputBuffer.getChannelData(0);
            const int16Data = new Int16Array(float32Data.length);
            for (let i = 0; i < float32Data.length; i++) {
                const sample = Math.max(-1, Math.min(1, float32Data[i]));
                int16Data[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
            }

            state.ws.send(int16Data.buffer);
        };

        window.metricsInterval = setInterval(fetchMetrics, 2000);
    } catch (err) {
        console.error("Failed to start session:", err);
        alert("Microphone access denied or error starting session.");
        stopSession();
    }
}

function stopSession() {
    resetTranscriptSessionState();
    interruptAssistantPlayback({ restartMp3Player: false });

    if (state.processor) {
        state.processor.disconnect();
        state.processor = null;
    }
    if (state.mediaStream) {
        state.mediaStream.getTracks().forEach((track) => track.stop());
        state.mediaStream = null;
    }
    if (state.audioContext && state.audioContext.state !== "closed") {
        state.audioContext.close();
        state.audioContext = null;
    }
    if (state.ws) {
        if (state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({ type: "control", action: "stop" }));
        }
        state.ws.close();
        state.ws = null;
    }

    resetMp3Playback();
    resetPcmPlayback();
    stopToolAudioPlayback();
    state.ignoreServerMp3 = false;

    state.isActive = false;
    micBtn.classList.remove("active");
    micBtn.textContent = "CONNECT";
    statusDot.classList.remove("connected");
    statusText.textContent = "Disconnected";
    metricSessions.textContent = "0";
    metricLatency.textContent = "--";

    if (window.metricsInterval) {
        clearInterval(window.metricsInterval);
        window.metricsInterval = null;
    }
}

function initMp3Player() {
    if (state.mp3AudioElement) {
        state.mp3AudioElement.pause();
    }

    state.mp3MediaSource = new MediaSource();
    state.mp3AudioElement = new Audio();
    state.mp3AudioElement.src = URL.createObjectURL(state.mp3MediaSource);
    state.mp3Queue = [];

    state.mp3MediaSource.addEventListener("sourceopen", () => {
        try {
            state.mp3SourceBuffer = state.mp3MediaSource.addSourceBuffer(MP3_MIME_TYPE);
            state.mp3SourceBuffer.addEventListener("updateend", pushNextMp3Chunk);
        } catch (e) {
            console.error("MediaSource error. MIME type might not be supported:", e);
        }
    }, { once: true });

    state.mp3AudioElement.play().catch((e) => console.error("Audio auto-play blocked", e));
}

function resetMp3Playback() {
    state.mp3Queue = [];

    if (state.mp3SourceBuffer) {
        try {
            if (state.mp3SourceBuffer.updating) {
                state.mp3SourceBuffer.abort();
            }
        } catch (_) {
            // Ignore abort errors on teardown.
        }
    }

    if (state.mp3AudioElement) {
        state.mp3AudioElement.pause();
        state.mp3AudioElement.currentTime = 0;
    }

    state.mp3SourceBuffer = null;
    state.mp3MediaSource = null;
    state.mp3AudioElement = null;
}

function pushNextMp3Chunk() {
    if (state.mp3SourceBuffer && !state.mp3SourceBuffer.updating && state.mp3Queue.length > 0) {
        const chunk = state.mp3Queue.shift();
        state.mp3SourceBuffer.appendBuffer(chunk);
    }
}

function resetPcmPlayback() {
    state.nextPcmPlaybackTime = state.audioContext ? state.audioContext.currentTime : 0;
}

function stopToolAudioPlayback() {
    if (state.toolAudioElement) {
        try {
            state.toolAudioElement.pause();
            state.toolAudioElement.currentTime = 0;
        } catch (_) {
            // Ignore teardown errors.
        }
    }
    state.toolAudioElement = null;
}

function sendControl(action, payload = {}) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: "control", action, payload }));
    }
}

function markAssistantPlaybackActive(durationMs) {
    const now = Date.now();
    state.assistantPlaybackActive = true;
    state.assistantPlaybackUntilMs = Math.max(
        state.assistantPlaybackUntilMs,
        now + durationMs
    );

    clearTimeout(state.assistantPlaybackTimer);
    state.assistantPlaybackTimer = setTimeout(() => {
        if (Date.now() >= state.assistantPlaybackUntilMs && state.activePcmSources.size === 0) {
            state.assistantPlaybackActive = false;
            state.assistantPlaybackUntilMs = 0;
            state.assistantPlaybackTimer = null;
            state.lastBargeInAtMs = 0;
        }
    }, durationMs + 50);
}

function interruptAssistantPlayback({ restartMp3Player = true } = {}) {
    clearTimeout(state.assistantPlaybackTimer);
    state.assistantPlaybackTimer = null;
    state.assistantPlaybackActive = false;
    state.assistantPlaybackUntilMs = 0;

    state.activePcmSources.forEach((source) => {
        try {
            source.onended = null;
            source.stop(0);
        } catch (_) {
            // Ignore sources that have already finished.
        }
    });
    state.activePcmSources.clear();

    resetPcmPlayback();
    resetMp3Playback();
    stopToolAudioPlayback();

    if (restartMp3Player && state.isActive) {
        initMp3Player();
    }
}

function triggerBargeIn(reason) {
    const now = Date.now();
    if (!state.assistantPlaybackActive) return;
    if (now - state.lastBargeInAtMs < BARGE_IN_COOLDOWN_MS) return;

    state.lastBargeInAtMs = now;
    sendControl("barge_in", { reason });
    interruptAssistantPlayback();
}

function resetTranscriptSessionState({ clearView = false } = {}) {
    clearTimeout(window._assistantResetTimer);
    window._assistantResetTimer = null;
    partialTranscriptElement = null;
    currentAssistantElement = null;

    if (clearView) {
        transcriptContainer.innerHTML = "";
    }
}

async function handleWebSocketMessage(event) {
    if (event.data instanceof ArrayBuffer) {
        await routeBinaryAudio(event.data);
        return;
    }

    if (event.data instanceof Blob) {
        await routeBinaryAudio(await event.data.arrayBuffer());
        return;
    }

    try {
        const payload = JSON.parse(event.data);
        handleJsonPayload(payload);
    } catch (err) {
        console.error("Failed to parse JSON message", err);
    }
}

async function routeBinaryAudio(buffer) {
    if (state.currentAudioMimeType.startsWith(PCM_MIME_PREFIX)) {
        await playPcmChunk(buffer);
        return;
    }

    if (state.ignoreServerMp3) {
        return;
    }

    markAssistantPlaybackActive(MP3_PLAYBACK_WINDOW_MS);
    state.mp3Queue.push(buffer);
    pushNextMp3Chunk();

    if (state.mp3AudioElement && state.mp3AudioElement.paused) {
        state.mp3AudioElement.play().catch(() => {});
    }
}

async function playPcmChunk(buffer) {
    if (!state.audioContext) return;

    if (state.audioContext.state === "suspended") {
        try {
            await state.audioContext.resume();
        } catch (_) {
            return;
        }
    }

    const sampleCount = Math.floor(buffer.byteLength / 2);
    if (!sampleCount) return;

    const view = new DataView(buffer);
    const samples = new Float32Array(sampleCount);
    for (let i = 0; i < sampleCount; i++) {
        samples[i] = view.getInt16(i * 2, true) / 32768;
    }

    const audioBuffer = state.audioContext.createBuffer(1, sampleCount, PCM_SAMPLE_RATE);
    audioBuffer.copyToChannel(samples, 0);

    const source = state.audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(state.audioContext.destination);
    state.activePcmSources.add(source);
    source.onended = () => {
        state.activePcmSources.delete(source);
        if (state.activePcmSources.size === 0 && Date.now() >= state.assistantPlaybackUntilMs) {
            state.assistantPlaybackActive = false;
            state.assistantPlaybackUntilMs = 0;
        }
    };

    const startAt = Math.max(
        state.audioContext.currentTime + PCM_PLAYBACK_LEAD_S,
        state.nextPcmPlaybackTime
    );
    source.start(startAt);
    state.nextPcmPlaybackTime = startAt + audioBuffer.duration;
    markAssistantPlaybackActive(
        Math.max(PCM_PLAYBACK_WINDOW_MS, Math.ceil(audioBuffer.duration * 1000) + 150)
    );
}

function handleJsonPayload(payload) {
    if (payload.type === "transcript") {
        renderTranscript(payload);
    } else if (payload.type === "llm_chunk") {
        handleLLMChunk(payload);
    } else if (payload.type === "tool_call") {
        handleToolCall(payload);
    } else if (payload.type === "audio_ready") {
        handleAudioReady(payload);
    } else if (payload.type === "status") {
        console.log("Status:", payload.event, payload.detail);
    } else if (payload.type === "error") {
        console.error("Server Error:", payload.message);
    }
}

function handleToolCall(payload) {
    if (payload.tool_name !== "play_audio") return;

    const clipName = payload.tool_args && payload.tool_args.clip_name;
    if (clipName === "notification") {
        state.ignoreServerMp3 = true;
        playBundledToolClip("/assets/notification.mp3");
    }
}

function playBundledToolClip(src) {
    stopToolAudioPlayback();

    const audio = new Audio(src);
    audio.preload = "auto";
    audio.volume = 1.0;
    state.toolAudioElement = audio;
    audio.onended = () => {
        if (state.toolAudioElement === audio) {
            state.toolAudioElement = null;
        }
    };
    audio.onerror = (event) => {
        console.error("Tool audio playback failed", event);
    };

    audio.play().catch((error) => {
        console.error("Tool audio auto-play blocked", error);
    });
}

function handleAudioReady(payload) {
    state.currentAudioMimeType = payload.mime_type || MP3_MIME_TYPE;
    if (state.currentAudioMimeType !== MP3_MIME_TYPE) {
        state.ignoreServerMp3 = false;
    }

    if (state.ignoreServerMp3 && state.currentAudioMimeType === MP3_MIME_TYPE) {
        return;
    }

    markAssistantPlaybackActive(
        state.currentAudioMimeType === MP3_MIME_TYPE
            ? MP3_PLAYBACK_WINDOW_MS
            : PCM_PLAYBACK_WINDOW_MS
    );
}

function renderTranscript(payload) {
    const kind = payload.kind ? payload.kind.toLowerCase() : "";
    if (kind === "partial") {
        if (payload.text && payload.text.trim() && state.toolAudioElement) {
            stopToolAudioPlayback();
        }

        if (payload.text && payload.text.trim()) {
            triggerBargeIn("partial_transcript");
        }

        if (window._assistantResetTimer) {
            clearTimeout(window._assistantResetTimer);
            window._assistantResetTimer = null;
            currentAssistantElement = null;
        }

        if (!partialTranscriptElement || !transcriptContainer.contains(partialTranscriptElement)) {
            partialTranscriptElement = document.createElement("div");
            partialTranscriptElement.className = "message msg-partial";
            transcriptContainer.appendChild(partialTranscriptElement);
        }
        partialTranscriptElement.textContent = payload.text;
    } else if (kind === "final") {
        if (partialTranscriptElement) {
            partialTranscriptElement.remove();
            partialTranscriptElement = null;
        }

        clearTimeout(window._assistantResetTimer);
        window._assistantResetTimer = null;

        const finalElem = document.createElement("div");
        finalElem.className = "message msg-user";
        finalElem.textContent = payload.text;
        transcriptContainer.appendChild(finalElem);

        currentAssistantElement = document.createElement("div");
        currentAssistantElement.className = "message msg-assistant";
        currentAssistantElement.textContent = "";
        currentAssistantElement.style.opacity = "0.5";
        transcriptContainer.appendChild(currentAssistantElement);
    }

    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
}

function handleLLMChunk(payload) {
    if (!currentAssistantElement || !transcriptContainer.contains(currentAssistantElement)) {
        currentAssistantElement = document.createElement("div");
        currentAssistantElement.className = "message msg-assistant";
        currentAssistantElement.textContent = "";
        transcriptContainer.appendChild(currentAssistantElement);
    }

    currentAssistantElement.style.opacity = "1";

    if (currentAssistantElement.textContent) {
        currentAssistantElement.textContent += " " + payload.text;
    } else {
        currentAssistantElement.textContent = payload.text;
    }

    if (payload.is_sentence_end) {
        clearTimeout(window._assistantResetTimer);
        window._assistantResetTimer = setTimeout(() => {
            currentAssistantElement = null;
        }, 2000);
    }

    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
}

async function fetchMetrics() {
    if (!state.isActive) return;
    try {
        const response = await fetch("/metrics");
        const data = await response.json();
        const sessionDetails = data.active_session_details && data.active_session_details.length > 0
            ? data.active_session_details[0]
            : null;
        const latestTurn = sessionDetails && sessionDetails.turns && sessionDetails.turns.length > 0
            ? sessionDetails.turns[sessionDetails.turns.length - 1]
            : null;
        const latencyMs =
            latestTurn && latestTurn.timing_ms ? latestTurn.timing_ms.end_to_end : null;

        metricSessions.textContent = String(data.active_sessions ?? 0);
        metricLatency.textContent = latencyMs ? (latencyMs / 1000).toFixed(2) : "--";

        const totalCost = (data.cost_usd && data.cost_usd.total) || data.total_cost_usd || 0;
        metricCost.textContent = totalCost.toFixed(4);

        if (sessionDetails) {
            const breakdown = sessionDetails.cost_usd || {};
            const sttTotal = breakdown.stt || 0;
            const llmTotal = breakdown.llm || 0;
            const ttsTotal = breakdown.tts || 0;

            costStt.textContent = "$" + sttTotal.toFixed(4);
            costLlm.textContent = "$" + llmTotal.toFixed(4);
            costTts.textContent = "$" + ttsTotal.toFixed(4);
        }
    } catch (e) {
        console.warn("Failed to fetch metrics", e);
    }
}
