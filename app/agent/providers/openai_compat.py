"""OpenAI-compatible provider -- Phase 07.

One implementation for every /v1/chat/completions clone (vLLM, llama.cpp,
LM Studio, OpenRouter). Differences worth isolating here: whether the server
honours parallel tool calls, whether it returns arguments as a JSON string or an
object, and whether it supports a strict/structured-output mode at all.

NOTE ->> Codex sign-in below is NOT that protocol -- it drives the codex binary
NOTE ->> over JSON-RPC. It lives here only until providers/codex.py exists.
"""

import asyncio
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from openai_codex import AsyncCodex, AsyncDeviceCodeLoginHandle
from pydantic import BaseModel

from app.configs.config import get_settings

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

CODEX_AUTH_FILE = Path.home() / ".codex" / "auth.json"


class OpenAIVerification(BaseModel):
    verification_url: str
    user_code: str


class CodexAccount(BaseModel):
    """Who the codex binary is signed in as right now.

    `kind` matters as much as the fact of a login: an "apiKey" account is
    already usable, but it is NOT a ChatGPT account, and running the device-code
    flow over it replaces the stored key with ChatGPT tokens.
    """

    kind: str
    email: str | None = None
    plan: str | None = None


@dataclass(slots=True)
class CodexLogin:
    """A device-code login in flight, holding the live client it belongs to.

    Two phases, because the user leaves to enter the code on another device:
    hand `verification` to them, then await `wait()` when they are back. The
    subprocess is already up by now -- `AsyncCodex` starts it on the first
    awaited call -- so every path out of here must reach `aclose()` or a process
    is orphaned. `AsyncCodex` is itself an async context manager, so a caller
    that wants block-scoped cleanup can `async with` the client `wait()` returns.
    """

    codex: AsyncCodex
    verification: OpenAIVerification
    handle: AsyncDeviceCodeLoginHandle

    async def wait(self) -> AsyncCodex:
        """Wait for the login to complete, then hand back the signed-in client."""
        completed = await self.handle.wait()
        if not completed.success:
            await self.aclose()
            raise RuntimeError(f"codex device-code login failed: {completed.error}")
        return self.codex

    async def cancel(self) -> None:
        """Abandon the attempt and release the subprocess."""
        try:
            await self.handle.cancel()
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        """Close the client -- a no-op if it never started, and idempotent."""
        await self.codex.close()


class CodexAuth:
    """Device-code sign-in against a ChatGPT account."""

    async def current_account(self, codex: AsyncCodex | None = None) -> CodexAccount | None:
        """Report the credential codex already holds, or None if there is none.

        Credentials outlive the process -- the binary keeps them in its own auth
        store -- so this is the pre-flight that decides whether a login is worth
        starting at all. Pass a live client to reuse it and keep it open; with
        no client this spawns its own and closes it before returning.
        """
        owned = codex is None
        codex = codex or AsyncCodex()
        try:
            state = await codex.account()
            print(f"state: {state} | {type(state)}")
        finally:
            if owned:
                await codex.close()
        if state.account is None:
            return None
        account = state.account.root
        plan = getattr(account, "plan_type", None)
        return CodexAccount(
            kind=account.type,
            email=getattr(account, "email", None),
            plan=plan.value if plan is not None else None,
        )

    async def login_with_codex(self, codex: AsyncCodex | None = None) -> CodexLogin:
        """Start a device-code login, reusing the client the pre-check spawned.

        Ownership: this closes only a client it spawned itself. One you passed
        in stays yours until the returned `CodexLogin` takes it over.
        """
        owned = codex is None
        codex = codex or AsyncCodex()
        try:
            handle = await codex.login_chatgpt_device_code()
        except Exception:
            if owned:
                await codex.close()
            raise
        return CodexLogin(
            codex=codex,
            verification=OpenAIVerification(
                verification_url=handle.verification_url,
                user_code=handle.user_code,
            ),
            handle=handle,
        )

    async def __call__(self, model: str | None = None) -> "BaseChatModel":
        """Ensure a credential exists, then hand back the LLM for the deep agent.

        The codex client is only ever the proof that a credential is on disk --
        it is closed before returning, because deepagents talks the wire format,
        not JSON-RPC to the binary. What survives the call is the key.
        """
        codex = AsyncCodex()
        try:
            account = await self.current_account(codex=codex)

            if account is None:
                login = await self.login_with_codex(codex)

                print(
                    f"open {login.verification.verification_url} "
                    f"and enter {login.verification.user_code}"
                )

                codex = await login.wait()
                account = await self.current_account(codex=codex)
        finally:
            await codex.close()

        return get_model(model)


def _codex_api_key() -> str:
    """The key to call /v1/chat/completions with -- settings first, codex store second.

    ChatGPT (device-code) auth deliberately fails here: it mints OAuth tokens for
    the codex backend, not a key any OpenAI-compatible endpoint accepts. Only the
    apiKey auth mode leaves something usable behind.
    """
    settings = get_settings()
    if settings.OPENAI_API_KEY:
        return settings.OPENAI_API_KEY
    if not CODEX_AUTH_FILE.exists():
        raise RuntimeError(
            "no OpenAI credential -- set OPENAI_API_KEY or run `codex login --api-key`"
        )
    stored = json.loads(CODEX_AUTH_FILE.read_text(encoding="utf-8"))
    key = stored.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            f"codex auth_mode is {stored.get('auth_mode')!r}, which holds no API key -- "
            "set OPENAI_API_KEY or run `codex login --api-key`"
        )
    return key


@lru_cache(maxsize=1)
def _http_client() -> httpx.AsyncClient:
    """One pooled client for every model built here. Keepalive is the whole point."""
    settings = get_settings()
    return httpx.AsyncClient(
        http2=True,
        timeout=settings.PROVIDER_TIMEOUT_S,
        limits=httpx.Limits(
            max_keepalive_connections=settings.PROVIDER_MAX_KEEPALIVE,
            keepalive_expiry=90,
        ),
    )


@lru_cache(maxsize=16)
def get_model(model: str | None = None, temperature: float = 0.0) -> "BaseChatModel":
    """The LLM instance for `create_deep_agent(model=...)`.

    Cached on (model, temperature): the second call reuses the client AND its
    connection pool, which is the 50-300 ms of TLS setup that per-request
    construction used to pay before the model was asked anything.

    NOTE ->> This is the wire-format path, NOT `AsyncCodex`. The codex client
    NOTE ->> drives the binary over JSON-RPC and is not a `BaseChatModel`, so it
    NOTE ->> cannot be handed to deepagents -- only its credential crosses over.
    """
    from langchain_openai import ChatOpenAI  # lazy -- keeps ~200 ms off the fast path

    settings = get_settings()
    return ChatOpenAI(
        model=model or settings.CODEX_DEFAULT_MODEL,
        temperature=temperature,
        api_key=_codex_api_key(),
        base_url=settings.CODEX_BASE_URL,
        http_async_client=_http_client(),
    )


if __name__ == "__main__":

    async def main() -> None:
        llm = await CodexAuth()()
        print(f"model ready: {llm.model_name} -- pass this to create_deep_agent(model=...)")

    asyncio.run(main())
