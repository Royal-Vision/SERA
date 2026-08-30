"""OpenAI-compatible provider -- Phase 07.

One implementation for every /v1/chat/completions clone (vLLM, llama.cpp,
LM Studio, OpenRouter). Differences worth isolating here: whether the server
honours parallel tool calls, whether it returns arguments as a JSON string or an
object, and whether it supports a strict/structured-output mode at all.
"""


from openai_codex import Codex

with Codex() as codex:
    thread = codex.thread_start()
    print(thread)