"""LangChain chat-model adapter for an authenticated Codex SDK client."""

from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, get_buffer_string
from langchain_core.outputs import ChatGeneration, ChatResult
from openai_codex import AsyncCodex
from pydantic import Field


class CodexChatModel(BaseChatModel):
    """Expose an open ``AsyncCodex`` client through LangChain's async API.

    Each invocation starts an ephemeral Codex thread and sends the complete
    LangChain message history. The caller retains ownership of ``codex``.
    """

    codex: AsyncCodex = Field(exclude=True, repr=False)
    model_name: str = "gpt-5.6-luna"

    @property
    def _llm_type(self) -> str:
        return "codex"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        raise RuntimeError("CodexChatModel is async-only; use ainvoke() or agenerate()")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager, kwargs
        if stop:
            raise ValueError("CodexChatModel does not support stop sequences")

        thread = await self.codex.thread_start(model=self.model_name, ephemeral=True)
        result = await thread.run(get_buffer_string(messages, format="xml"))
        usage = result.usage.total if result.usage else None
        message = AIMessage(
            content=result.final_response or "",
            response_metadata={
                "thread_id": thread.id,
                "turn_id": result.id,
                "duration_ms": result.duration_ms,
            },
            usage_metadata=(
                {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                }
                if usage
                else None
            ),
        )
        return ChatResult(generations=[ChatGeneration(message=message)])
