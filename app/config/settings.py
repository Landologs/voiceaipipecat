import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    api_key: str = field(default="", repr=False)
    stt_provider: str = "openai"
    llm_provider: str = "openai"
    tts_provider: str = "openai"
    stt_model: str = ""
    llm_model: str = ""
    tts_model: str = ""
    tts_voice: str = ""
    input_device: int | None = None
    output_device: int | None = None
    business_path: Path = ROOT / "app/config/business.example.json"
    log_transcripts: bool = False

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
        def device(name):
            value = os.getenv(name, "").strip()
            try:
                return int(value) if value else None
            except ValueError:
                raise ValueError(f"{name} must be an integer device index") from None
        return cls(
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            **{name: os.getenv(name.upper(), "").strip() or "openai"
               for name in ("stt_provider", "llm_provider", "tts_provider")},
            **{name: os.getenv(name.upper(), "").strip()
               for name in ("stt_model", "llm_model", "tts_model", "tts_voice")},
            input_device=device("AUDIO_INPUT_DEVICE_INDEX"),
            output_device=device("AUDIO_OUTPUT_DEVICE_INDEX"),
            business_path=ROOT / (os.getenv("BUSINESS_CONFIG_PATH") or "app/config/business.example.json"),
            log_transcripts=os.getenv("LOG_TRANSCRIPTS", "").lower() == "true",
        )

    def validate_voice(self):
        for name in ("stt_provider", "llm_provider", "tts_provider"):
            if getattr(self, name) != "openai":
                raise ValueError(f"{name.upper()}: only the openai adapter is implemented")
        missing = [name.upper() for name in ("stt_model", "llm_model", "tts_model", "tts_voice")
                   if not getattr(self, name)]
        if not self.api_key:
            missing.append("OPENAI_API_KEY")
        if missing:
            raise ValueError("Required before live voice: " + ", ".join(missing))
