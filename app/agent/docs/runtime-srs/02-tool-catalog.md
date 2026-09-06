# 02 - Complete Tool Catalog

[Runtime SRS index](README.md) | [Docs start page](../README.md) | [Diagram standard](../diagram-standard.md)

> Related deep guides: [Skills](../skills-architecture/README.md),
> [Multi-agent control](../cli-architecture/03-multi-agent-control.md), and
> [Sandbox/resources](../execution-architecture/01-sandbox-and-resources.md).

## Purpose and completeness

This catalog covers every tool family found in the current registry, including:

- source-backed built-in tools;
- feature-gated built-ins;
- dynamically generated MCP and structured-output tools;
- aliases retained for transcript compatibility;
- registry references whose implementation source is absent from this snapshot.

The **Current** lines describe verified TypeScript behavior. The **Required**
lines define the safer Python/FastAPI implementation contract. A registry name
does not mean a tool is available in every build or session.

## Priority model

| Priority | Meaning | Release rule |
| --- | --- | --- |
| `P0` | Minimum safe coding loop | Required before any general shell or extension release. |
| `P1` | Agent control and user interaction | Required for a useful multi-turn coding agent. |
| `P2` | Rich workspace and network intelligence | Add after P0/P1 policy and persistence pass. |
| `P3` | Teams, worktrees, scheduling, remote automation | Opt-in advanced capabilities. |
| `P4` | Dynamic, SDK, experimental, or internal | Ship only with explicit capability gates. |
| `UNRESOLVED` | Referenced but implementation absent | Do not port until source or product requirements are recovered. |

## Master inventory

| Priority | Canonical name | Source status | Primary capability | Default target decision |
| --- | --- | --- | --- | --- |
| P0 | `Read` | Implemented | `fs.read` | Allow inside approved workspace |
| P0 | `Glob` | Implemented | `fs.search` | Allow inside approved workspace |
| P0 | `Grep` | Implemented | `fs.search` | Allow inside approved workspace |
| P0 | `Edit` | Implemented | `fs.write` | Ask; allow in `accept_edits` outside protected paths |
| P0 | `Write` | Implemented | `fs.write` | Ask; allow in `accept_edits` outside protected paths |
| P0 | `Bash` | Implemented | `process.spawn` | Command-specific policy; otherwise ask |
| P1 | `Agent` | Implemented | `agent.spawn` | Allow bounded local subagent; ask for elevated capabilities |
| P1 | `TaskOutput` | Implemented, deprecated | `task.read` | Allow owned task |
| P1 | `TaskStop` | Implemented | `task.cancel` | Allow owned task; ask across ownership boundary |
| P1 | `TodoWrite` | Implemented, legacy mode | `task.write` | Allow session-local state |
| P1 | `TaskCreate` | Implemented, v2 mode | `task.write` | Allow session/team-local state |
| P1 | `TaskGet` | Implemented, v2 mode | `task.read` | Allow visible task |
| P1 | `TaskList` | Implemented, v2 mode | `task.read` | Allow visible task list |
| P1 | `TaskUpdate` | Implemented, v2 mode | `task.write` | Allow owned team/session state |
| P1 | `AskUserQuestion` | Implemented | `user.interact` | Always interrupt user |
| P1 | `Skill` | Implemented | `skill.invoke` | Depends on resolved skill capabilities |
| P1 | `EnterPlanMode` | Implemented | `session.mode.write` | Allow |
| P1 | `ExitPlanMode` | Implemented | `session.mode.write` | Always interrupt main user |
| P1 | `ToolSearch` | Implemented | `tool.discover` | Allow |
| P2 | `NotebookEdit` | Implemented | `fs.write.notebook` | Ask; `accept_edits` may allow |
| P2 | `WebFetch` | Implemented | `network.http.read` | Domain-specific policy |
| P2 | `WebSearch` | Implemented | `network.search` | Ask once or explicit configured allow |
| P2 | `LSP` | Implemented | `ide.code_intelligence` | Allow approved workspace reads |
| P2 | `PowerShell` | Implemented, platform gated | `process.spawn` | Command-specific policy; otherwise ask |
| P2 | `Config` | Implemented, internal build gated | `settings.read/write` | Read allow; write ask |
| P2 | `EnterWorktree` | Implemented, gated | `git.worktree.create` | Ask |
| P2 | `ExitWorktree` | Implemented, gated | `git.worktree.remove` | Ask; explicit destructive confirmation |
| P3 | `SendMessage` | Implemented, swarm gated | `agent.message` | Internal allow; cross-machine always ask |
| P3 | `TeamCreate` | Implemented, swarm gated | `agent.team.write` | Ask or explicit team-mode grant |
| P3 | `TeamDelete` | Implemented, swarm gated | `agent.team.delete` | Ask and verify no active members |
| P3 | `CronCreate` | Implemented, trigger gated | `automation.schedule` | Always ask for durable or recurring work |
| P3 | `CronDelete` | Implemented, trigger gated | `automation.delete` | Ask unless deleting an unstarted session-local own job |
| P3 | `CronList` | Implemented, trigger gated | `automation.read` | Allow owned jobs |
| P3 | `RemoteTrigger` | Implemented, policy gated | `external.automation` | Reads ask-once; writes always ask |
| P4 | `ListMcpResourcesTool` | Implemented, dynamically added | `mcp.resource.list` | Server trust policy |
| P4 | `ReadMcpResourceTool` | Implemented, dynamically added | `mcp.resource.read` | Server and URI policy |
| P4 | `mcp__<server>__<tool>` | Dynamic template | MCP-advertised capability | Central policy; never annotation-only |
| P4 | `mcp__<server>__authenticate` | Dynamic pseudo-tool | `mcp.authenticate` | Always user interrupt |
| P4 | `StructuredOutput` | Implemented, dynamically injected | `response.structured` | Allow after schema validation |
| P4 | `SendUserMessage` | Implemented, feature gated | `user.message` | Normal reply allow; proactive/external delivery policy |
| P4 | `TestingPermission` | Implemented, test only | `test.permission` | Always ask |
| UNRESOLVED | 16 registry-referenced tools | Source absent or partial | Unknown | Deny and hide |

