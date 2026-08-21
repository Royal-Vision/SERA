"""SERA CLI.

    sera doctor                     check providers, tools, runtime
    sera tools                      list tools and their specs
    sera run "<prompt>"             one-shot agent turn
    sera chat                       interactive session

Import discipline is the whole performance story for a CLI. `import langgraph.graph`
costs ~1800 ms on this machine, so nothing at module scope may reach it. Commands that
do not need the graph never pay for it:

    sera --help      ~50 ms
    sera tools      ~250 ms
    sera run        ~2 s (graph import, once, behind a status line)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.agent.perf import apply_performance_mode

USAGE = """sera - a coding agent

  sera doctor                     check providers, tools and runtime
  sera tools                      list available tools
  sera run "<prompt>"             one-shot agent turn
  sera chat                       interactive session

Options:
  -p, --provider   ollama | codex | antigravity   (default: ollama)
  -m, --model      model name                     (default: provider's default)
  -C, --cwd        project root                   (default: current directory)
      --mode       default | accept_edits | plan | bypass
      --max-steps  agent loop ceiling             (default: 12)
"""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sera", add_help=False)
    p.add_argument("command", nargs="?", default="help")
    p.add_argument("prompt", nargs="*")
    p.add_argument("-p", "--provider", default=None)
    p.add_argument("-m", "--model", default=None)
    p.add_argument("-C", "--cwd", default=".")
    p.add_argument("--mode", default="default")
    p.add_argument("--max-steps", type=int, default=12)
    p.add_argument("-h", "--help", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    info = apply_performance_mode()
    args = _parser().parse_args(argv if argv is not None else sys.argv[1:])

    if args.help or args.command in ("help", "-h", "--help"):
        print(USAGE)
        return 0

    from app.configs.config import settings

    provider = args.provider or settings.DEFAULT_PROVIDER

    if args.command == "doctor":
        return _doctor(info, provider)
    if args.command == "tools":
        return _tools()
    if args.command in ("run", "chat"):
        import asyncio

        return asyncio.run(_agent(args, provider))

    print(f"Unknown command: {args.command}\n\n{USAGE}", file=sys.stderr)
    return 2


# ──────────────────────────────────────────────────────────────────────────────


def _doctor(info: dict, provider: str) -> int:
    import asyncio

    from app.agent.base import build_default_registry
    from app.agent.providers.base import health, list_ollama_models, list_providers

    print("runtime")
    for k, v in info.items():
        print(f"  {k:16} {v}")

    registry = build_default_registry()
    print(f"\ntools ({len(registry)})")
    print(f"  {', '.join(sorted(t.name for t in registry))}")

    print("\nproviders")

    async def probe() -> None:
        for spec in list_providers():
            ok, detail = await health(spec.id)
            mark = "up  " if ok else "down"
            star = " *" if spec.id == provider else "  "
            print(f" {star}{mark} {spec.id:14} {spec.base_url or '(unset)':38} {detail}")
            if ok and spec.id == "ollama":
                models = await list_ollama_models()
                if models:
                    print(f"        models: {', '.join(models[:8])}"
                          f"{' ...' if len(models) > 8 else ''}")

    asyncio.run(probe())
    return 0


def _tools() -> int:
    from app.agent.base import build_default_registry

    registry = build_default_registry()
    print(f"{'name':<12} {'risk':<8} {'ro':<4} {'par':<4} {'budget':<8} description")
    print("-" * 100)
    for tool in sorted(registry, key=lambda t: t.name):
        s = tool.spec
        print(
            f"{s.name:<12} {s.risk.value:<8} "
            f"{'yes' if s.read_only else 'no':<4} "
            f"{'yes' if s.concurrency_safe else 'no':<4} "
            f"{str(s.budget_ms) + 'ms':<8} {(s.description or '')[:52]}"
        )
    return 0


async def _agent(args, provider: str) -> int:
    from app.agent.base import build_default_registry
    from app.agent.contracts import PermissionMode
    from app.agent.perf import enable_eager_tasks, freeze_after_warmup

    enable_eager_tasks()

    try:
        mode = PermissionMode(args.mode)
    except ValueError:
        print(f"Unknown mode {args.mode!r}. "
              f"Valid: {', '.join(m.value for m in PermissionMode)}", file=sys.stderr)
        return 2

    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir():
        print(f"Not a directory: {cwd}", file=sys.stderr)
        return 2

    registry = build_default_registry()

    print(f"loading graph...", end="\r", file=sys.stderr, flush=True)
    from app.agent.graph.agent import build_agent, make_context

    graph = build_agent(registry, provider, args.model, max_steps=args.max_steps)
    print(" " * 20, end="\r", file=sys.stderr)

    freeze_after_warmup()

    def progress(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    ctx = make_context(cwd, provider, args.model or "", mode, on_progress=progress)
    print(f"sera · {provider} · {cwd.name} · mode={mode.value} · "
          f"{len(registry)} tools", file=sys.stderr)

    if args.command == "run":
        prompt = " ".join(args.prompt).strip()
        if not prompt:
            print('usage: sera run "your prompt"', file=sys.stderr)
            return 2
        await _turn(graph, ctx, prompt)
        return 0

    print("(ctrl-c or 'exit' to quit)\n", file=sys.stderr)
    history: list = []
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.lower() in ("exit", "quit", ":q"):
            return 0
        if not line:
            continue
        try:
            history = await _turn(graph, ctx, line, history)
        except KeyboardInterrupt:
            print("\n[interrupted]", file=sys.stderr)


async def _turn(graph, ctx, prompt: str, history: list | None = None) -> list:
    """Run one turn, streaming tokens as they arrive."""
    from langchain_core.messages import AIMessage, HumanMessage

    messages = [*(history or []), HumanMessage(content=prompt)]
    config = {"configurable": {"agent_context": ctx}}

    final: list = messages
    printed_any = False

    async for chunk, meta in graph.astream(
        {"messages": messages, "steps": 0}, config, stream_mode="messages"
    ):
        if isinstance(chunk, AIMessage) and chunk.content:
            text = chunk.content if isinstance(chunk.content, str) else ""
            if text:
                print(text, end="", flush=True)
                printed_any = True

    if printed_any:
        print()

    state = await graph.aget_state(config) if hasattr(graph, "aget_state") else None
    return state.values["messages"] if state and state.values.get("messages") else final


if __name__ == "__main__":
    raise SystemExit(main())
