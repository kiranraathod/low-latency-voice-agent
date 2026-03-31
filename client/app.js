// app.js

const state = {
    ws: null,
    audioContext: null,
    mediaStream: null,
    processor: null,
    isActive: false,
    ttsSourceBuffer: null,
    ttsMediaSource: null,
    ttsQueue: [],
    ttsAudioElement: null
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
        // 1. Initialize Audio Context for Microphone Capture
        state.audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        const source = state.audioContext.createMediaStreamSource(state.mediaStream);
        
        // Use ScriptProcessor for broad compatibility and simplicity in downsampling
        // 4096 framing gives ~250ms chunks at 16000Hz.
        state.processor = state.audioContext.createScriptProcessor(4096, 1, 1);
        
        source.connect(state.processor);
        state.processor.connect(state.audioContext.destination);
        
        // 2. Setup WebSocket
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws/talk`;
        state.ws = new WebSocket(wsUrl);
        state.ws.binaryType = "blob";
        
        state.ws.onopen = () => {
            console.log("WebSocket connected");
            statusDot.classList.add("connected");
            statusText.textContent = "Connected";
            micBtn.classList.add("active");
            micBtn.textContent = "DISCONNECT";
            state.isActive = true;
            transcriptContainer.innerHTML = ""; // clear
            
            // Send START control frame
            state.ws.send(JSON.stringify({ action: "start" }));
            
            initTTSPlayer();
        };
        
        state.ws.onmessage = handleWebSocketMessage;
        
        state.ws.onclose = () => {
            console.log("WebSocket disconnected");
            stopSession();
        };

        state.ws.onerror = (err) => {
            console.error("WebSocket error", err);
        };
        
        // 3. Capture Audio and Send
        state.processor.onaudioprocess = (e) => {
            if (!state.isActive || state.ws.readyState !== WebSocket.OPEN) return;
            
            const float32Data = e.inputBuffer.getChannelData(0);
            
            // Convert Float32 to Int16
            const int16Data = new Int16Array(float32Data.length);
            for (let i = 0; i < float32Data.length; i++) {
                let s = Math.max(-1, Math.min(1, float32Data[i]));
                int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            
            // Send binary PCM frame
            state.ws.send(int16Data.buffer);
        };
        
        // Poll for metrics
        window.metricsInterval = setInterval(fetchMetrics, 2000);

    } catch (err) {
        console.error("Failed to start session:", err);
        alert("Microphone access denied or error starting session.");
        stopSession();
    }
}

function stopSession() {
    if (state.processor) {
        state.processor.disconnect();
        state.processor = null;
    }
    if (state.mediaStream) {
        state.mediaStream.getTracks().forEach(t => t.stop());
        state.mediaStream = null;
    }
    if (state.audioContext && state.audioContext.state !== 'closed') {
        state.audioContext.close();
        state.audioContext = null;
    }
    if (state.ws) {
        if (state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({ action: "stop" }));
        }
        state.ws.close();
        state.ws = null;
    }
    
    state.isActive = false;
    micBtn.classList.remove("active");
    micBtn.textContent = "CONNECT";
    statusDot.classList.remove("connected");
    statusText.textContent = "Disconnected";
    
    if (window.metricsInterval) {
        clearInterval(window.metricsInterval);
    }
}

function initTTSPlayer() {
    state.ttsMediaSource = new MediaSource();
    state.ttsAudioElement = new Audio();
    state.ttsAudioElement.src = URL.createObjectURL(state.ttsMediaSource);
    state.ttsQueue = [];
    
    state.ttsMediaSource.addEventListener('sourceopen', () => {
        // audio/mpeg is commonly supported for ElevenLabs default format
        try {
            state.ttsSourceBuffer = state.ttsMediaSource.addSourceBuffer('audio/mpeg');
            state.ttsSourceBuffer.addEventListener('updateend', pushNextTTSChunk);
        } catch (e) {
            console.error("MediaSource error. MIME type might not be supported:", e);
        }
    });

    state.ttsAudioElement.play().catch(e => console.error("Audio auto-play blocked", e));
}

function pushNextTTSChunk() {
    if (state.ttsSourceBuffer && !state.ttsSourceBuffer.updating && state.ttsQueue.length > 0) {
        const chunk = state.ttsQueue.shift();
        state.ttsSourceBuffer.appendBuffer(chunk);
    }
}

async function handleWebSocketMessage(event) {
    if (event.data instanceof Blob) {
        // Binary TTS chunk received
        const buffer = await event.data.arrayBuffer();
        state.ttsQueue.push(buffer);
        pushNextTTSChunk();
        
        if (state.ttsAudioElement.paused) {
            state.ttsAudioElement.play().catch(() => {});
        }
    } else {
        // JSON Control or Status frame
        try {
            const payload = JSON.parse(event.data);
            handleJsonPayload(payload);
        } catch (err) {
            console.error("Failed to parse JSON message", err);
        }
    }
}

function handleJsonPayload(payload) {
    if (payload.type === "transcript") {
        renderTranscript(payload);
    } else if (payload.type === "llm_chunk") {
        handleLLMChunk(payload);
    } else if (payload.type === "status") {
        console.log("Status:", payload.event, payload.detail);
    } else if (payload.type === "error") {
        console.error("Server Error:", payload.message);
    }
}

let partialTranscriptElement = null;
let currentAssistantElement = null;

function renderTranscript(payload) {
    const kind = payload.kind ? payload.kind.toLowerCase() : "";
    if (kind === "partial") {
        if (!partialTranscriptElement) {
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
        
        // Only create user message + assistant placeholder if we don't have one pending
        if (!currentAssistantElement) {
            const finalElem = document.createElement("div");
            finalElem.className = "message msg-user";
            finalElem.textContent = payload.text;
            transcriptContainer.appendChild(finalElem);
            
            // Create a single assistant placeholder that will be filled by llm_chunk frames
            currentAssistantElement = document.createElement("div");
            currentAssistantElement.className = "message msg-assistant";
            currentAssistantElement.textContent = "";
            currentAssistantElement.style.opacity = "0.5";
            transcriptContainer.appendChild(currentAssistantElement);
        }
    }
    
    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
}

function handleLLMChunk(payload) {
    if (!currentAssistantElement) {
        // Create one if it doesn't exist yet (edge case)
        currentAssistantElement = document.createElement("div");
        currentAssistantElement.className = "message msg-assistant";
        currentAssistantElement.textContent = "";
        transcriptContainer.appendChild(currentAssistantElement);
    }
    
    // Make it fully visible on first chunk
    currentAssistantElement.style.opacity = "1";
    
    // Append the text chunk (sentences arrive one at a time)
    if (currentAssistantElement.textContent) {
        currentAssistantElement.textContent += " " + payload.text;
    } else {
        currentAssistantElement.textContent = payload.text;
    }
    
    // If this is a sentence end and the response might be complete,
    // prepare for the next turn
    if (payload.is_sentence_end) {
        // Mark this assistant element as complete after a brief delay
        // so subsequent chunks from the same turn still append
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
        const response = await fetch('/metrics');
        const data = await response.json();
        
        metricSessions.textContent = data.active_sessions || "1";
        metricLatency.textContent = data.avg_end_to_end_ms ? (data.avg_end_to_end_ms / 1000).toFixed(2) : "--";
        
        let totalCost = data.total_cost_usd || 0;
        metricCost.textContent = totalCost.toFixed(4);
        
        // Find current session cost breakdown
        if (data.active_session_details && data.active_session_details.length > 0) {
            // grab the latest active session details as an approximation
            const sd = data.active_session_details[0];
            costStt.textContent = "$" + (sd.total_stt_cost || 0).toFixed(4);
            costLlm.textContent = "$" + (sd.total_llm_cost || 0).toFixed(4);
            costTts.textContent = "$" + (sd.total_tts_cost || 0).toFixed(4);
        }
    } catch (e) {
        console.warn("Failed to fetch metrics", e);
    }
}
