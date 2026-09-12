import argparse
import asyncio
import json
import logging
import sys

from app.config.business_config import BusinessConfig
from app.config.settings import Settings


def main():
    parser = argparse.ArgumentParser(description="Local Hebrew receptionist")
    parser.add_argument("command", choices=[
        "check", "audio-devices", "audio-check", "phone-tts-test", "text", "voice", "telephony",
        "demo-text", "demo-voice", "demo-telephony"
    ])
    parser.add_argument("--message", help="Send one message in text mode and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the phone TTS diagnostic plan without calling a provider")
    args = parser.parse_args()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Disable third-party debug/error bodies that may contain caller content or credentials.
    from loguru import logger
    logger.remove()
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("openai").setLevel(logging.CRITICAL)
    try:
        settings = Settings.load()
        demo = args.command.startswith("demo-")
        command = args.command.removeprefix("demo-")
        if demo:
            settings = settings.for_demo()
        business = BusinessConfig.load(settings.business_path)
        if command == "check":
            print("Business configuration valid. Offline mode: no provider connections.")
            try:
                settings.validate_voice()
                print("Live settings present; credentials/model access not tested.")
            except ValueError as exc:
                print(str(exc))
        elif command in ("audio-devices", "audio-check"):
            from app.voice.audio_check import diagnose
            report = diagnose(settings.input_device, settings.output_device,
                              exercise=command == "audio-check")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if command == "audio-check" and any(
                    not report[name]["opened"] for name in ("microphone", "speaker")):
                return 1
        elif command == "phone-tts-test":
            from app.voice.phone_number_tts_test import run_phone_number_tts_test
            return asyncio.run(run_phone_number_tts_test(settings, dry_run=args.dry_run))
        elif command == "text":
            settings.validate_text()
            from app.chat.console import run_text
            return asyncio.run(run_text(settings, business, args.message))
        elif command == "telephony":
            from app.telephony.twilio_server import run_server
            run_server(settings, business)
        else:
            settings.validate_voice()
            from app.voice.pipeline import run_voice
            asyncio.run(run_voice(settings, business))
        return 0
    except (ValueError, FileNotFoundError) as exc:
        # Validation details can echo input values; show configuration field names only.
        from pydantic import ValidationError
        if isinstance(exc, ValidationError):
            logging.error("Invalid configuration fields: %s", ", ".join(
                ".".join(map(str, e["loc"])) for e in exc.errors()))
        else:
            logging.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logging.error("Application failed (%s). Check audio devices and provider configuration.",
                      type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