## P0 core workspace tools

### `Read`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/FileReadTool/FileReadTool.ts`](../../tools/FileReadTool/FileReadTool.ts) |
| Input | `file_path: str`; optional `offset: int >= 0`; optional `limit: int > 0`; optional PDF `pages: str`. |
| Output | Tagged union: text with line metadata, image with MIME/base64/dimensions, notebook cells, PDF bytes, extracted PDF-page artifact set, or unchanged marker. |
| Current properties | Strict schema, read-only, concurrency-safe, no generic result persistence, path-based permission check. |
| Permission | Allow only after canonical path containment and read-rule evaluation. Protected config, secret, device, UNC, and out-of-workspace paths require deny or explicit policy. |
| Validation | Block hanging device paths; require regular supported files; apply size/token/page limits; validate page ranges; detect binary and supported media formats. |
| Required implementation | Stream or page large text; return artifact references for binary content; store read fingerprint (`path`, identity, mtime, size, hash/range) for later optimistic writes. |
| Concurrency | Parallel shared read lock on canonical file identity. |

### `Glob`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/GlobTool/GlobTool.ts`](../../tools/GlobTool/GlobTool.ts) |
| Input | `pattern: str`; optional `path: str`, defaulting to workspace cwd. |
| Output | `duration_ms`, `num_files`, ordered `filenames`, and `truncated`. |
| Current properties | Read-only and concurrency-safe; default result limit is 100; paths under cwd are relativized. |
| Permission | Evaluate canonical search root and pattern. Ignore rules and protected directories apply before traversal. |
| Validation | Search root must exist and be a directory; block unsafe remote/UNC resolution before filesystem metadata access. |
| Required implementation | Stable deterministic ordering, pagination cursor, explicit max results, cancellation during traversal, no symlink escape. |
| Concurrency | Parallel shared lock on search root. |

### `Grep`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/GrepTool/GrepTool.ts`](../../tools/GrepTool/GrepTool.ts) |
| Input | Required regex `pattern`; optional `path`, `glob`, `output_mode`; context controls `-A`, `-B`, `-C`/`context`; optional line numbers, case-insensitive mode, file `type`, `head_limit`, `offset`, and `multiline`. |
| Output | Mode, file count, filenames, optional content, line/match counts, and applied pagination metadata. |
| Current properties | Strict, read-only, concurrency-safe; default cap 250; version-control directories excluded. |
| Permission | Same root and ignore policy as `Read`; regex content does not widen filesystem scope. |
| Validation | Compile pattern before execution; reject unsupported expressions; validate root; cap context and output; treat `head_limit=0` as privileged/unbounded rather than ordinary input. |
| Required implementation | Use argument-vector subprocess or native library, never shell interpolation; preserve deterministic pagination and cancellation. |
| Concurrency | Parallel shared lock on search root. |

### `Edit`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/FileEditTool/types.ts`](../../tools/FileEditTool/types.ts) and [`FileEditTool.ts`](../../tools/FileEditTool/FileEditTool.ts) |
| Input | `file_path: str`, `old_string: str`, `new_string: str`, optional `replace_all: bool = false`. |
| Output | File path, old/new strings, original content, structured diff hunks, user-modified flag, replace-all flag, optional git diff. |
| Current properties | Strict schema; path permission; read-before-write and stale-file checks; unique match required unless `replace_all`; rejects notebook edits. |
| Permission | Ask by default. `accept_edits` may allow ordinary workspace files, but protected paths remain ask/deny and bypass-immune. Approval shows the exact proposed diff. |
| Validation | Canonical contained path; old and new differ; target exists unless creating from empty; complete prior read; unchanged fingerprint; target occurrence count; file-size cap; secret and settings guards. |
| Required implementation | Recheck fingerprint after approval and immediately before atomic replace; preserve permissions/encoding intentionally; fsync/atomic rename where supported; return before/after hashes. |
| Concurrency | Exclusive write lock on canonical file; conflict with reads whose consistency matters. |
| Idempotency | Deduplicate by expected-before hash plus after hash. A repeated completed call returns the existing receipt. |

