"""Small direct ElevenLabs Text-to-Dialogue client for offline audio diagnostics."""

from __future__ import annotations

import base64
import json
import time
import uuid

import websockets


async def synthesize_dialogue(
    *,
    api_key: str,
    voice_id: str,
    model: str,
    text: str,
    language: str = "he",
) -> tuple[bytes, int, int, float]:
    """Return signed 16-bit mono PCM and first-audio latency."""
    context_id = f"diagnostic-{uuid.uuid4().hex}"
    url = (
        "wss://api.elevenlabs.io/v1/text-to-dialogue/multi-stream-input"
        f"?model_id={model}&output_format=pcm_24000"
        f"&language_code={language}&enable_logging=false"
    )
    chunks: list[bytes] = []
    first_audio_at: float | None = None

    async with websockets.connect(
        url,
        max_size=16 * 1024 * 1024,
        additional_headers={"xi-api-key": api_key},
    ) as socket:
        # Pipecat establishes and keeps this socket open during pipeline setup,
        # so measure synthesis latency after the connection is ready.
        started = time.perf_counter()
        await socket.send(json.dumps({
            "context_id": context_id,
            "voices": [voice_id],
            "voice_settings": {"stability": 0.5},
        }))
        await socket.send(json.dumps({
            "context_id": context_id,
            "inputs": [{"text": text, "voice_id": voice_id, "new_turn": True}],
        }, ensure_ascii=False))
        await socket.send(json.dumps({"context_id": context_id, "close_context": True}))

        async for raw_message in socket:
            message = json.loads(raw_message)
            if message.get("error"):
                raise RuntimeError(str(message.get("message") or "ElevenLabs error"))
            if message.get("context_id") != context_id:
                continue
            if message.get("audio"):
                if first_audio_at is None:
                    first_audio_at = time.perf_counter()
                chunks.append(base64.b64decode(message["audio"]))
            if message.get("is_final") is True:
                break
        await socket.send(json.dumps({"close_socket": True}))

    if not chunks:
        raise RuntimeError("ElevenLabs returned no audio")
    ttfb = (first_audio_at - started) if first_audio_at else 0.0
    return b"".join(chunks), 24000, 1, ttfb
