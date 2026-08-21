"""Provider registry -- Codex / Antigravity / Ollama behind one warm interface.

The single most important thing here: **chat model instances are cached**. Constructing
one per request creates a new httpx client, a new connection pool and a new TLS
handshake, which costs 50-300 ms before the model is asked anything. That alone would
blow the latency budget.

Codex and Antigravity both speak the OpenAI wire format, so they share one adapter and
differ only by base_url. Ollama gets its own because `keep_alive` and `num_ctx` have no
OpenAI analogue -- and `keep_alive` is the difference between a warm model and a
multi-second reload on every call.

langchain_* imports are deliberately function-local: this module is importable from the
CLI's fast path without paying for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from app.configs.config import settings

ProviderKind = Literal["ollama", "openai_compat"]


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    id: str
    kind: ProviderKind
    base_url: str
    default_model: str
    supports_tools: bool = True
    api_key_setting: str | None = None

    @property
    def api_key(self) -> str | None:
        if not self.api_key_setting:
            return None
        return getattr(settings, self.api_key_setting, None)


def _specs() -> dict[str, ProviderSpec]:
    return {
        "ollama": ProviderSpec(
            id="ollama",
            kind="ollama",
            base_url=settings.OLLAMA_BASE_URL,
            default_model=settings.OLLAMA_DEFAULT_MODEL,
        ),
        "codex": ProviderSpec(
            id="codex",
            kind="openai_compat",
            base_url=settings.CODEX_BASE_URL or "https://api.openai.com/v1",
            default_model=settings.CODEX_DEFAULT_MODEL,
            api_key_setting="OPENAI_API_KEY",
        ),
        "antigravity": ProviderSpec(
            id="antigravity",
            kind="openai_compat",
            base_url=settings.ANTIGRAVITY_BASE_URL or "",
            default_model=settings.ANTIGRAVITY_DEFAULT_MODEL,
            api_key_setting="GOOGLE_API_KEY",
        ),
    }


def get_spec(provider: str) -> ProviderSpec:
    specs = _specs()
    if provider not in specs:
        raise ValueError(
            f"Unknown provider {provider!r}. Available: {', '.join(sorted(specs))}"
        )
    return specs[provider]


def list_providers() -> list[ProviderSpec]:
    return list(_specs().values())


@lru_cache(maxsize=16)
def get_model(provider: str, model: str | None = None, temperature: float = 0.0) -> Any:
    """Return a cached, warm chat model.

    lru_cache is the whole point -- the second call for the same (provider, model)
    reuses the client and its connection pool.
    """
    spec = get_spec(provider)
    name = model or spec.default_model

    if spec.kind == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=name,
            base_url=spec.base_url,
            temperature=temperature,
            # Without keep_alive Ollama evicts the model after ~5 minutes and the next
            # request pays a multi-second reload.
            keep_alive=settings.OLLAMA_KEEP_ALIVE,
            num_ctx=settings.OLLAMA_NUM_CTX,
            num_predict=settings.OLLAMA_NUM_PREDICT,
        )

    from langchain_openai import ChatOpenAI

    if not spec.base_url:
        raise ValueError(
            f"{provider} has no base_url configured. "
            f"Set {provider.upper()}_BASE_URL in your .env."
        )

    return ChatOpenAI(
        model=name,
        base_url=spec.base_url,
        api_key=spec.api_key or "not-needed",
        temperature=temperature,
        timeout=settings.PROVIDER_TIMEOUT_S,
        streaming=True,
    )


async def health(provider: str) -> tuple[bool, str]:
    """Cheap reachability probe. Never raises."""
    import httpx

    spec = get_spec(provider)
    if not spec.base_url:
        return False, "no base_url configured"

    url = f"{spec.base_url.rstrip('/')}/api/tags" if spec.kind == "ollama" \
        else f"{spec.base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
        return (resp.status_code < 500), f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001 - a probe must never raise
        return False, f"{type(exc).__name__}"


async def list_ollama_models() -> list[str]:
    import httpx

    spec = get_spec("ollama")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{spec.base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
            return sorted(m["name"] for m in resp.json().get("models", []))
    except Exception:  # noqa: BLE001
        return []