### `Write`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/FileWriteTool/FileWriteTool.ts`](../../tools/FileWriteTool/FileWriteTool.ts) |
| Input | Absolute `file_path: str` and complete `content: str`. |
| Output | `create` or `update`, file path, written content, structured patch, nullable original content, optional git diff. |
| Current properties | Strict schema; path permission; existing files must have a complete fresh read; parent creation and editor/LSP notifications. |
| Permission | Same as `Edit`, with clearer warning when overwriting an existing file. Creating executable, settings, hook, CI, or secret-bearing files requires elevated review. |
| Validation | Canonical path, allowed file type/policy, fresh expected hash, content-size limit, parent containment, secret checks, no special device target. |
| Required implementation | Atomic write; explicit mode policy; before/after hashes; artifact-backed diff; no implicit line-ending rewrite. |
| Concurrency | Exclusive file write lock and parent-directory intent lock for creation. |
| Idempotency | Expected-before hash and content hash. Never overwrite changed content on retry. |

### `Bash`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/BashTool/BashTool.tsx`](../../tools/BashTool/BashTool.tsx) plus security and permission modules in the same directory. |
| Input | `command: str`; optional `timeout` milliseconds, `description`, `run_in_background`, and dangerous sandbox override. Internal simulated-edit data is explicitly excluded from the model schema. |
| Output | `stdout`, `stderr`, interruption state, optional image flag, background task ID/state, persisted-output metadata, and optional structured content. |
| Current properties | Strict schema; read-only classifier controls concurrency; command-specific permission parser; optional sandbox and background task support; 30K-character persistence threshold. |
| Permission | Parse all compound commands and redirections. Apply hard deny, exact/prefix rules, sandbox eligibility, protected paths, network effects, credential access, and destructive semantics. Unknown or unparsable commands ask or deny. |
| Validation | Non-empty command, timeout cap, cwd containment, environment allowlist, no model-provided internal fields, no forbidden shell mode or unsupported background request. |
| Required implementation | Spawn a process group with bounded environment, output, time, and cleanup; never concatenate approval metadata into the command; capture executable/argv parse evidence. |
| Concurrency | Only proven read-only commands may overlap. All other commands serialize by workspace and additional semantic locks such as `git-write`. |
| Idempotency | Treat as non-idempotent unless a narrow classifier marks a specific invocation pure. Never auto-retry after ambiguous process loss. |

## P1 agent and interaction tools

### `Agent` (legacy alias `Task`)

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/AgentTool/AgentTool.tsx`](../../tools/AgentTool/AgentTool.tsx) and [`runAgent.ts`](../../tools/AgentTool/runAgent.ts) |
| Input | `description`, `prompt`; optional `subagent_type`, model alias, background flag, teammate `name`, `team_name`, permission `mode`, `isolation`, and gated `cwd`. |
| Output | Synchronous completion with agent ID, type, text blocks, tool count, duration, tokens and usage; async launch with output file; internal teammate and remote-launch variants. |
| Current properties | Read-only only because child tools enforce permission; concurrency-safe; agent definitions and required MCP servers are filtered; recursion is constrained by context. |
| Permission | Spawn itself may be allowed for a bounded local child. Any increased model cost, new cwd, remote isolation, team membership, broader tool set, or stronger permission mode requires policy evaluation. Child tools never inherit more authority than the parent grant. |
| Validation | Agent type exists and is allowed; model allowed; parent depth and child count within limits; cwd/isolation exclusivity; required MCP ready; no forbidden recursive fork/team shape. |
| Required implementation | Create durable child run before launch; explicit parent-child edge, budgets, cancellation tree, tool allowlist, checkpoint ID, and result handoff. Never model it as an ordinary background coroutine only. |
| Concurrency | Parallel subject to per-session child limit and global provider/tool quotas. |

### `TaskOutput` (aliases `AgentOutputTool`, `BashOutputTool`)

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/TaskOutputTool/TaskOutputTool.tsx`](../../tools/TaskOutputTool/TaskOutputTool.tsx) |
| Input | `task_id`; `block: bool = true`; `timeout: 0..600000 ms = 30000`. |
| Output | Retrieval status (`success`, `timeout`, `not_ready`) and nullable task details including type, status, description, output, exit code/error, prompt/result. |
| Current properties | Read-only, concurrency-safe, deprecated in favor of reading output artifacts. |
| Permission | Caller must own the task or possess read scope for its parent run. |
| Required implementation | Prefer `GET /tasks/{id}` and artifact streaming for clients. Keep the tool only for model compatibility; bound blocking and output. |

