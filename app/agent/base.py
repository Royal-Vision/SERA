"""
    Tool protocol, registry and executor -- Tool Contract SRS §01. Step 2 · Phase 02.

    Sits on contracts.py and under everything else. Imported before the first protocol
    frame: contracts.py ~8 ms BEFORE pydantic moved into it, this file ~148 ms
    (pydantic dominates), registry construction ~80 ms. That is most of the 400 ms
    handshake budget, so nothing else may join the fast path.

    TOOL-006: nothing -- not a model, hook, plugin, MCP server or client -- invokes a
    tool implementation outside ToolExecutor. That single chokepoint is what makes
    every requirement below enforceable in one place.
"""

# NOTE ->> Import discipline: stdlib + pydantic ONLY. `import langgraph.graph` = ~1800 ms.


# ==============================================================================
# 1 · Tool  --  Protocol or ABC?
# ==============================================================================

# CONFLICT ->> SRS §Python reference contract uses Protocol[InputT, OutputT].
# CONFLICT ->> Phase 02 §2 explicitly chose an ABC, because nearly every tool wants the
# CONFLICT ->> same defaults and overrides exactly one -- a Protocol makes every tool
# CONFLICT ->> reimplement all of them.
# CONFLICT ->> Recommendation: ABC that STRUCTURALLY SATISFIES the Protocol. Keep the
# CONFLICT ->> Protocol as the published type for MCP/plugin adapters that cannot inherit
# CONFLICT ->> from our base class; keep the ABC as what built-ins actually subclass.
# CONFLICT ->> Decide before writing the first tool -- retrofitting changes every file.

# NOTE ->> Members: spec: ToolSpec (class attribute -- one per tool CLASS, not instance).
# NOTE ->> json_schema(): built from spec.input_model.model_json_schema(). Cache it --
# NOTE ->> it is rebuilt on every registry snapshot otherwise.

# -- the two methods a tool actually writes -------------------------------------
# NOTE ->> async validate_semantics(args, ctx) -> None
# NOTE ->>   Facts JSON Schema cannot express: the path exists and is a regular file;
# NOTE ->>   the file was read completely and has not changed since; offset/limit are
# NOTE ->>   mutually valid; an edit target occurs exactly once unless replace_all; the
# NOTE ->>   LSP/MCP server is connected; the task/agent/cron exists AND is owned by the
# NOTE ->>   caller; mutually exclusive options are not both set; the requested timeout is
# NOTE ->>   within policy; the operation is legal in the current mode.
# NOTE ->>   IT MUST NOT PERFORM THE SIDE EFFECT. Bound it and make it cancellable --
# NOTE ->>   it runs before permission, so it runs on calls that will be denied.
# NOTE ->> async execute(args, ctx) -> OutputT
# NOTE ->>   Returns the TYPED output. Not a ToolResult -- the executor wraps it, so a
# NOTE ->>   tool cannot forge a status or skip output validation.

# -- per-args facts (this is where phase 02's four hooks went) -------------------
# NOTE ->> permission_facts(args, ctx) -> PermissionFacts. Defaults to the spec, and
# NOTE ->> bash overrides it: bash(ls) is side_effect=none, bash(rm -rf) is destructive.
# NOTE ->> This is the ONE reason those hooks take args, and why "just make them
# NOTE ->> properties" is unrecoverable later.
# NOTE ->> human_summary is what the user actually approves -- write it for a human
# NOTE ->> reading a prompt at 2am, not for a log parser.


# ==============================================================================
# 2 · The stages  --  TOOL-005, and they are distinct on purpose
# ==============================================================================

# NOTE ->> resolve -> STRUCTURAL validate -> canonicalise+hash -> SEMANTIC validate ->
# NOTE ->> pre-hooks -> permission -> reserve attempt -> locks -> execute -> validate
# NOTE ->> output -> bound/spill -> post-hooks -> commit -> model result.
# NOTE ->> Collapsing any two of these is how a check gets skipped on one path only.

# NOTE ->> RESOLVE against the REGISTRY SNAPSHOT sent to that model call (TOOL-013), not
# NOTE ->> the live registry. A tool added mid-turn must not become callable retroactively,
# NOTE ->> and a replay must not silently run against a changed schema.

# NOTE ->> STRUCTURAL validation happens EXACTLY ONCE, at the untrusted boundary:
# NOTE ->>     args = tool.spec.input_model.model_validate(raw_arguments)
# NOTE ->> Input models use ConfigDict(extra="forbid", strict=True). Field-local coercion
# NOTE ->> for a specific provider quirk is allowed if documented and tested; GLOBAL
# NOTE ->> permissive coercion is not, because it makes permission matching unsafe --
# NOTE ->> "1" and 1 must not be two spellings of one approved argument.

