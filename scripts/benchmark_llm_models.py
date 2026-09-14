"""Run a small, comparable streaming latency benchmark for configured OpenAI LLMs."""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from openai import AsyncOpenAI

from app.agent.prompt_loader import load_prompt
from app.config.business_config import BusinessConfig
from app.config.settings import Settings

DEFAULT_MODELS = ("gpt-5-nano", "gpt-4.1-mini", "gpt-5.6-luna")
CASES = (
    "הלקוח אומר: יש לי נזילה במזגן. עני במשפט קצר ושאלי שאלה אחת.",
    "הלקוח אומר: כן, שבע בערב מתאים לי. מה השלב הבא?",
    "הלקוח אומר: לא, מחר בבוקר עדיף לי. הציעי חלופה קצרה.",
    "הלקוח אומר: אני מתקשר מרמת גן והמזגן לא עובד. בקשי פרט חסר אחד.",
    "הלקוח אומר: תודה, זה הכול. סיימי את השיחה במשפט הסיום שהוגדר.",
)


async def run_one(client: AsyncOpenAI, model: str, system_prompt: str, user_prompt: str) -> dict:
    started = time.perf_counter()
    first_token = None
    chunks: list[str] = []
    try:
        request = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": True,
            "max_completion_tokens": 240,
        }
        if model == "gpt-5-nano":
            request["reasoning_effort"] = "minimal"
        elif model.startswith("gpt-5"):
            request["reasoning_effort"] = "none"
        stream = await client.chat.completions.create(**request)
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                if first_token is None:
                    first_token = time.perf_counter()
                chunks.append(delta)
        finished = time.perf_counter()
        return {
            "status": "ok",
            "first_token_seconds": None if first_token is None else first_token - started,
            "total_seconds": finished - started,
            "response": "".join(chunks),
        }
    except Exception as exc:  # Keep the benchmark running for the other models.
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
            "total_seconds": time.perf_counter() - started,
        }


async def main() -> None:
    load_dotenv()
    models = tuple(filter(None, os.getenv("BENCHMARK_MODELS", "").split(","))) or DEFAULT_MODELS
    settings = Settings.load()
    if not settings.api_key:
        raise SystemExit("OPENAI_API_KEY is missing")
    business = BusinessConfig.load(settings.business_path)
    system_prompt = load_prompt(business, datetime.now(timezone.utc), calendar_path=settings.calendar_path)
    results: dict[str, list[dict]] = {}
    async with AsyncOpenAI(api_key=settings.api_key, timeout=60, max_retries=0) as client:
        for model in models:
            print(f"START {model}", flush=True)
            model_results = []
            for index, case in enumerate(CASES, 1):
                result = await run_one(client, model, system_prompt, case)
                result["case"] = index
                model_results.append(result)
                if result["status"] == "ok":
                    first = result["first_token_seconds"]
                    print(
                        f"{model} case={index} first={'n/a' if first is None else f'{first:.3f}s'} "
                        f"total={result['total_seconds']:.3f}s",
                        flush=True,
                    )
                else:
                    print(f"{model} case={index} ERROR {result['error_type']}: {result['error']}", flush=True)
            results[model] = model_results

    output = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "models": results,
    }
    target = Path("demo/results") / f"llm_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"RESULT_FILE {target}")
    for model, rows in results.items():
        ok = [row for row in rows if row["status"] == "ok"]
        ok_with_tokens = [row for row in ok if row["first_token_seconds"] is not None]
        if ok_with_tokens:
            print(
                f"SUMMARY {model} ok={len(ok)}/5 "
                f"first_avg={statistics.mean(row['first_token_seconds'] for row in ok_with_tokens):.3f}s "
                f"total_avg={statistics.mean(row['total_seconds'] for row in ok):.3f}s"
            )


if __name__ == "__main__":
    asyncio.run(main())