### `TaskStop` (legacy alias `KillShell`)

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/TaskStopTool/TaskStopTool.ts`](../../tools/TaskStopTool/TaskStopTool.ts) |
| Input | Optional canonical `task_id`; deprecated `shell_id`; exactly one effective ID required. |
| Output | Message, task ID, task type, optional command/description. |
| Permission | Allow cancellation of a task owned by the same run. Cross-agent or shared-task cancellation asks or requires coordinator authority. |
| Validation | Task exists and is currently running; caller is authorized; cancellation is supported. |
| Required implementation | Cascade cancellation to process group or child run, persist request and final outcome, and remain idempotent after task termination. |

### `TodoWrite`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/TodoWriteTool/TodoWriteTool.ts`](../../tools/TodoWriteTool/TodoWriteTool.ts) and [`utils/todo/types.ts`](../../utils/todo/types.ts) |
| Input | Complete `todos` array; each item has non-empty `content`, status (`pending`, `in_progress`, `completed`), and non-empty `activeForm`. |
| Output | Previous list, submitted list, and optional verification nudge. |
| Availability | Enabled only when task v2 is off. |
| Permission | Allow session-local state; no filesystem or external effect. |
| Required implementation | Preserve for migration only. New product behavior uses normalized task records rather than replacing an entire embedded list. |

### `TaskCreate`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/TaskCreateTool/TaskCreateTool.ts`](../../tools/TaskCreateTool/TaskCreateTool.ts) |
| Input | `subject`, `description`; optional present-continuous `activeForm`; optional metadata map. |
| Output | New task ID and subject. |
| Permission | Allow within the caller's session/team task list. Metadata keys are server-allowlisted. |
| Validation | Non-empty bounded text; metadata JSON size; team/task-list ownership; task-created hooks may reject. |
| Required implementation | Transactionally insert task and event; use idempotency key to prevent duplicate tasks after reconnect. |

### `TaskGet`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/TaskGetTool/TaskGetTool.ts`](../../tools/TaskGetTool/TaskGetTool.ts) |
| Input | `taskId`. |
| Output | Nullable task with ID, subject, description, status, `blocks`, and `blockedBy`. |
| Permission | Allow only if task belongs to a visible session/team list. |
| Required implementation | Include owner, version, timestamps, and unresolved dependency status in structured output. |

### `TaskList`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/TaskListTool/TaskListTool.ts`](../../tools/TaskListTool/TaskListTool.ts) |
| Input | Empty strict object. |
| Output | Tasks with ID, subject, status, optional owner, and unresolved blockers. |
| Permission | Allow visible list. Internal tasks remain filtered. |
| Required implementation | Add pagination/filter fields before lists can grow without bound. Preserve an empty-input compatibility version. |

### `TaskUpdate`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/TaskUpdateTool/TaskUpdateTool.ts`](../../tools/TaskUpdateTool/TaskUpdateTool.ts) |
| Input | `taskId`; optional subject, description, active form, status including special `deleted`, dependency additions, owner, and metadata merge/delete values. |
| Output | Success, task ID, updated fields, optional error, status transition, and verification nudge. |
| Permission | Caller must own or coordinate the task list. Owner reassignment across agents requires coordinator policy. |
| Validation | Task and dependency IDs exist; no self-dependency or dependency cycle; legal state transition; optimistic task version. |
| Required implementation | Use `expected_version`; update task, dependency edges, hooks, and event in one transaction. |

### `AskUserQuestion`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/AskUserQuestionTool/AskUserQuestionTool.tsx`](../../tools/AskUserQuestionTool/AskUserQuestionTool.tsx) |
| Model input | One to four unique questions. Each has question text, short header, two to four unique options, optional preview, and `multiSelect`. Options have label and description. |
| Runtime-only input | Current source also accepts injected `answers`, annotations, and metadata. The Python model MUST split these from model-provided input. |
| Output | Questions, answer map, optional per-question preview/notes annotations. |
| Permission | Always requires an interactive interrupt; unavailable in clients that cannot render it. It is not auto-approved by bypass mode. |
| Validation | Unique question text and option labels, bounded lengths, supported preview format, one response per question. |
| Required implementation | Persist request before event; resume via permission/interaction command; never trust model-supplied answers. |

### `Skill`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/SkillTool/SkillTool.ts`](../../tools/SkillTool/SkillTool.ts) |
| Input | `skill: str`; optional `args: str`. Leading slash is normalized for compatibility. |
| Output | Inline expansion with command name, optional allowed tools/model; or forked completion with agent ID and result. |
| Permission | Resolve the skill first, then evaluate its declared tools, model, network, MCP, plugin source, and fork behavior. Invocation is not automatically safe because the wrapper only expands a prompt. |
| Validation | Skill exists in allowed local/bundled/plugin/MCP registry; source trusted; argument policy valid; no recursive forbidden workflow. |
| Required implementation | Version and hash skill content in run snapshot; expose exact capability manifest; execute inline expansion or child graph through ordinary policy. |

### `EnterPlanMode`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/EnterPlanModeTool/EnterPlanModeTool.ts`](../../tools/EnterPlanModeTool/EnterPlanModeTool.ts) |
| Input | Empty strict object. |
| Output | Confirmation message. |
| Permission | Allow only on main run; no child-agent use. Preserve previous mode for later restoration. |
| Required implementation | Durable session mode transition and event; planning policy allows reads and plan artifact writes only. |

