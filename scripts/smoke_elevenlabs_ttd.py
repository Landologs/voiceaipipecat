"""Generate one short Hebrew Eleven v3 sample without starting a call.

The script uses the same Text-to-Dialogue WebSocket and environment settings as
the receptionist. It writes raw provider PCM to a local WAV file and never
prints credentials.
"""

from __future__ import annotations

import asyncio
import time
import wave
from datetime import datetime
from pathlib import Path

from app.config.settings import ROOT, Settings
from app.voice.elevenlabs_dialogue_direct import synthesize_dialogue


SAMPLE_TEXT = "היי, זה מיזוג פלוס. מה נשמע?"
OUTPUT_ROOT = ROOT / "demo" / "results" / "provider-tests"


async def generate_sample() -> Path:
    settings = Settings.load()
    settings.validate_voice()
    if settings.tts_provider != "elevenlabs":
        raise ValueError("TTS_PROVIDER must be elevenlabs for this smoke test")

    started = time.perf_counter()
    pcm, rate, channels, ttfb = await synthesize_dialogue(
        api_key=settings.elevenlabs_api_key,
        voice_id=settings.elevenlabs_voice_id,
        model=settings.tts_model,
        text=SAMPLE_TEXT,
    )

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_ROOT / f"eleven-v3-hebrew-{datetime.now():%Y%m%d-%H%M%S}.wav"
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)

    elapsed = time.perf_counter() - started
    print(f"ElevenLabs sample saved: {output}")
    print(f"First audio: {ttfb:.3f}s; complete: {elapsed:.3f}s")
    return output


if __name__ == "__main__":
    asyncio.run(generate_sample())
