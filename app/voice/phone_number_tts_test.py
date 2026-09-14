"""Standalone local-speaker diagnostic for Hebrew phone-number TTS.

It deliberately bypasses STT, the LLM, the receptionist pipeline and every
business-data path.  The canonical phone numbers in this file are synthetic.
"""

from __future__ import annotations

import asyncio
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import pyaudio
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame

from app.config.settings import ROOT
from app.voice.elevenlabs_dialogue_direct import synthesize_dialogue
from app.voice.pipeline import create_tts
from app.voice.phone_speech import group_israeli_phone_number, spoken_phone_number

REPETITIONS = 3
PAUSE_SECONDS = 1.5
OUTPUT_DIRECTORY = ROOT / "data" / "tts_phone_tests"

# These are synthetic audio-test strings. They are never dialed or stored as leads.
TEST_NUMBERS = (
    "0521234567",
    "0500000000",
    "0522222222",
    "0545050505",
    "0588080808",
    "0531010101",
    "0509090909",
    "0549876543",
    "0587707070",
    "0536006600",
)

@dataclass(frozen=True)
class TestRepresentation:
    mode: str
    tts_input: str


def group_israeli_mobile_number(canonical: str) -> str:
    """Return the numeric display form used by the diagnostic's numeric mode."""
    if len(canonical) != 10 or not canonical.startswith("05"):
        raise ValueError("Expected a ten-digit Israeli mobile number beginning with 05")
    return group_israeli_phone_number(canonical)


def spoken_hebrew_phone_number(canonical: str) -> str:
    """Spell a phone number as feminine Hebrew digits, retaining natural groups."""
    return spoken_phone_number(canonical, "he")


def representations(canonical: str) -> tuple[TestRepresentation, TestRepresentation]:
    # There is no production phone-to-TTS formatter yet. Numeric mode deliberately
    # supplies the common grouped display text, preserving the current raw-number
    # behavior for comparison with the explicit spoken form.
    return (
        TestRepresentation("numeric", group_israeli_mobile_number(canonical)),
        TestRepresentation("spoken-hebrew", spoken_hebrew_phone_number(canonical)),
    )


def estimated_audio_seconds(text: str) -> float:
    """Conservative local estimate; provider responses do not return per-call cost."""
    return max(3.0, len(text.split()) * 0.48 + text.count(",") * 0.25)


def estimate_plan(settings) -> dict[str, float | int | None]:
    inputs = [representation.tts_input for number in TEST_NUMBERS
              for representation in representations(number)]
    audio_seconds = sum(estimated_audio_seconds(text) for text in inputs) * REPETITIONS
    cost_usd = audio_seconds / 60 * 0.015 if (
        settings.tts_provider == "openai" and settings.tts_model == "gpt-4o-mini-tts"
    ) else None
    return {
        "requests": len(inputs) * REPETITIONS,
        "estimated_audio_seconds": audio_seconds,
        "estimated_cost_usd": cost_usd,
    }


def create_standalone_tts(settings):
    """Create the configured service and initialize it without a pipeline worker."""
    service = create_tts(settings)
    # PipelineWorker normally assigns this in FrameProcessor.setup(). The
    # diagnostic intentionally has no pipeline, so it supplies OpenAI/Gemini's
    # existing 24 kHz application output rate itself.
    service._sample_rate = 24000
    return service


def _save_wav(path: Path, audio: bytes, sample_rate: int, channels: int) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(audio)


async def synthesize(service, text: str, context_id: str) -> tuple[bytes, int, int]:
    """Request TTS directly and collect PCM audio without starting a Pipecat pipeline."""
    chunks: list[bytes] = []
    sample_rate = 24000
    channels = 1
    async for frame in service.run_tts(text, context_id):
        if isinstance(frame, ErrorFrame):
            raise RuntimeError("TTS provider returned an error")
        if isinstance(frame, TTSAudioRawFrame):
            chunks.append(frame.audio)
            sample_rate = frame.sample_rate
            channels = frame.num_channels
    if not chunks:
        raise RuntimeError("TTS provider returned no audio")
    return b"".join(chunks), sample_rate, channels