### `ExitPlanMode`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`](../../tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts) |
| Input | Optional `allowedPrompts`, each containing `tool: Bash` and a semantic permission prompt. Runtime injects plan text and plan-file path for observers. |
| Output | Plan, agent flag, optional file path, tool availability, edit flag, leader-approval state and request ID. |
| Permission | Main user must approve plan exit. Teammate plan mode routes approval to leader. The model MUST NOT supply approval. |
| Validation | Current mode is plan; plan artifact exists and belongs to run; semantic permissions are bounded and do not become arbitrary command allows. |
| Required implementation | Use durable interaction request linked to plan artifact hash; mode transition and granted scopes commit atomically. |

### `ToolSearch`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/ToolSearchTool/ToolSearchTool.ts`](../../tools/ToolSearchTool/ToolSearchTool.ts) |
| Input | Search `query`; optional `max_results = 5`; supports exact `select:name1,name2`. |
| Output | Matching canonical names, query, deferred-tool count, optional pending MCP servers. |
| Permission | Allow; discovery does not authorize use. |
| Validation | Positive bounded result count; names must exist in the run registry snapshot. |
| Required implementation | Deterministic scoring and schema-load event. Cache by registry snapshot hash, not global mutable tool names. |

## P2 workspace and network tools

### `NotebookEdit`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/NotebookEditTool/NotebookEditTool.ts`](../../tools/NotebookEditTool/NotebookEditTool.ts) |
| Input | Absolute `notebook_path`, optional `cell_id`, `new_source`, optional `cell_type` (`code` or `markdown`), optional `edit_mode` (`replace`, `insert`, `delete`). |
| Output | Source, cell ID/type, language, mode, optional error, path, original serialized notebook, updated serialized notebook. |
| Permission | Workspace write approval with exact cell diff. `delete` is destructive metadata even though file can be restored. |
| Validation | `.ipynb`, valid JSON/notebook format, fresh complete read/hash, legal cell ID and mode, `cell_type` required for insert, bounded cell/source size. |
| Required implementation | Preserve unknown notebook metadata; atomic write; return notebook-level before/after hash and cell patch artifact. |

### `WebFetch`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/WebFetchTool/WebFetchTool.ts`](../../tools/WebFetchTool/WebFetchTool.ts) |
| Input | Valid `url` and a `prompt` applied to fetched content. |
| Output | Byte count, HTTP code/text, processed result, duration, final URL. |
| Permission | Domain-specific allow/ask/deny. Redirects MUST be rechecked at every hop. Private, loopback, link-local, metadata-service, and disallowed schemes are denied. |
| Validation | HTTPS by default, DNS/IP SSRF checks, response/type/redirect/time limits, no credentials in URL, safe decompression bounds. |
| Required implementation | Separate fetch from model summarization for audit/cost; store citation metadata and bounded raw artifact where policy allows. |
| Concurrency | Parallel under per-host and global network limits. |

### `WebSearch`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/WebSearchTool/WebSearchTool.ts`](../../tools/WebSearchTool/WebSearchTool.ts) |
| Input | Query length at least two; optional allowed domains or blocked domains, never both. |
| Output | Query, search hit/text result sequence, duration. Current provider tool caps usage at eight searches. |
| Permission | Explicit search capability grant; domain filters further restrict but do not grant network access. |
| Validation | Domain normalization, query/output bounds, provider capability, no conflicting filters. |
| Required implementation | Preserve result URL/title/source metadata and model citations; enforce provider and per-turn use budget. |

### `LSP`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/LSPTool/LSPTool.ts`](../../tools/LSPTool/LSPTool.ts) and [`schemas.ts`](../../tools/LSPTool/schemas.ts) |
| Input | Operation enum: definition, references, hover, document/workspace symbols, implementation, call hierarchy preparation, incoming calls, outgoing calls; each currently carries file path and positive 1-based line/character. |
| Output | Operation, formatted result, file path, optional result and file counts. |
| Permission | Read policy for the referenced workspace file; extension-host LSP adapters additionally require Workspace Trust. |
| Validation | LSP connected; operation supported; path exists and is a file; position valid for document snapshot/version. |
| Required implementation | Prefer tagged per-operation input models so workspace-symbol search can have a query rather than fake file position fields. Include document version in request/result. |

### `PowerShell`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/PowerShellTool/PowerShellTool.tsx`](../../tools/PowerShellTool/PowerShellTool.tsx) and permission/security modules in that directory. |
| Input | Command; optional timeout, description, background flag, dangerous sandbox override. |
| Output | Stdout/stderr, interruption, semantic exit interpretation, image and persisted-output metadata, background state. |
| Permission | Same capability requirements as `Bash`, using PowerShell AST parsing and canonical cmdlet resolution. Auto mode MUST NOT approve when the security parser is unavailable. |
| Validation | Platform and executable available, constrained execution policy, encoded-command and downloader/`Invoke-Expression` checks, path and git safety. |
| Required implementation | Register as a shell dialect behind one `ShellExecutor`; do not duplicate lifecycle/persistence logic. |

