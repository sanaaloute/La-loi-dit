"""One-shot provider health check: pings ollama, openrouter and tokenfree.

Uses the real .env settings and the same client resolution as model_router.
Run:  .venv/Scripts/python.exe verify_providers_tmp.py
"""

import asyncio
import time

from backend.core.config import get_settings
from backend.core.llm import LLMClient
from backend.core.model_router import _provider_api_key

CHECKS = [
    # (label, provider, catalog model id or None for the .env default model)
    ("ollama (default .env)", "ollama", None),
    ("openrouter", "openrouter", "openrouter/deepseek/deepseek-chat"),
    ("tokenfree", "tokenfree", "tokenfree/gemini-2.5-flash"),
]


async def check(label: str, provider: str, model_id: str | None) -> None:
    settings = get_settings()
    kwargs = {"provider": provider, "api_key": _provider_api_key(provider, settings)}
    if model_id is not None:
        kwargs["model"] = model_id
    elif provider == settings.llm_provider.lower():
        pass  # use settings.llm_model / llm_api_base as-is
    client = LLMClient(settings, **kwargs)
    start = time.monotonic()
    try:
        answer = await asyncio.wait_for(
            client.complete(
                "You are a health-check probe. Answer with exactly: ok",
                "Réponds uniquement par : ok",
                max_tokens=512,
            ),
            timeout=90,
        )
        elapsed = time.monotonic() - start
        status = "OK  " if answer.strip() else "EMPTY"
        print(f"{status} {label:22s} model={client.model:45s} {elapsed:5.1f}s  -> {answer.strip()[:80]!r}")
    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"FAIL  {label:22s} model={client.model:45s} {elapsed:5.1f}s  -> {exc}")


async def main() -> None:
    settings = get_settings()
    print(f"default provider: {settings.llm_provider}  model: {settings.llm_model}  base: {settings.llm_api_base}")
    print(f"keys present: llm={'yes' if settings.llm_api_key else 'NO'} "
          f"openrouter={'yes' if settings.openrouter_api_key else 'NO'} "
          f"tokenfree={'yes' if settings.tokenfree_api_key else 'NO'}")
    print("-" * 100)
    for label, provider, model_id in CHECKS:
        await check(label, provider, model_id)


if __name__ == "__main__":
    asyncio.run(main())
