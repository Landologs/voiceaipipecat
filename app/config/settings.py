import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = ROOT / "demo"
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_TEST_STT_MODEL = "gemini-3.5-transcribe-live"
GEMINI_TEST_LLM_MODEL = "gemini-3.6-flash"
GEMINI_TEST_TTS_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_TEST_TTS_VOICE = "Kore"


@dataclass
class Settings:
    api_key: str = field(default="", repr=False)
    gemini_api_key: str = field(default="", repr=False)
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
    demo_mode: bool = False

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        default_provider = "gemini" if gemini_key and not openai_key else "openai"

        def provider(name):
            return os.getenv(name, "").strip().lower() or default_provider

        def device(name):
            value = os.getenv(name, "").strip()
            try:
                return int(value) if value else None
            except ValueError:
                raise ValueError(f"{name} must be an integer device index") from None
        stt_provider = provider("STT_PROVIDER")
        llm_provider = provider("LLM_PROVIDER")
        tts_provider = provider("TTS_PROVIDER")
        return cls(
            api_key=openai_key,
            gemini_api_key=gemini_key,
            stt_provider=stt_provider,
            llm_provider=llm_provider,
            tts_provider=tts_provider,
            stt_model=os.getenv("STT_MODEL", "").strip()
            or (GEMINI_TEST_STT_MODEL if stt_provider == "gemini" else ""),
            llm_model=os.getenv("LLM_MODEL", "").strip()
            or (GEMINI_TEST_LLM_MODEL if llm_provider == "gemini" else ""),
            tts_model=os.getenv("TTS_MODEL", "").strip()
            or (GEMINI_TEST_TTS_MODEL if tts_provider == "gemini" else ""),
            tts_voice=os.getenv("TTS_VOICE", "").strip()
            or (GEMINI_TEST_TTS_VOICE if tts_provider == "gemini" else ""),
            input_device=device("AUDIO_INPUT_DEVICE_INDEX"),
            output_device=device("AUDIO_OUTPUT_DEVICE_INDEX"),
            business_path=ROOT / (os.getenv("BUSINESS_CONFIG_PATH") or "app/config/business.example.json"),
            log_transcripts=os.getenv("LOG_TRANSCRIPTS", "").lower() == "true",
        )

    def for_demo(self):
        """Lock a run to the workspace demo directory and its data only."""
        return replace(self, business_path=DEMO_ROOT / "business.json", demo_mode=True)

    def validate_voice(self):
        for name in ("stt_provider", "llm_provider", "tts_provider"):
            if getattr(self, name) not in {"openai", "gemini"}:
                raise ValueError(f"{name.upper()} must be openai or gemini")
        missing = [name.upper() for name in ("stt_model", "llm_model", "tts_model", "tts_voice")
                   if not getattr(self, name)]
        providers = {self.stt_provider, self.llm_provider, self.tts_provider}
        if "openai" in providers and not self.api_key:
            missing.append("OPENAI_API_KEY")
        if "gemini" in providers and not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if missing:
            raise ValueError("Required before live voice: " + ", ".join(missing))

    def validate_text(self):
        if self.llm_provider not in {"openai", "gemini"}:
            raise ValueError("LLM_PROVIDER must be openai or gemini")
        missing = []
        if not self.llm_model:
            missing.append("LLM_MODEL")
        if self.llm_provider == "openai" and not self.api_key:
            missing.append("OPENAI_API_KEY")
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if missing:
            raise ValueError("Required before live text chat: " + ", ".join(missing))

    @property
    def llm_api_key(self) -> str:
        return self.gemini_api_key if self.llm_provider == "gemini" else self.api_key

    @property
    def llm_base_url(self) -> str | None:
        return GEMINI_OPENAI_BASE_URL if self.llm_provider == "gemini" else None

    @property
    def calendar_path(self) -> Path | None:
        return DEMO_ROOT / "calendar.json" if self.demo_mode else None

    @property
    def results_path(self) -> Path:
        return DEMO_ROOT / "results" if self.demo_mode else ROOT / "data/calls"

    @property
    def show_live_transcripts(self) -> bool:
        return self.demo_mode or self.log_transcripts
