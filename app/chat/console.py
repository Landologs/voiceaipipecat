import asyncio
import logging
import sys
from datetime import datetime, timezone

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError, RateLimitError

from app.agent.prompt_loader import load_prompt
from app.knowledge.adaptation import TurnContextEngine

logger = logging.getLogger(__name__)


def _client_options(settings) -> dict:
    options = {
        "api_key": settings.llm_api_key,
        "timeout": 60,
        "max_retries": 0,
    }
    if settings.llm_base_url:
        options["base_url"] = settings.llm_base_url
    return options


async def request_reply(client, settings, messages: list[dict[str, str]]) -> str:
    """Send one text turn through the configured LLM without STT or TTS."""
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
    )
    return response.choices[0].message.content or ""


async def run_text(settings, business, initial_message: str | None = None) -> int:
    settings.validate_text()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    one_shot = initial_message is not None
    messages = [{
        "role": "system",
        "content": load_prompt(business, datetime.now(timezone.utc),
                               calendar_path=settings.calendar_path),
    }]
    turn_context = TurnContextEngine()
    logger.info("Text chat started: LLM=%s model=%s", settings.llm_provider, settings.llm_model)

    async with AsyncOpenAI(**_client_options(settings)) as client:
        while True:
            if initial_message is None:
                try:
                    message = (await asyncio.to_thread(input, "Вы: ")).strip()
                except EOFError:
                    print()
                    return 0
            else:
                message, initial_message = initial_message.strip(), None

            if message.lower() in {"/exit", "/quit", "exit", "quit"}:
                return 0
            if not message:
                continue

            messages.append({"role": "user", "content": message})
            try:
                request_messages = turn_context.augment_messages(messages, message)
                reply = await request_reply(client, settings, request_messages)
            except RateLimitError:
                messages.pop()
                logger.error("LLM rate limit reached. Wait for the provider quota window and retry.")
                if not one_shot:
                    continue
                return 2
            except AuthenticationError:
                logger.error("LLM authentication failed. Check the configured API key.")
                return 2
            except APIConnectionError:
                messages.pop()
                logger.error("Could not connect to the LLM provider.")
                if not one_shot:
                    continue
                return 2
            except APIStatusError as exc:
                messages.pop()
                logger.error("LLM request failed with HTTP status %s.", exc.status_code)
                if not one_shot:
                    continue
                return 2

            messages.append({"role": "assistant", "content": reply})
            print(f"Агент: {reply}\n")
            if one_shot:
                return 0
            if not reply:
                logger.warning("The LLM returned an empty response.")