### `Config`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/ConfigTool/ConfigTool.ts`](../../tools/ConfigTool/ConfigTool.ts) |
| Input | `setting`; optional scalar `value`. Omission means read. |
| Output | Success, get/set operation, setting, value, previous/new values, optional error. |
| Permission | Reading non-secret supported settings is allowed. Every write asks and identifies destination/scope. Secret values are never returned to the model. |
| Validation | Setting comes from server-owned allowlist; value validates against setting-specific schema, not a generic scalar union; policy-managed settings cannot be changed. |
| Required implementation | Use one Pydantic discriminated union per writable setting or a typed registry; atomic config write and redacted audit. |

### `EnterWorktree`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/EnterWorktreeTool/EnterWorktreeTool.ts`](../../tools/EnterWorktreeTool/EnterWorktreeTool.ts) |
| Input | Optional validated slug-like `name`. |
| Output | Worktree path, optional branch, message. |
| Permission | Ask before creating git refs/directories and changing session workspace. |
| Validation | Git root exists, session not already in a managed worktree, name valid and non-colliding, destination under managed root. |
| Required implementation | Durable worktree record, original cwd/head, cleanup owner, and state transition. Do not use process-global `chdir` in a multi-session daemon. |

### `ExitWorktree`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/ExitWorktreeTool/ExitWorktreeTool.ts`](../../tools/ExitWorktreeTool/ExitWorktreeTool.ts) |
| Input | `action: keep|remove`; optional `discard_changes`. |
| Output | Action, original cwd, worktree path/branch, optional terminal session, discarded file/commit counts, message. |
| Permission | `keep` asks for session workspace transition; `remove` always asks. Discarding changes requires explicit destructive confirmation tied to current change summary. |
| Validation | Worktree was created by this session; reliable git status/base; active agents/processes stopped; confirmation hash still matches changes. |
| Required implementation | Never remove manually created or another session's worktree; cleanup is idempotent and auditable. |

## P3 coordination and automation tools

### `SendMessage`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/SendMessageTool/SendMessageTool.ts`](../../tools/SendMessageTool/SendMessageTool.ts) |
| Input | Recipient name, broadcast `*`, optional summary, and either text or tagged shutdown/plan-approval response. Feature gate also permits UDS and remote bridge addresses. |
| Output | Success/message plus routing, recipients, request ID, or target depending on operation. |
| Permission | Same-team text is allowed within authorized run. Broadcast is rate-limited. Cross-machine bridge messages always require explicit user consent and cannot carry structured control messages. |
| Validation | Recipient exists and scheme is legal; summary required for text; request/response IDs and sender role match; bounded content; no nested-team routing. |
| Required implementation | Durable mailbox record and delivery ID; at-least-once transport with deduplication; never treat message text as a trusted user command. |

### `TeamCreate`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/TeamCreateTool/TeamCreateTool.ts`](../../tools/TeamCreateTool/TeamCreateTool.ts) |
| Input | `team_name`; optional description and lead `agent_type`. |
| Output | Team name, team record path in current implementation, lead agent ID. |
| Permission | Explicit multi-agent/team grant because it enables further model/tool cost. |
| Validation | One active led team per run, unique sanitized name, valid lead type, member and budget limits. |
| Required implementation | Store team relationally, not as authority-bearing filesystem paths; create team/task list/lead membership transactionally. |

### `TeamDelete`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/TeamDeleteTool/TeamDeleteTool.ts`](../../tools/TeamDeleteTool/TeamDeleteTool.ts) |
| Input | Empty strict object; current team inferred from context. |
| Output | Success, message, optional team name. |
| Permission | Ask the team owner. Refuse while non-lead members are active. |
| Validation | Caller leads team; all child runs terminal or explicitly cancelled; cleanup scope enumerated. |
| Required implementation | Soft-delete team record first; schedule idempotent cleanup; retain audit and run history. |

### `CronCreate`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/ScheduleCronTool/CronCreateTool.ts`](../../tools/ScheduleCronTool/CronCreateTool.ts) |
| Input | Five-field local-time cron string, prompt, optional recurring flag default true, optional durable flag default false. |
| Output | Job ID, human schedule, recurring flag, optional durable flag. |
| Permission | Always ask for recurring or durable execution. One-shot session-local reminders may use an explicit session grant. Approval displays timezone, next fire, recurrence, expiry, agent and capabilities. |
| Validation | Parse expression; next match within policy horizon; maximum 50 current jobs in source; durable jobs prohibited for ephemeral teammates; bounded prompt. |
| Required implementation | Store timezone explicitly, capability snapshot, owner, next-run timestamp, expiry, idempotency key, and disabled state. Never run with permissions broader than approved. |

