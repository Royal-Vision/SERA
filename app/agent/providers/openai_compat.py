"""OpenAI-compatible provider -- Phase 07.

One implementation for every /v1/chat/completions clone (vLLM, llama.cpp,
LM Studio, OpenRouter). Differences worth isolating here: whether the server
honours parallel tool calls, whether it returns arguments as a JSON string or an
object, and whether it supports a strict/structured-output mode at all.

NOTE ->> Codex sign-in below is NOT that protocol -- it drives the codex binary
NOTE ->> over JSON-RPC. It lives here only until providers/codex.py exists.
"""

from dataclasses import dataclass

from openai_codex import Codex, DeviceCodeLoginHandle
from pydantic import BaseModel


class OpenAIVerification(BaseModel):
    verification_url: str
    user_code: str


@dataclass(slots=True)
class CodexLogin:
    """A device-code login in flight, holding the live client it belongs to.

    Two phases, because the user leaves to enter the code on another device:
    hand `verification` to them, then call `wait()` when they are back. The
    client is already spawned -- `Codex()` starts its subprocess in __init__ --
    so every path out of here must reach `close()` or a process is orphaned.
    """

    codex: Codex
    verification: OpenAIVerification
    handle: DeviceCodeLoginHandle

    def wait(self) -> Codex:
        """Block until the login completes, then hand back the signed-in client."""
        completed = self.handle.wait()
        if not completed.success:
            self.close()
            raise RuntimeError(f"codex device-code login failed: {completed.error}")
        return self.codex

    def cancel(self) -> None:
        """Abandon the attempt and release the subprocess."""
        try:
            self.handle.cancel()
        finally:
            self.close()

    def close(self) -> None:
        self.codex.close()


class CodexAuth:
    """Device-code sign-in against a ChatGPT account."""

    def login_with_codex(self) -> CodexLogin:
        codex = Codex()
        try:
            handle = codex.login_chatgpt_device_code()
        except Exception:
            codex.close()
            raise
        return CodexLogin(
            codex=codex,
            verification=OpenAIVerification(
                verification_url=handle.verification_url,
                user_code=handle.user_code,
            ),
            handle=handle,
        )