# NOTE ->> CANONICALISE, then hash (TOOL-011):
# NOTE ->>     argument_hash = SHA-256(name || version || canonical_json)
# NOTE ->> The canonical object is the ONLY one passed to policy, the approval UI,
# NOTE ->> execution, persistence and audit. Approve one object, run a different one and
# NOTE ->> the approval meant nothing.
# NOTE ->> A hook may PROPOSE a replacement -- revalidate it and give it a NEW hash before
# NOTE ->> approval. A hook that can edit arguments after approval is a privilege bypass.


# ==============================================================================
# 3 · Permission handshake
# ==============================================================================

# NOTE ->> The tool supplies PermissionFacts. It never prompts a terminal itself.
# NOTE ->> Policy returns allow / deny / a DURABLE ask. Durable because an ask outlives
# NOTE ->> the worker: persist the request and release the graph worker, then resume on
# NOTE ->> the answer. Holding a coroutine open for a human is how a restart loses the turn.
# NOTE ->> Approval binds the EXACT argument hash. Changed arguments -> permission_expired,
# NOTE ->> never a silent re-run.
# NOTE ->> A denial still produces a provider-valid terminal tool result. The model must
# NOTE ->> SEE the refusal and adapt; killing the turn loses all in-flight work.


# ==============================================================================
# 4 · Execution, and the no-raise rule
# ==============================================================================

# NOTE ->> Reserve the attempt BEFORE process or network execution (TOOL-008). Crash
# NOTE ->> recovery needs a record that says "this may have run", or an ambiguous loss is
# NOTE ->> indistinguishable from never having started.
# NOTE ->> Exactly ONE terminal state per started attempt (TOOL-009), by COMPARE-AND-SET.
# NOTE ->> A late worker must never turn a cancelled call into succeeded.
# NOTE ->> Exception mapping:
# NOTE ->>   ValidationError    -> schema_invalid      (model can correct)
# NOTE ->>   semantic failure   -> semantic_invalid    (model can gather context)
# NOTE ->>   TimeoutError       -> timed_out
# NOTE ->>   CancelledError     -> RE-RAISE after recording cancelled. The one exception
# NOTE ->>                         that must escape -- swallowing it breaks user
# NOTE ->>                         cancellation and turn timeouts alike.
# NOTE ->>   output adapter     -> output_invalid      (OUR defect: do NOT ask the model
# NOTE ->>                         to repair it, it cannot)
# NOTE ->>   anything else      -> internal, full traceback to RESTRICTED logs (stderr),
# NOTE ->>                         correlation id only to the model.
# NOTE ->> Use typed domain exceptions, not `except Exception` branching on message text.


# ==============================================================================
# 5 · Concurrency  --  class AND resource keys
# ==============================================================================

# NOTE ->> TOOL-016: two calls in one model response may overlap only when EVERY pair is
# NOTE ->> concurrency-compatible AND their lock modes do not conflict. Read+write to the
# NOTE ->> same path in one batch is a correctness bug, not a performance question.
# NOTE ->> Lock keys come from VALIDATED args -- fs:/repo/a.py:write, repo:/repo:git-write.
# NOTE ->> Key on canonical file identity where you can; a path string does not survive
# NOTE ->> a rename.
# NOTE ->> TOOL-017: results are appended to model history in ORIGINAL TOOL-CALL ORDER,
# NOTE ->> however the completions actually interleave. Otherwise the transcript is
# NOTE ->> non-deterministic and replay diverges.
# NOTE ->> Release locks in a `finally`. A lock held by a crashed attempt deadlocks the
# NOTE ->> next turn, and the symptom appears nowhere near the cause.


# ==============================================================================
# 6 · Timeouts, cancellation, progress, artifacts
# ==============================================================================

# NOTE ->> Four deadlines: queue, wall-clock, optional idle-output, plus the parent-run
# NOTE ->> cancellation token. ctx.budget_for(spec, requested) already clips the tool's
# NOTE ->> policy by what remains of the turn.
# NOTE ->> Cancellation: persist cancel_requested -> signal -> stop child processes or
# NOTE ->> remote requests -> wait ONLY the bounded cleanup period -> persist cancelled or
# NOTE ->> failed_cleanup -> release locks in finally.
# NOTE ->> Coroutine cancellation is NOT enough for a subprocess: kill the process GROUP
# NOTE ->> and collect remaining output.
# NOTE ->> Progress is advisory and droppable (TOOL-014): throttle, coalesce adjacent text
# NOTE ->> deltas, never emit an unredacted secret, always finish with ONE durable terminal
# NOTE ->> event.
# NOTE ->> Over max_inline_result_bytes (TOOL-010): write full bytes to the artifact store,
# NOTE ->> compute media type + size + sha256, attach an immutable ArtifactRef, send the
# NOTE ->> model a head/tail preview plus the id. A path in model content is NOT an
# NOTE ->> authorization token -- artifact reads still require authenticated REST.


