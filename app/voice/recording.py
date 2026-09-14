"""Local WAV persistence for complete caller/agent voice conversations."""

from __future__ import annotations

import asyncio
import logging
import os
import wave
from pathlib import Path

logger = logging.getLogger(__name__)


def write_pcm_wav(
    audio: bytes,
    target: Path,
    *,
    sample_rate: int,
    num_channels: int,
) -> Path:
    """Atomically save signed 16-bit PCM as a standard WAV file."""
    if not audio:
        raise ValueError("Cannot save an empty audio recording")
    if sample_rate <= 0 or num_channels not in {1, 2}:
        raise ValueError("Invalid WAV audio format")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with temporary.open("w+b") as raw_stream:
            with wave.open(raw_stream, "wb") as wav_stream:
                wav_stream.setnchannels(num_channels)
                wav_stream.setsampwidth(2)
                wav_stream.setframerate(sample_rate)
                wav_stream.writeframes(audio)
            raw_stream.flush()
            os.fsync(raw_stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


class CallRecordingWriter:
    """Persist the Pipecat audio buffer without blocking the conversation loop."""

    def __init__(self, target: Path):
        self.target = target
        self.saved_path: Path | None = None

    async def save(
        self,
        audio: bytes,
        sample_rate: int,
        num_channels: int,
    ) -> Path | None:
        if not audio:
            logger.warning("Call audio recording was empty; no WAV file was saved")
            return None
        try:
            self.saved_path = await asyncio.to_thread(
                write_pcm_wav,
                audio,
                self.target,
                sample_rate=sample_rate,
                num_channels=num_channels,
            )
        except (OSError, ValueError):
            logger.exception("Call audio recording could not be saved")
            return None
        logger.info("Call audio recording saved: %s", self.saved_path)
        return self.saved_path
