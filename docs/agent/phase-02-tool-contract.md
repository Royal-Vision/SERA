# Phase 02 — The Tool Contract

**Effort:** 1 day · **Depends on:** [01](phase-01-runtime.md)
**The most load-bearing 200 lines in the system.**

---

## 1. Why this phase exists

Here is the trap. Writing a tool as a plain function feels obviously right:

```python
async def read_file(path: str) -> str: ...
```

It works. It is readable. And it has thrown away every piece of information the rest of
the system needs:

| Later phase | Question it must answer | Answerable from a bare function? |
|---|---|---|
| [05](phase-05-tool-engine.md) | Can these two calls run concurrently? | ✗ |
| [05](phase-05-tool-engine.md) | Did this tool exceed its budget? | ✗ |
| [11](phase-11-permissions.md) | Does this need approval? | ✗ |
| [11](phase-11-permissions.md) | Is this offered in plan mode? | ✗ |
| [12](phase-12-guardrails.md) | Can this reach the network? | ✗ |

You cannot recover this by inspection. `read_file` is safe and `write_file` is not, and
no amount of signature analysis tells you which. **The metadata has to be declared.**

`docs/tools.md` gets this right — `is_read_only` and `is_concurrency_safe` are in its
`Tool` protocol. This phase makes them the centre of the design rather than an
afterthought.

---

## 2. The architecture decision

### `ToolSpec` as a separate frozen record

Not attributes scattered on the class. One frozen dataclass, validated at construction:

```python
@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    category: ToolCategory
    risk: RiskLevel
    read_only: bool
    concurrency_safe: bool
    timeout_s: float
    budget_ms: int
    description: str = ""
    cache_ttl_s: int | None = None
    plan_mode_safe: bool = True
```

Why frozen and validated: **the invariants are enforceable and worth enforcing.**

```python
def __post_init__(self):
    if self.read_only and self.risk is not RiskLevel.SAFE:
        raise ValueError(f"{self.name}: read_only tools must be SAFE")
    if self.cache_ttl_s is not None and not self.read_only:
        raise ValueError(f"{self.name}: only read_only tools may be cached")
    if not self.read_only and self.plan_mode_safe:
        raise ValueError(f"{self.name}: mutating tools cannot be plan_mode_safe")
    if self.timeout_s <= 0:
        raise ValueError(f"{self.name}: timeout_s must be positive")
```

A tool that is mutating *and* plan-mode-safe is a security hole, and this makes it a
crash at import rather than a bug in production.

### ABC, not Protocol

`docs/tools.md` uses a `Protocol`. We use an ABC because nearly every tool wants the
same defaults — `read_only` from the spec, `permission_key` derived from arguments,
uniform timeout handling — and an ABC lets a tool override only the one it cares about.

The motivating case is `bash`: `bash(ls)` is read-only, `bash(rm -rf)` is not. So the
behaviour flags take **arguments**, defaulting to the spec:

```python
def is_read_only(self, args) -> bool:        return self.spec.read_only
def is_concurrency_safe(self, args) -> bool: return self.spec.concurrency_safe
def risk_for(self, args) -> RiskLevel:       return self.spec.risk
def permission_key(self, args) -> str:       return self.name
```

That signature choice is what makes per-command shell permissions possible in
[Phase 11](phase-11-permissions.md) without redesigning anything.

### Risk levels

```python
class RiskLevel(StrEnum):
    SAFE   = "safe"     # read-only, confined to project → auto-allow
    LOW    = "low"      # writes, easily reverted (new file)
    MEDIUM = "medium"   # modifies existing state (edit)
    HIGH   = "high"     # destructive, irreversible, or escapes the project
```

### Permission modes

```python
class PermissionMode(StrEnum):
    DEFAULT      = "default"        # read-only auto, everything else prompts
    ACCEPT_EDITS = "accept_edits"   # edits auto, shell + HIGH still prompt
    PLAN         = "plan"           # nothing may mutate, at all
    BYPASS       = "bypass"         # everything auto — non-interactive/CI only
```

---

## 3. `AgentContext` — dependencies, not globals

```python
@dataclass
class AgentContext:
    cwd: Path
    permission: PermissionContext
    session_id: str
    request_id: str
    provider: str
    model: str
    deadline_at: float = 0.0        # monotonic
    started_at: float = field(default_factory=time.monotonic)
    in_progress_tool_ids: set[str] = field(default_factory=set)
    confirmed_tool_calls: set[str] = field(default_factory=set)
    on_progress: Callable[[str], None] | None = None
    extras: dict[str, Any] = field(default_factory=dict)
```

This is `docs/tools.md`'s `ToolContext`. Three things earn their place:

**`deadline_at`, not just `timeout_s`.** A tool that cannot finish before the turn
deadline should return a partial result rather than blow the budget:

