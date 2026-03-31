"""
scripts/test_client.py — Automated test client for the voice pipeline.

Simulates a browser client by:
  1. Connecting to ws://localhost:8000/ws/talk
  2. Sending a pre-recorded WAV file as binary audio frames
  3. Printing all received JSON frames
  4. Saving received Aura PCM as output.wav and tool MP3 audio as output.mp3

Usage:
    uv run python scripts/test_client.py [--wav path/to/audio.wav] [--url ws://localhost:8000/ws/talk]

The test WAV should be:
  - PCM 16-bit, 16kHz, mono (Deepgram's preferred STT format)
  - Length: 5-30 seconds of speech content
"""
from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import time
from pathlib import Path

try:
    import websockets
    from websockets.asyncio.client import connect
except ImportError:
    print("ERROR: websockets not installed. Run: uv add websockets", file=sys.stderr)
    sys.exit(1)

DEFAULT_URL = "ws://localhost:8000/ws/talk"
CHUNK_SIZE = 4096
SEND_INTERVAL = 0.05
PCM_SAMPLE_RATE = 24000
PCM_MIME_PREFIX = "audio/pcm"
MP3_MIME_TYPE = "audio/mpeg"


async def run_test(wav_path: Path | None, url: str) -> None:
    print(f"Connecting to {url} ...")
    assistant_audio = bytearray()
    tool_audio = bytearray()
    current_audio_mime = MP3_MIME_TYPE
    frame_count = 0
    start_time = time.monotonic()

    async with connect(url) as ws:
        print("Connected ✓")
        await ws.send(json.dumps({"type": "control", "action": "start"}))

        async def receive_loop() -> None:
            nonlocal frame_count, current_audio_mime
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        if current_audio_mime.startswith(PCM_MIME_PREFIX):
                            assistant_audio.extend(message)
                            print(
                                f"  ← [AUDIO PCM] {len(message)} bytes "
                                f"(total: {len(assistant_audio)})"
                            )
                        else:
                            tool_audio.extend(message)
                            print(
                                f"  ← [AUDIO MP3] {len(message)} bytes "
                                f"(total: {len(tool_audio)})"
                            )
                    else:
                        frame = json.loads(message)
                        frame_count += 1
                        if frame.get("type") == "audio_ready":
                            current_audio_mime = frame.get("mime_type", MP3_MIME_TYPE)
                        print(f"  ← [{frame.get('type', '?').upper():12s}] {json.dumps(frame)}")
            except websockets.exceptions.ConnectionClosed:
                pass

        recv_task = asyncio.create_task(receive_loop())

        if wav_path and wav_path.exists():
            audio_data = _load_audio(wav_path)
            print(f"Streaming {len(audio_data)} bytes from {wav_path} ...")
            for i in range(0, len(audio_data), CHUNK_SIZE):
                await ws.send(audio_data[i : i + CHUNK_SIZE])
                await asyncio.sleep(SEND_INTERVAL)
            print("Audio stream complete.")
        else:
            print("No WAV file — sending 5s of silence for pipeline smoke test ...")
            silence = b"\x00\x00" * 16000 * 5
            for i in range(0, len(silence), CHUNK_SIZE):
                await ws.send(silence[i : i + CHUNK_SIZE])
                await asyncio.sleep(SEND_INTERVAL)

        await ws.send(json.dumps({"type": "control", "action": "stop"}))
        print("Sent STOP, waiting for final frames ...")
        await asyncio.sleep(3)

        recv_task.cancel()
        await asyncio.gather(recv_task, return_exceptions=True)

    elapsed = time.monotonic() - start_time
    print(f"\n{'─' * 60}")
    print(f"Test complete in {elapsed:.2f}s")
    print(f"  JSON frames received : {frame_count}")
    print(f"  Aura PCM bytes       : {len(assistant_audio)}")
    print(f"  Tool MP3 bytes       : {len(tool_audio)}")

    if assistant_audio:
        wav_path_out = Path("output.wav")
        wav_path_out.write_bytes(_pcm_to_wav_bytes(bytes(assistant_audio), PCM_SAMPLE_RATE))
        print(f"  Aura audio saved to  : {wav_path_out.resolve()}")

    if tool_audio:
        mp3_path_out = Path("output.mp3")
        mp3_path_out.write_bytes(bytes(tool_audio))
        print(f"  Tool audio saved to  : {mp3_path_out.resolve()}")


def _load_audio(path: Path) -> bytes:
    """Strip WAV header and return raw PCM, or transcode via ffmpeg."""
    data = path.read_bytes()

    if data[:4] == b"RIFF":
        print(f"Detected WAV format for {path.name}.")
        return data[44:]

    print(
        f"Non-WAV format detected for {path.name}. "
        "Attempting on-the-fly ffmpeg transcoding to 16kHz PCM..."
    )
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        print(
            "ERROR: ffmpeg is not installed or not in PATH. "
            "Please install ffmpeg or provide a raw PCM WAV file.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        process = subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(path),
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                "16000",
                "pipe:1",
            ],
            capture_output=True,
            check=True,
        )
        print(f"Transcoding successful ({len(process.stdout)} bytes generated).")
        return process.stdout
    except subprocess.CalledProcessError as exc:
        print(f"ERROR transcoding {path}: {exc.stderr.decode()}", file=sys.stderr)
        sys.exit(1)


def _pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM s16le bytes in a WAV header."""
    byte_rate = sample_rate * 2
    block_align = 2
    data_size = len(pcm_bytes)
    riff_size = 36 + data_size
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        byte_rate,
        block_align,
        16,
        b"data",
        data_size,
    )
    return header + pcm_bytes


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