# ==============================================================================
# 7 · Registry + snapshot
# ==============================================================================

# NOTE ->> Three layers: built-ins, trusted plugins (after manifest validation), dynamic
# NOTE ->> MCP (after capability negotiation). Duplicate canonical names are REJECTED;
# NOTE ->> built-ins win over dynamic unless a namespace policy says otherwise.
# NOTE ->> Snapshot at run start: name + aliases, version, implementation source,
# NOTE ->> input/output SCHEMA HASHES, permission + capability metadata, availability
# NOTE ->> result and reason, deferred/always_load, MCP server identity and annotations.
# NOTE ->> for_mode(PLAN) keeps only spec.plan_mode_safe -- mutating tools are NOT OFFERED,
# NOTE ->> not merely denied. A model that cannot see Write does not spend a round-trip
# NOTE ->> being refused.
# NOTE ->> ToolSearch returns names FROM THE SNAPSHOT. Discovering a deferred tool adds its
# NOTE ->> schema to the next request -- it does not grant permission to call it.

# NOTE ->> MCP schemas are UNTRUSTED NETWORK INPUT. Namespace as mcp__<server>__<tool>,
# NOTE ->> keep the original names separately, sanitise and length-limit descriptions,
# NOTE ->> validate the JSON Schema before registration, reject unsupported features with
# NOTE ->> an availability reason -- and treat a MISSING readOnlyHint/destructiveHint/
# NOTE ->> openWorldHint as UNSAFE. Central policy runs even when the server claims
# NOTE ->> read-only; an annotation is a claim by the thing being policed.


# ==============================================================================
# 8 · Hooks
# ==============================================================================

# NOTE ->> Pre: observe REDACTED canonical input, add context, propose a replacement
# NOTE ->> (revalidated + rehashed), return allow/ask/deny SUBJECT TO immutable safety
# NOTE ->> policy. Post: observe terminal status and bounded output, and MUST NOT rewrite
# NOTE ->> the committed result.
# NOTE ->> Every hook: explicit source + version, timeout, cancellation, deterministic
# NOTE ->> ordering, durable decision reason.
# NOTE ->> Failure policy per hook: security hooks FAIL CLOSED, advisory hooks fail open
# NOTE ->> with a warning. A hard deny and workspace trust are never overridable.


# ==============================================================================
# 9 · PermissionPolicy  --  STUB until Step 8
# ==============================================================================

# NOTE ->> check(facts, ctx) -> PermissionResult. For now: ALLOW, rule="stub".
# NOTE ->> Keep the SIGNATURE final even while the body is a stub -- the executor starts
# NOTE ->> calling it in Step 6 and must not change when the body gets real.
# NOTE ->> check() stays PURE and SYNCHRONOUS: no prompting, no I/O, no await. That purity
# NOTE ->> is exactly what makes Step 8's gate a table test with no mocking.
# NOTE ->> The real cascade (Step 8), where THE ORDER IS THE SECURITY PROPERTY:
# NOTE ->>   1. always_deny            -- wins in EVERY mode, BYPASS included
# NOTE ->>   2. PLAN and mutating      -- before allow-lists, so no earlier approval wins
# NOTE ->>   3. side_effect is none    -- allow
# NOTE ->>   4. BYPASS                 -- allow
# NOTE ->>   5. allow-lists            -- persisted, then session
# NOTE ->>   6. accept_edits and risk < high
# NOTE ->>   7. otherwise              -- ask


# ==============================================================================
# Gate  ->  tests/agent/test_base.py
# ==============================================================================

# NOTE ->> run() with invalid args returns schema_invalid and does NOT raise.
# NOTE ->> a tool whose execute() raises ZeroDivisionError returns internal, not a crash.
# NOTE ->> a tool that sleeps past its timeout returns timed_out.
# NOTE ->> CancelledError propagates after a cancelled record is written.
# NOTE ->> an implementation returning the wrong type returns output_invalid.
# NOTE ->> duplicate canonical name -> registry raises.
# NOTE ->> for_mode(PLAN) excludes every tool whose side_effect is not none.
# NOTE ->> no execution occurs before an ALLOW decision.
# NOTE ->> changed arguments invalidate a prior approval (permission_expired).
# NOTE ->> every started attempt reaches EXACTLY ONE terminal state.
# NOTE ->> terminal event order is stable under parallel completion (TOOL-017).
# NOTE ->> duplicate delivery does not duplicate a deduplicated side effect.
# NOTE ->> a deny rule cannot be overridden by a lower-priority allow or by a hook.
# NOTE ->> `import app.agent.base` pulls in neither langchain nor langgraph.
