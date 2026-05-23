import contextlib
import threading
import time
from typing import Any, Awaitable, Optional

from prometheus_client import Counter, Histogram, start_http_server

from app.configs.logger import get_logger

logger = get_logger()

# USD per 1M tokens. Local/self-hosted models default to 0 cost until you set
# your own internal pricing.
MODEL_COST_PER_1M = {
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "gpt-4o": {"input": 5.00, "output": 15.00},
    "gpt-4o-mini-2024-07-18": {"input": 0.15, "output": 0.60},
    "gpt-4.1-2025-04-14": {"input": 2.00, "output": 8.00},
    "Qwen/Qwen2.5-1.5B-Instruct": {"input": 0.0, "output": 0.0},
    "Qwen/Qwen2.5-3B-Instruct": {"input": 0.0, "output": 0.0},
    "Qwen/Qwen2.5-VL-7B-Instruct": {"input": 0.0, "output": 0.0},
    "Qwen/Qwen3-4B-Instruct-2507": {"input": 0.0, "output": 0.0},
}

_LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 20, 30, 60, 120)
_TTFT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8)
_STARTED_METRICS_PORTS: set[int] = set()
_STARTED_METRICS_PORTS_LOCK = threading.Lock()


class LLMMetrics:
    """Singleton Prometheus metrics registry for LLM workloads."""

    _instances: dict[str, "LLMMetrics"] = {}
    _lock = threading.Lock()

    def __new__(cls, namespace: str = "llm"):
        with cls._lock:
            instance = cls._instances.get(namespace)
            if instance is None:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[namespace] = instance
        return instance

    def __init__(self, namespace: str = "llm"):
        if self._initialized:
            return

        ns = namespace

        self.calls = Counter(
            f"{ns}_calls_total",
            "Total LLM API calls",
            ["model", "operation", "environment", "status"],
        )
        self.chat_sessions = Counter(
            f"{ns}_chat_sessions_total",
            "Total chat sessions started",
            ["model", "environment"],
        )
        self.chat_messages = Counter(
            f"{ns}_chat_messages_total",
            "Total chat messages sent",
            ["model", "operation", "environment"],
        )
        self.tool_calls = Counter(
            f"{ns}_tool_calls_total",
            "Total tool/function calls",
            ["model", "tool_name", "environment"],
        )
        self.operations = Counter(
            f"{ns}_operations_total",
            "Total agent operations",
            ["operation", "environment"],
        )
        self.errors = Counter(
            f"{ns}_errors_total",
            "Total LLM errors",
            ["model", "error_type", "environment"],
        )
        self.input_tokens = Counter(
            f"{ns}_input_tokens_total",
            "Total input tokens consumed",
            ["model", "environment"],
        )
        self.output_tokens = Counter(
            f"{ns}_output_tokens_total",
            "Total output tokens generated",
            ["model", "environment"],
        )
        self.cost_usd = Counter(
            f"{ns}_estimated_cost_usd_total",
            "Estimated cost in USD",
            ["model", "environment"],
        )
        self.response_duration = Histogram(
            f"{ns}_response_duration_seconds",
            "LLM response duration (full round-trip)",
            ["model", "operation", "environment"],
            buckets=_LATENCY_BUCKETS,
        )
        self.ttft = Histogram(
            f"{ns}_time_to_first_token_seconds",
            "Time to first token",
            ["model", "environment"],
            buckets=_TTFT_BUCKETS,
        )

        self._environment = "production"
        self._initialized = True

    def set_environment(self, env: str) -> None:
        self._environment = env

    def record_tokens(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        environment: Optional[str] = None,
    ) -> None:
        env = environment or self._environment
        input_tokens = max(0, int(input_tokens))
        output_tokens = max(0, int(output_tokens))

        self.input_tokens.labels(model=model, environment=env).inc(input_tokens)
        self.output_tokens.labels(model=model, environment=env).inc(output_tokens)

        costs = MODEL_COST_PER_1M.get(model, {"input": 0.0, "output": 0.0})
        cost = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000
        self.cost_usd.labels(model=model, environment=env).inc(cost)

    def record_usage(
        self,
        model: str,
        usage: Any,
        environment: Optional[str] = None,
    ) -> None:
        if usage is None:
            return

        input_tokens = self._resolve_usage_value(
            usage,
            "input_tokens",
            "prompt_tokens",
            "prompt_token_count",
        )
        output_tokens = self._resolve_usage_value(
            usage,
            "output_tokens",
            "completion_tokens",
            "completion_token_count",
        )

        if input_tokens is None and output_tokens is None:
            return

        self.record_tokens(
            model=model,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            environment=environment,
        )

    def record_ttft(self, model: str, seconds: float, environment: Optional[str] = None) -> None:
        env = environment or self._environment
        self.ttft.labels(model=model, environment=env).observe(seconds)

    @contextlib.contextmanager
    def track_call(
        self,
        model: str,
        operation: str = "chat",
        tool_name: Optional[str] = None,
        environment: Optional[str] = None,
    ):
        env = environment or self._environment
        start = time.perf_counter()
        status = "success"

        self.operations.labels(operation=operation, environment=env).inc()
        if operation == "tool" and tool_name:
            self.tool_calls.labels(model=model, tool_name=tool_name, environment=env).inc()
        elif operation == "chat":
            self.chat_messages.labels(model=model, operation=operation, environment=env).inc()

        try:
            yield self
        except Exception as exc:
            status = "error"
            self.errors.labels(
                model=model,
                error_type=type(exc).__name__,
                environment=env,
            ).inc()
            raise
        finally:
            elapsed = time.perf_counter() - start
            self.calls.labels(
                model=model,
                operation=operation,
                environment=env,
                status=status,
            ).inc()
            self.response_duration.labels(
                model=model,
                operation=operation,
                environment=env,
            ).observe(elapsed)

    @contextlib.asynccontextmanager
    async def track_call_async(
        self,
        model: str,
        operation: str = "chat",
        tool_name: Optional[str] = None,
        environment: Optional[str] = None,
    ):
        env = environment or self._environment
        start = time.perf_counter()
        status = "success"

        self.operations.labels(operation=operation, environment=env).inc()
        if operation == "tool" and tool_name:
            self.tool_calls.labels(model=model, tool_name=tool_name, environment=env).inc()
        elif operation == "chat":
            self.chat_messages.labels(model=model, operation=operation, environment=env).inc()

        try:
            yield self
        except Exception as exc:
            status = "error"
            self.errors.labels(
                model=model,
                error_type=type(exc).__name__,
                environment=env,
            ).inc()
            raise
        finally:
            elapsed = time.perf_counter() - start
            self.calls.labels(
                model=model,
                operation=operation,
                environment=env,
                status=status,
            ).inc()
            self.response_duration.labels(
                model=model,
                operation=operation,
                environment=env,
            ).observe(elapsed)

    async def track_awaitable(
        self,
        awaitable: Awaitable[Any],
        model: str,
        operation: str = "chat",
        tool_name: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> Any:
        async with self.track_call_async(
            model=model,
            operation=operation,
            tool_name=tool_name,
            environment=environment,
        ):
            return await awaitable

    def start_session(self, model: str, environment: Optional[str] = None) -> None:
        env = environment or self._environment
        self.chat_sessions.labels(model=model, environment=env).inc()

    @staticmethod
    def _resolve_usage_value(usage: Any, *names: str) -> Optional[int]:
        for name in names:
            if isinstance(usage, dict) and name in usage and usage[name] is not None:
                return int(usage[name])

            value = getattr(usage, name, None)
            if value is not None:
                return int(value)

        return None


def start_metrics_server(port: int = 8000) -> bool:
    """Start the Prometheus metrics server once per process."""
    with _STARTED_METRICS_PORTS_LOCK:
        if port in _STARTED_METRICS_PORTS:
            return False

        start_http_server(port)
        _STARTED_METRICS_PORTS.add(port)

    logger.info("Prometheus metrics server running on :%s/metrics", port)
    return True


_default_metrics: Optional[LLMMetrics] = None


def get_metrics() -> LLMMetrics:
    global _default_metrics
    if _default_metrics is None:
        _default_metrics = LLMMetrics()
    return _default_metrics
