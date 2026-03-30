"""
app/pipeline/tts.py — ElevenLabs streaming TTS processor.

Architecture:
  tts_processor(session) reads sentence chunks from session.tts_queue
  → opens an ElevenLabs WebSocket connection
  → streams sentence text as it arrives
  → forwards MP3 audio chunks to the client WebSocket
  → logs time-to-first-audio, total TTS time, character count
  → respects barge_in_event to cancel mid-stream
"""
from __future__ import annotations

import asyncio
import base64
import json
import time

import structlog
import websockets

from app.session import QUEUE_SENTINEL, VoiceSession

logger = structlog.get_logger(__name__)

async def tts_processor(session: VoiceSession) -> None:
    """Implement ElevenLabs streaming TTS Phase 4 logic."""
    log = logger.bind(session_id=str(session.id), task="tts")
    log.info("tts.start")

    settings = session.settings
    # Safely extract API key (handles Pydantic SecretStr if present)
    api_key_val = settings.elevenlabs_api_key
    if hasattr(api_key_val, "get_secret_value"):
        api_key_str = api_key_val.get_secret_value()
    else:
        api_key_str = str(api_key_val)

    # ElevenLabs WebSocket URI
    voice_id = settings.elevenlabs_voice_id
    model_id = settings.elevenlabs_model_id
    uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id={model_id}"

    try:
        async with websockets.connect(uri) as ws:
            # 1. Send the initial connection setup payload
            init_msg = {
                "text": " ",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
                "xi_api_key": api_key_str,
            }
            await ws.send(json.dumps(init_msg))

            # --- Background task to receive audio chunks ---
            async def _receive_audio():
                first_chunk = True
                audio_bytes_received = 0
                try:
                    async for message in ws:
                        if session.barge_in_event.is_set():
                            break
                            
                        data = json.loads(message)
                        if data.get("audio"):
                            audio_bytes = base64.b64decode(data["audio"])
                            if audio_bytes:
                                audio_bytes_received += len(audio_bytes)
                                
                                # Process metrics on first real chunk
                                turn = session.metrics.current_turn
                                if first_chunk and turn:
                                    turn.tts_first_chunk_s = time.monotonic()
                                    first_chunk = False
                                
                                # Forward binary MP3 chunk directly to client
                                try:
                                    await session.websocket.send_bytes(audio_bytes)
                                except Exception as e:
                                    log.warning("tts.send_bytes_error", error=str(e))
                                
                        # Check for end-of-stream indicator
                        if data.get("isFinal"):
                            log.debug("tts.is_final_received")
                            break
                            
                except websockets.exceptions.ConnectionClosed:
                    log.debug("tts.elevenlabs_ws_closed")
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.error("tts.receive_error", error=str(e))
                finally:
                    log.info("tts.receive_audio_done", bytes=audio_bytes_received)
                    turn = session.metrics.current_turn
                    if turn:
                        turn.tts_done_s = time.monotonic()

            receiver_task = asyncio.create_task(_receive_audio())

            # --- 2. Foreground loop: Send text chunks to ElevenLabs ---
            total_chars = 0
            turn = session.metrics.current_turn
            
            try:
                while True:
                    if session.barge_in_event.is_set():
                        log.info("tts.barge_in_detected")
                        # You could potentially abort ElevenLabs generation here if API permits
                        break
                        
                    sentence = await session.tts_queue.get()
                    
                    if sentence is QUEUE_SENTINEL:
                        log.info("tts.sentinel_received")
                        # Send EOS to ElevenLabs
                        await ws.send(json.dumps({"text": ""}))
                        break
                        
                    if sentence:    
                        total_chars += len(sentence)
                        payload = {
                            "text": sentence,
                            "try_trigger_generation": True
                        }
                        await ws.send(json.dumps(payload))
                        
            except asyncio.CancelledError:
                pass
            finally:
                if turn:
                    turn.tts_characters += total_chars
                    turn.tts_cost_usd += (total_chars / 1000.0) * settings.elevenlabs_cost_per_1k_chars
                    
                log.info("tts.sent_all_text", characters=total_chars)
                
                # Wait for all audio to finish returning
                try:
                    # Give it up to 5 seconds to drain
                    await asyncio.wait_for(receiver_task, timeout=5.0)
                except asyncio.TimeoutError:
                    log.warning("tts.receiver_drain_timeout")
                    receiver_task.cancel()
                except asyncio.CancelledError:
                    receiver_task.cancel()
                    
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error("tts.fatal_error", error=str(e), exc_info=True)
    finally:
        log.info("tts.done")