def print_plan(settings, plan: dict[str, float | int | None]) -> None:
    print("Hebrew phone-number TTS diagnostic")
    voice = "configured ElevenLabs voice" if settings.tts_provider == "elevenlabs" else settings.tts_voice
    print(f"Provider/model/voice: {settings.tts_provider} / {settings.tts_model} / {voice}")
    print(f"Planned TTS requests: {plan['requests']} (10 numbers × 2 modes × {REPETITIONS} repetitions)")
    print(f"Estimated generated audio: about {plan['estimated_audio_seconds'] / 60:.1f} minutes")
    if plan["estimated_cost_usd"] is None:
        print("Estimated API cost: unavailable for this provider/model; check its billing dashboard.")
    else:
        print(f"Estimated API cost: about ${plan['estimated_cost_usd']:.2f} (audio-duration estimate, not a quote)")
    print("No STT, LLM, receptionist pipeline, calls, or business data are used.\n")


async def run_phone_number_tts_test(settings, *, dry_run: bool = False) -> int:
    """Play all synthetic cases and save each PCM result as a local WAV file."""
    if settings.tts_provider not in {"openai", "gemini", "elevenlabs"}:
        raise ValueError("TTS_PROVIDER must be openai, gemini, or elevenlabs")
    missing = [name for name, value in (
        ("TTS_MODEL", settings.tts_model),
        ("TTS_VOICE", settings.tts_voice if settings.tts_provider != "elevenlabs" else "present"),
        ("OPENAI_API_KEY", settings.api_key if settings.tts_provider == "openai" else "present"),
        ("GEMINI_API_KEY", settings.gemini_api_key if settings.tts_provider == "gemini" else "present"),
        ("ELEVENLABS_API_KEY", settings.elevenlabs_api_key if settings.tts_provider == "elevenlabs" else "present"),
        ("ELEVENLABS_VOICE_ID", settings.elevenlabs_voice_id if settings.tts_provider == "elevenlabs" else "present"),
    ) if not value]
    if missing:
        raise ValueError("Required for phone TTS test: " + ", ".join(missing))

    plan = estimate_plan(settings)
    print_plan(settings, plan)
    for index, number in enumerate(TEST_NUMBERS, start=1):
        for representation in representations(number):
            print(f"{index:02d} {representation.mode}: {number} -> {representation.tts_input}")
    if dry_run:
        print("Dry run complete: no TTS request or audio playback was made.")
        return 0

    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    service = None if settings.tts_provider == "elevenlabs" else create_standalone_tts(settings)
    audio = pyaudio.PyAudio()
    successes = 0
    latencies: list[float] = []
    try:
        for index, canonical in enumerate(TEST_NUMBERS, start=1):
            for representation in representations(canonical):
                for repetition in range(1, REPETITIONS + 1):
                    print(
                        f"\nTEST {index}/10\nPhone: {group_israeli_mobile_number(canonical)}\n"
                        f"Canonical: {canonical}\nMode: {representation.mode}\n"
                        f"TTS input: {representation.tts_input}\nRepetition: {repetition}/{REPETITIONS}",
                        flush=True,
                    )
                    started = time.perf_counter()
                    try:
                        if settings.tts_provider == "elevenlabs":
                            pcm, rate, channels, _ = await synthesize_dialogue(
                                api_key=settings.elevenlabs_api_key,
                                voice_id=settings.elevenlabs_voice_id,
                                model=settings.tts_model,
                                text=representation.tts_input,
                            )
                        else:
                            pcm, rate, channels = await synthesize(
                                service, representation.tts_input, f"phone-{index}-{representation.mode}-{repetition}"
                            )
                        latency = time.perf_counter() - started
                        latencies.append(latency)
                        filename = f"{index:02d}_{representation.mode}_rep{repetition}.wav"
                        _save_wav(OUTPUT_DIRECTORY / filename, pcm, rate, channels)
                        stream = audio.open(
                            format=pyaudio.paInt16, channels=channels, rate=rate,
                            output=True, output_device_index=settings.output_device,
                        )
                        try:
                            stream.write(pcm)
                        finally:
                            stream.stop_stream()
                            stream.close()
                        successes += 1
                        print(f"Completed in {latency:.2f}s; saved: {OUTPUT_DIRECTORY / filename}", flush=True)
                    except Exception as exc:
                        print(f"FAILED {index:02d}/{representation.mode}/rep{repetition}: {type(exc).__name__}", flush=True)
                        raise
                    if repetition < REPETITIONS:
                        await asyncio.sleep(PAUSE_SECONDS)
    finally:
        audio.terminate()
        client = getattr(service, "_client", None) if service else None
        close = getattr(client, "close", None)
        if close:
            await close()

    average = sum(latencies) / len(latencies) if latencies else 0
    print(f"\nCompleted {successes}/{plan['requests']} TTS requests.")
    print(f"Average request-to-audio latency: {average:.2f}s")
    print(f"Saved WAV files: {OUTPUT_DIRECTORY}")
    return 0
