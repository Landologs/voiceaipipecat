"""Local audio diagnostics; never calls a provider or saves microphone audio."""
import math
from array import array
import pyaudio


def diagnose(input_device=None, output_device=None, *, exercise=False):
    audio = pyaudio.PyAudio()
    try:
        devices = []
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            devices.append({key: info[key] for key in
                            ("index", "name", "maxInputChannels", "maxOutputChannels")})
        report = {"devices": devices}
        if not exercise:
            return report
        for label, index, rate in (("microphone", input_device, 16000),
                                   ("speaker", output_device, 24000)):
            stream = None
            try:
                kwargs = {"input": True, "input_device_index": index} if label == "microphone" else {
                    "output": True, "output_device_index": index}
                stream = audio.open(format=pyaudio.paInt16, channels=1, rate=rate,
                                    frames_per_buffer=1024, **kwargs)
                if label == "microphone":
                    samples = array("h", stream.read(rate, exception_on_overflow=False))
                    report[label] = {"opened": True, "peak": max(map(abs, samples), default=0)}
                else:
                    tone = array("h", (int(900 * math.sin(2 * math.pi * 440 * n / rate))
                                       for n in range(rate // 3)))
                    stream.write(tone.tobytes())
                    report[label] = {"opened": True, "tone_written": True}
            except (OSError, ValueError) as exc:
                report[label] = {"opened": False, "error": str(exc)}
            finally:
                if stream:
                    stream.stop_stream()
                    stream.close()
        return report
    finally:
        audio.terminate()