### `CronDelete`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/ScheduleCronTool/CronDeleteTool.ts`](../../tools/ScheduleCronTool/CronDeleteTool.ts) |
| Input | Job `id`. |
| Output | Deleted/cancelled job ID. |
| Permission | Owner or coordinator only. Ask for durable/shared jobs; allow cleanup of caller-owned session job under granted automation scope. |
| Validation | Job exists and ownership matches. |
| Required implementation | Mark disabled transactionally before scheduler removal; repeated delete returns existing terminal state. |

### `CronList`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/ScheduleCronTool/CronListTool.ts`](../../tools/ScheduleCronTool/CronListTool.ts) |
| Input | Empty strict object. |
| Output | Jobs with ID, cron, human schedule, prompt, recurrence, durability. |
| Permission | Allow jobs visible to caller; teammate sees own jobs. Redact prompt secrets. |
| Required implementation | Add status, timezone, next/last run, owner, expiry, and pagination. |

### `RemoteTrigger`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/RemoteTriggerTool/RemoteTriggerTool.ts`](../../tools/RemoteTriggerTool/RemoteTriggerTool.ts) |
| Input | `action: list|get|create|update|run`; optional constrained `trigger_id`; optional arbitrary JSON body. |
| Output | HTTP status and serialized JSON. |
| Permission | `list/get` require remote-session read scope. `create/update/run` always require external-write approval unless an exact managed policy grants them. |
| Validation | Action-specific required fields; replace arbitrary body with per-action Pydantic models; organization and policy scope; response limit. |
| Required implementation | Provider adapter with idempotency key, redacted structured response, timeout, audit, and no raw bearer token in state. |

## P4 dynamic and special tools

### `ListMcpResourcesTool`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/ListMcpResourcesTool/ListMcpResourcesTool.ts`](../../tools/ListMcpResourcesTool/ListMcpResourcesTool.ts) |
| Input | Optional exact server name. |
| Output | Resource array with URI, name, optional MIME/description, and server. |
| Permission | Connected server must be trusted for resource metadata. A blanket tool allow does not imply every future server is trusted. |
| Required implementation | Pagination, server identity, schema/capability snapshot, bounded per-server failure handling. |

### `ReadMcpResourceTool`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/ReadMcpResourceTool/ReadMcpResourceTool.ts`](../../tools/ReadMcpResourceTool/ReadMcpResourceTool.ts) |
| Input | Exact `server` and resource `uri`. |
| Output | Content items with URI, optional MIME, text, or saved binary artifact reference. |
| Permission | Evaluate server and URI scheme/prefix; open-world and sensitive resource servers ask even for reads. |
| Validation | Server connected and advertises resources; URI belongs to listed or policy-approved template; bounded response and binary decode. |
| Required implementation | Store blobs as artifacts, not arbitrary filesystem paths; strict input; content-type allowlist and malware-safe handling. |

### Dynamic `mcp__<server>__<tool>`

| Contract item | Specification |
| --- | --- |
| Current source | Template [`tools/MCPTool/MCPTool.ts`](../../tools/MCPTool/MCPTool.ts), populated in [`services/mcp/client.ts`](../../services/mcp/client.ts). |
| Input | Server-advertised JSON Schema; varies per tool. |
| Output | MCP content, structured content, metadata, images/resources, or protocol error. |
| Current metadata | Namespaced identity, optional deferred search hint/always-load, read-only/destructive/open-world annotations, progress and elicitation. Missing hints default false in current source. |
| Permission | Central policy always runs. Missing or false read-only hint does not prove write, but it requires ask. Destructive/open-world annotations increase policy; they never reduce it. |
| Required implementation | Validate and hash schema, freeze it in run snapshot, preserve original identity, bound retries, authenticate server, audit transport, and handle external writes as non-idempotent unless downstream key exists. |

### Dynamic `mcp__<server>__authenticate`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/McpAuthTool/McpAuthTool.ts`](../../tools/McpAuthTool/McpAuthTool.ts) |
| Input | Empty object. |
| Output | Status (`auth_url`, `unsupported`, `error`), message, optional authorization URL. |
| Permission | Target design always interrupts the user because authentication changes account connectivity. Never auto-open a browser from a headless model call. |
| Required implementation | OAuth state/PKCE, callback ownership, secret store, URL expiry, exact server binding, and reconnect event. Browser action belongs to trusted client/extension host. |

### `StructuredOutput`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/SyntheticOutputTool/SyntheticOutputTool.ts`](../../tools/SyntheticOutputTool/SyntheticOutputTool.ts) |
| Input | Dynamically supplied output JSON Schema; arbitrary object only until adapter is configured. |
| Output | Confirmation plus separate structured payload. |
| Availability | Injected for non-interactive structured-output requests, not ordinary registry exposure. |
| Permission | Allow after schema validation; no side effect. |
| Required implementation | Compile and cache validator by schema hash; mark the run terminal only after one valid call; reject second terminal output; store structured result separately from transcript text. |

