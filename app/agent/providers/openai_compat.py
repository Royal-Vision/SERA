"""OpenAI-compatible provider -- Phase 07.

One implementation for every /v1/chat/completions clone (vLLM, llama.cpp,
LM Studio, OpenRouter). Differences worth isolating here: whether the server
honours parallel tool calls, whether it returns arguments as a JSON string or an
object, and whether it supports a strict/structured-output mode at all.

NOTE ->> Codex sign-in below is NOT that protocol -- it drives the codex binary
NOTE ->> over JSON-RPC. It lives here only until providers/codex.py exists.
"""

from dataclasses import dataclass

from openai_codex import AsyncCodex, AsyncDeviceCodeLoginHandle
from pydantic import BaseModel


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

    async def login_with_codex(self) -> CodexLogin:
        codex = AsyncCodex()
        try:
            handle = await codex.login_chatgpt_device_code()
        except Exception:
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
