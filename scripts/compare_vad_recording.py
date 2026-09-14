"""Compare default and telephone VAD settings on an existing stereo call WAV."""

from __future__ import annotations

import argparse
import asyncio
import wave
from array import array
from pathlib import Path

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

from app.voice.pipeline import TELEPHONY_VAD_PARAMS


async def speech_starts(audio: bytes, sample_rate: int, params: VADParams) -> int:
    vad = SileroVADAnalyzer(sample_rate=sample_rate, params=params)
    vad.set_sample_rate(sample_rate)
    previous = VADState.QUIET
    starts = 0
    try:
        # 20 ms input chunks approximate the cadence of the telephone transport.
        for offset in range(0, len(audio), 320 * 2):
            state = await vad.analyze_audio(audio[offset : offset + 320 * 2])
            if state is VADState.SPEAKING and previous is not VADState.SPEAKING:
                starts += 1
            previous = state
    finally:
        await vad.cleanup()
    return starts


async def main(path: Path) -> None:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        samples = array("h")
        samples.frombytes(wav.readframes(wav.getnframes()))
    caller_audio = array("h", samples[0::channels]).tobytes()
    old = await speech_starts(caller_audio, sample_rate, VADParams(stop_secs=0.2))
    new = await speech_starts(caller_audio, sample_rate, TELEPHONY_VAD_PARAMS)
    print(f"default_speech_starts={old}")
    print(f"telephone_speech_starts={new}")
    print(f"additional_short_turns_detected={new - old}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("recording", type=Path)
    args = parser.parse_args()
    asyncio.run(main(args.recording))
