import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

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
    twilio_account_sid: str = ""
    twilio_auth_token: str = field(default="", repr=False)
    twilio_public_url: str = ""
    telephony_host: str = "127.0.0.1"
    telephony_port: int = 8000
    google_calendar_credentials_file: str = field(default="", repr=False)
    google_calendar_id: str = ""
    whatsapp_access_token: str = field(default="", repr=False)
    whatsapp_phone_number_id: str = ""

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

        def integer(name, default):
            value = os.getenv(name, "").strip()
            try:
                return int(value) if value else default
            except ValueError:
                raise ValueError(f"{name} must be an integer") from None

        def boolean(name, default=False):
            value = os.getenv(name, "").strip().lower()
            if not value:
                return default
            if value not in {"true", "false"}:
                raise ValueError(f"{name} must be true or false")
            return value == "true"
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
            log_transcripts=boolean("LOG_TRANSCRIPTS"),
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", "").strip(),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
            twilio_public_url=(
                os.getenv("TWILIO_PUBLIC_URL", "").strip()
                or os.getenv("PUBLIC_BASE_URL", "").strip()
            ).rstrip("/"),
            telephony_host=os.getenv("TELEPHONY_HOST", "").strip() or "127.0.0.1",
            telephony_port=integer("TELEPHONY_PORT", 8000),
            google_calendar_credentials_file=os.getenv(
                "GOOGLE_CALENDAR_CREDENTIALS_FILE", ""
            ).strip(),
            google_calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "").strip(),
            whatsapp_access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip(),
            whatsapp_phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
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

    def validate_telephony(self):
        self.validate_voice()
        missing = [name for name, value in (
            ("TWILIO_ACCOUNT_SID", self.twilio_account_sid),
            ("TWILIO_AUTH_TOKEN", self.twilio_auth_token),
        ) if not value]
        if missing:
            raise ValueError("Required before Twilio telephony: " + ", ".join(missing))
        if not (1 <= self.telephony_port <= 65535):
            raise ValueError("TELEPHONY_PORT must be between 1 and 65535")
        if self.twilio_public_url:
            parsed = urlsplit(self.twilio_public_url)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("TWILIO_PUBLIC_URL must be an HTTPS origin without a path")

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
