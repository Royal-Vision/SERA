"""Bash -- P0 · process.spawn. Step 12 · Phase 11b + Tool Catalog §P0.

LAST, deliberately. Everything before this was safe BY STRUCTURE -- confined to the
project, read-before-edit, no network. bash cannot be made safe by structure, so it
is the one tool that genuinely needs the permission gate, and shipping the gate on
a tool that can `rm -rf` is the wrong place to discover a bug in it.

Default target decision: command-specific policy; otherwise ASK.
"""

# NOTE ->> Do not start this before Step 8's PermissionPolicy is real and table-tested.


# ==============================================================================
# 1 · Input / output
# ==============================================================================

# NOTE ->> extra="forbid". command: str (non-empty). timeout: int | None (MILLISECONDS,
# NOTE ->> capped). description: str. run_in_background: bool = False.
# NOTE ->> INTERNAL FIELDS ARE EXCLUDED FROM THE MODEL SCHEMA (catalog): sandbox overrides,
# NOTE ->> simulated-edit data, approval metadata. If the model can name a field, the model
# NOTE ->> can set it -- a "dangerously skip sandbox" flag in the public schema is a
# NOTE ->> permission bypass with a docstring.
# NOTE ->> Output: stdout, stderr, interruption state, background task id/state,
# NOTE ->> persisted-output metadata, optional structured content.


# ==============================================================================
# 2 · Permission  --  parse, never pattern-match
# ==============================================================================

# NOTE ->> permission_key(args) -> f"bash({args.command})". THIS is why permission_key took
# NOTE ->> args back in Step 2.
# NOTE ->> Parse ALL compound commands and redirections before deciding. `ls && rm -rf /`
# NOTE ->> is not an `ls`. A substring or prefix check on the raw string is defeated by
# NOTE ->> `;`, `&&`, `||`, `|`, `$(...)`, backticks, newlines and `>` -- decide per parsed
# NOTE ->> segment, and DENY when the parse fails. Unparsable means unknown, and unknown
# NOTE ->> asks or denies; it never falls through to allow.
# NOTE ->> is_read_only(args) varies BY VERB: ls, cat, pwd yes. Be conservative. `git` is
# NOTE ->> NOT read-only, because of `git push`.
# NOTE ->> bash(git status) must NOT grant bash(rm) -- that is a Step 8 gate assertion, and
# NOTE ->> it is the whole reason allow-list entries are permission KEYS and not tool names.
# NOTE ->> Apply, in order: hard deny-list, exact/prefix rules, sandbox eligibility,
# NOTE ->> protected paths, network effects, credential access, destructive semantics.
# NOTE ->> NEVER CONCATENATE APPROVAL METADATA INTO THE COMMAND (catalog). Capture the
# NOTE ->> executable/argv parse as EVIDENCE alongside the command, never inside it.


# ==============================================================================
# 3 · Process lifecycle
# ==============================================================================

# NOTE ->> PROCESS GROUP, and kill the GROUP on timeout (start_new_session=True, then
# NOTE ->> os.killpg). A killed `npm test` that orphans node processes leaves them holding
# NOTE ->> ports and file locks, and the next run fails for a reason that looks unrelated.
# NOTE ->> Bounded ENVIRONMENT -- allowlist, do not inherit os.environ wholesale. It carries
# NOTE ->> API keys straight into anything the model runs.
# NOTE ->> cwd containment. Output cap ~64 KB; persist beyond ~30K chars as an artifact and
# NOTE ->> hand the model a reference.
# NOTE ->> Ship an unbypassable default deny-list. It does not stop a determined attacker;
# NOTE ->> it stops an ACCIDENT, which is far more common.


# ==============================================================================
# 4 · Concurrency + idempotency
# ==============================================================================

# NOTE ->> ONLY proven read-only commands may overlap. Everything else serialises on the
# NOTE ->> workspace, plus semantic locks -- `git-write` is the one the catalog names, since
# NOTE ->> two concurrent git commands corrupt the index.
# NOTE ->> NON-IDEMPOTENT unless a narrow classifier proves a specific invocation pure.
# NOTE ->> NEVER AUTO-RETRY AFTER AMBIGUOUS PROCESS LOSS: the command may have completed and
# NOTE ->> the connection dropped. Retrying `git push` is noise; retrying `rm -rf build &&
# NOTE ->> make install` twice is not.


# ==============================================================================
# Gate  ->  tests/agent/test_tools_bash.py
# ==============================================================================

# NOTE ->> a 10-second timeout leaves ZERO orphan processes, verified per platform.
# NOTE ->> `ls && rm -rf /` is not classified as read-only.
# NOTE ->> an unparsable command denies rather than allowing.
# NOTE ->> bash(git status) approved does not allow bash(rm -rf build).
# NOTE ->> the model cannot set the sandbox-override field (it is not in the schema).
# NOTE ->> the child environment contains no key from the parent allowlist gaps.
# NOTE ->> output over the cap is persisted and referenced, not inlined.
