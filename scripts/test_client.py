"""
scripts/test_client.py — Automated test client for the voice pipeline.

Simulates a browser client by:
  1. Connecting to ws://localhost:8000/ws/talk
  2. Sending a pre-recorded WAV file as binary audio frames
  3. Printing all received JSON frames
  4. Saving received audio to output.mp3

Usage:
    uv run python scripts/test_client.py [--wav path/to/audio.wav] [--url ws://localhost:8000/ws/talk]

The test WAV should be:
  - PCM 16-bit, 16kHz, mono (Deepgram's preferred format)
  - Length: 5-30 seconds of speech content
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Will be used in full implementation
try:
    import websockets
    from websockets.asyncio.client import connect
except ImportError:
    print("ERROR: websockets not installed. Run: uv add websockets", file=sys.stderr)
    sys.exit(1)

DEFAULT_URL = "ws://localhost:8000/ws/talk"
CHUNK_SIZE = 4096       # bytes per audio frame sent
SEND_INTERVAL = 0.05   # seconds between chunks (simulates real-time streaming)


async def run_test(wav_path: Path | None, url: str) -> None:
    print(f"Connecting to {url} ...")
    received_audio = bytearray()
    frame_count = 0
    start_time = time.monotonic()

    async with connect(url) as ws:
        print("Connected ✓")

        # Send START control frame
        await ws.send(
            json.dumps({"type": "control", "action": "start"})
        )

        async def receive_loop() -> None:
            nonlocal frame_count, received_audio
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        received_audio.extend(message)
                        print(f"  ← [AUDIO] {len(message)} bytes (total: {len(received_audio)})")
                    else:
                        frame = json.loads(message)
                        frame_count += 1
                        ftype = frame.get("type", "?")
                        print(f"  ← [{ftype.upper():12s}] {json.dumps(frame, indent=None)}")
            except websockets.exceptions.ConnectionClosed:
                pass

        recv_task = asyncio.create_task(receive_loop())

        # Stream audio
        if wav_path and wav_path.exists():
            audio_data = _load_audio(wav_path)
            print(f"Streaming {len(audio_data)} bytes from {wav_path} ...")
            for i in range(0, len(audio_data), CHUNK_SIZE):
                chunk = audio_data[i : i + CHUNK_SIZE]
                await ws.send(chunk)
                await asyncio.sleep(SEND_INTERVAL)
            print("Audio stream complete.")
        else:
            print("No WAV file — sending 5s of silence for pipeline smoke test ...")
            # 5 seconds of 16kHz 16-bit mono silence
            silence = b"\x00\x00" * 16000 * 5
            for i in range(0, len(silence), CHUNK_SIZE):
                await ws.send(silence[i : i + CHUNK_SIZE])
                await asyncio.sleep(SEND_INTERVAL)

        # Send STOP signal
        await ws.send(json.dumps({"type": "control", "action": "stop"}))
        print("Sent STOP, waiting for final frames ...")
        await asyncio.sleep(3)

        recv_task.cancel()
        await asyncio.gather(recv_task, return_exceptions=True)

    elapsed = time.monotonic() - start_time
    print(f"\n{'─'*60}")
    print(f"Test complete in {elapsed:.2f}s")
    print(f"  JSON frames received : {frame_count}")
    print(f"  Audio bytes received : {len(received_audio)}")

    if received_audio:
        out_path = Path("output.mp3")
        out_path.write_bytes(bytes(received_audio))
        print(f"  Audio saved to       : {out_path.resolve()}")


def _load_audio(path: Path) -> bytes:
    """Strip WAV header and return raw PCM, or transcode via ffmpeg."""
    data = path.read_bytes()
    
    # If it's a standard RIFF WAV, just strip the 44-byte header
    if data[:4] == b"RIFF":
        print(f"Detected WAV format for {path.name}.")
        return data[44:]
        
    print(f"Non-WAV format detected for {path.name}. Attempting on-the-fly ffmpeg transcoding to 16kHz PCM...")
    import subprocess
    import shutil
    
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg is not installed or not in PATH. Please install ffmpeg to test with MP3 files, or provide a RAW PCM WAV file instead.", file=sys.stderr)
        sys.exit(1)
        
    try:
        # Ask ffmpeg to decode the audio mathematically to 16kHz 16-bit Mono PCM and dump straight to stdout
        process = subprocess.run(
            ["ffmpeg", "-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
            capture_output=True,
            check=True
        )
        print(f"Transcoding successful ({len(process.stdout)} bytes generated).")
        return process.stdout
    except subprocess.CalledProcessError as e:
        print(f"ERROR transcocing {path}: {e.stderr.decode()}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice agent test client")
    parser.add_argument("--wav", type=Path, default=None, help="Path to WAV audio file")
    parser.add_argument("--url", default=DEFAULT_URL, help="WebSocket URL")
    args = parser.parse_args()

    try:
        asyncio.run(run_test(args.wav, args.url))
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