### `SendUserMessage` (legacy alias `Brief`)

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/BriefTool/BriefTool.ts`](../../tools/BriefTool/BriefTool.ts) |
| Input | Markdown message, optional attachment paths, status `normal|proactive`. |
| Output | Message, optional resolved attachment metadata, optional sent timestamp. |
| Permission | Normal in-session rendering may be allowed. Proactive OS/mobile/remote delivery and file attachments require channel and file-read policy. |
| Validation | Attachment path containment/read permission, size/type limits, no secret files, channel enabled, bounded message. |
| Required implementation | Separate model final response from notification delivery. Use artifact IDs instead of raw paths and record delivery receipts per channel. |

### `TestingPermission`

| Contract item | Specification |
| --- | --- |
| Current source | [`tools/testing/TestingPermissionTool.tsx`](../../tools/testing/TestingPermissionTool.tsx) |
| Input | Empty strict object. |
| Output | Success text. |
| Availability | Test environment only. |
| Permission | Always ask. |
| Required implementation | Include only in test registry fixtures; startup MUST fail if enabled in production. |

## Conditional registry references with absent source

The following names are referenced by [`tools.ts`](../../tools.ts), but the
corresponding implementation file is absent from this workspace snapshot or,
for `REPL`, only supporting files are present. Their inputs, outputs, and
security behavior cannot be truthfully reconstructed from the available code.

| Registry reference | Gate or context | Available evidence | Required action |
| --- | --- | --- | --- |
| `TungstenTool` | Internal user type | Static import only; directory absent | Keep hidden; recover source/product contract. |
| `REPLTool` | Internal user type and REPL mode | Constants and primitive wrapper helpers; main implementation absent | Do not rebuild until VM isolation and permissions are specified. |
| `SuggestBackgroundPRTool` | Internal user type | Dynamic require path absent | Recover intended PR provider and approval policy. |
| `SleepTool` | Proactive/Kairos | Prompt file exists; implementation absent | Specify notification-drain semantics before adding. |
| `MonitorTool` | Monitor feature | Dynamic require path absent | Define stream source, lifecycle, limits, and cancellation. |
| `SendUserFileTool` | Kairos | Dynamic require path absent | Define artifact/channel security before implementation. |
| `PushNotificationTool` | Kairos/push feature | Dynamic require path absent | Define external delivery permission and receipts. |
| `SubscribePRTool` | GitHub webhook feature | Dynamic require path absent | Define repository scope, webhook ownership, and unsubscribe. |
| `OverflowTestTool` | Overflow test feature | Dynamic require path absent | Test-only; never expose in production. |
| `CtxInspectTool` | Context-collapse feature | Dynamic require path absent | Define redaction; context inspection may expose secrets. |
| `TerminalCaptureTool` | Terminal panel feature | Dynamic require path absent | Define terminal ownership, capture bounds, and consent. |
| `WebBrowserTool` | Browser feature | Dynamic require path absent | Define browser isolation, screenshots, navigation, and open-world policy. |
| `SnipTool` | History snip feature | Dynamic require path absent | Define immutable transcript versus context-view mutation semantics. |
| `ListPeersTool` | UDS inbox feature | Dynamic require path absent | Define peer authentication, discovery scope, and address format. |
| `WorkflowTool` | Workflow scripts feature | Registry and constants reference; implementation absent | Define workflow manifest, recursion, capabilities, and checkpointing. |
| `VerifyPlanExecutionTool` | Environment flag | Dynamic require path absent | Define evidence inputs, verifier authority, and terminal behavior. |

`TOOL-CAT-001`: The Python registry MUST NOT create placeholder implementations
for unresolved names. It returns an availability record with reason
`implementation_unavailable` to operators, while omitting the schema from model
requests.

## Tools intentionally not present

The current source does not define a general file-delete tool, SQL tool, email
tool, browser automation contract with source in this snapshot, or unrestricted
HTTP request tool. These capabilities MUST NOT be simulated through hidden
backend endpoints. If added later, they receive first-class contracts and
permission rules.

## Build order by importance

1. `Read`, `Glob`, `Grep`, registry snapshot, executor, and artifacts.
2. `Edit` and `Write` with durable permission interrupts and optimistic file hashes.
3. Task records, `AskUserQuestion`, plan-mode transitions, and `ToolSearch`.
4. `Bash` with sandboxing, process groups, parser-based permission, and no auto-retry.
5. `Agent` with child-run graph, budgets, checkpoints, and cancellation tree.
6. Web, LSP, notebook, and worktree capabilities.
7. MCP resources and dynamic tools after server trust and schema snapshotting.
8. Teams, messages, crons, and remote triggers after ownership and external-action audit are proven.
9. Unresolved/experimental tools only from recovered product requirements.

## Catalog acceptance test

For every enabled registry entry, an automated catalog test MUST assert that:

- a corresponding `ToolSpec` exists;
- input and output schemas compile;
- capability, side effect, permission default, timeout, concurrency, and
  idempotency metadata are explicit;
- at least one permission and one validation fixture exist;
- aliases do not collide;
- dynamic schema hash is present;
- source/manifest provenance is recorded;
- unresolved tools are not model-visible.