```python
def budget_for(self, spec: ToolSpec) -> float:
    return min(spec.timeout_s, self.remaining_s())
```

Never let a slow tool become a slow product.

**`extras` as a per-turn scratchpad.** Phase 05's file-state tracker lives here, so it
is scoped to the turn and nothing leaks between requests.

**`on_progress` optional.** Only the interactive path supplies it. A batch invocation
uses the same context type with no dependency on frontend state — the point
`docs/tools.md` makes about not passing REPL callbacks into a batch context.

### The single path chokepoint

```python
def resolve_in_project(self, raw: str) -> Path:
    root = self.cwd
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Path escapes the project root: {raw}")
    return resolved
```

**`resolve()` before comparing**, so symlinks cannot step outside. Every filesystem tool
routes through this. It is one function, and it retires the entire path-traversal
category — the Phase 00 principle that the cheapest guardrail is one you never evaluate.

---

## 4. `ToolResult` and the no-raise rule

```python
@dataclass(slots=True)
class ToolResult:
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    truncated: int = 0
```

```python
async def run(self, raw: dict, ctx: AgentContext) -> ToolResult:
    try:
        args = self.validate(raw)
    except ValidationError as exc:
        return ToolResult.error(...)
    timeout = ctx.budget_for(self.spec)
    if timeout <= 0:
        return ToolResult.error(f"{self.name} skipped: turn deadline reached.")
    try:
        async with asyncio.timeout(timeout):
            return await self.call(args, ctx)
    except TimeoutError:
        return ToolResult.error(f"{self.name} timed out after {timeout:.1f}s.")
    except asyncio.CancelledError:
        raise                       # cancellation must propagate
    except Exception as exc:
        logger.exception("Tool %s failed", self.name)
        return ToolResult.error(f"{self.name} failed: {type(exc).__name__}: {exc}")
```

**`content` is what the model sees, so keep it terse.** In an agent loop, tool output is
re-sent on every subsequent turn — verbose results are a compounding cost, not a one-off.

**`CancelledError` must re-raise.** Swallowing it breaks user cancellation and turn
timeouts, and it is the one exception that must escape.

---

## 5. The registry

```python
class ToolRegistry:
    def register(self, tool) -> None: ...          # rejects duplicate names
    def get(self, name) -> Tool | None: ...
    def spec(self, name) -> ToolSpec | None: ...
    def for_mode(self, mode) -> list[Tool]: ...
    def schemas(self, mode) -> list[dict]: ...
```

**`for_mode` is the important one.** In plan mode, mutating tools are not merely denied —
they are **not offered**:

```python
if mode is PermissionMode.PLAN:
    return [t for t in self._by_name.values() if t.spec.plan_mode_safe]
```

A model that cannot see `write_file` does not waste a round-trip trying it and being
refused. Against a `roundtrips ≤ 4` budget, that is a meaningful saving — and it is a
better user experience than a refusal.

---

## 6. Schema design rules

The input model *is* the prompt. These rules reduce Phase 05's repair workload:

| Rule | Why |
|---|---|
| **`extra="forbid"` always** | Models hallucinate parameters. Silent acceptance produces wrong behaviour; loud rejection produces a correction |
| **Flat, not nested** | Models are markedly worse at nested objects than flat fields |
| **Enums over free strings** | Turns an open-ended guess into a closed choice |
| **Few required fields** | Every required field is a chance to omit one |
| **Descriptive names** | `file_path` beats `path`; `old_string` beats `target` |
| **Constraints in the schema** | `ge=1, le=10000` — Phase 05 renders these into error messages |
| **`description` on every field** | It is the only documentation the model gets |

---

## 7. Import discipline

`contracts.py` and `base.py` are imported before the first protocol frame. **stdlib +
pydantic only.** No langchain, no langgraph, no torch.

Measured on this machine: `contracts.py` ≈ 8 ms, `base.py` ≈ 148 ms (pydantic dominates),
registry construction ≈ 80 ms. Acceptable inside the 400 ms handshake budget — but it is
most of it, so nothing else may join the fast path.

---

## 8. Gate

- [ ] Every `ToolSpec` invariant raises at construction, with a test per invariant
- [ ] `resolve_in_project()` rejects `../`, absolute escapes, and symlink escapes
- [ ] `Tool.run()` never propagates a non-`CancelledError` exception
- [ ] `registry.for_mode(PLAN)` excludes every mutating tool
- [ ] `import app.agent.base` does not pull in langchain or langgraph
- [ ] Duplicate tool registration raises

---

← [Previous: Phase 01 — Runtime & Protocol](phase-01-runtime.md) · [Index](README.md) · [Next: Phase 03 — First Tool](phase-03-read-tool.md) →
