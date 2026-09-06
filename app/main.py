import argparse
import asyncio
import json
import logging

from app.config.business_config import BusinessConfig
from app.config.settings import Settings


def main():
    parser = argparse.ArgumentParser(description="Local Hebrew receptionist")
    parser.add_argument("command", choices=["check", "audio-devices", "audio-check", "voice"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Disable third-party debug/error bodies that may contain caller content or credentials.
    from loguru import logger
    logger.remove()
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("openai").setLevel(logging.CRITICAL)
    try:
        settings = Settings.load()
        business = BusinessConfig.load(settings.business_path)
        if args.command == "check":
            print("Business configuration valid. Offline mode: no provider connections.")
            try:
                settings.validate_voice()
                print("Live settings present; credentials/model access not tested.")
            except ValueError as exc:
                print(str(exc))
        elif args.command in ("audio-devices", "audio-check"):
            from app.voice.audio_check import diagnose
            report = diagnose(settings.input_device, settings.output_device,
                              exercise=args.command == "audio-check")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if args.command == "audio-check" and any(
                    not report[name]["opened"] for name in ("microphone", "speaker")):
                return 1
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
