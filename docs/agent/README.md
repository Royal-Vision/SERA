# Agent runtime reference

[Project documentation](../README.md) | [Reviewer build guide](../reviewer/README.md)

Status: condensed design reference. This replaces the earlier phase-by-phase
coding-agent plan and tool tutorial. It is not an inventory of implemented features.
The small reviewer starts with a fixed workflow and one bounded model request;
an autonomous tool loop is a later capability.

## Responsibility boundaries

| Part | Responsibility | Boundary |
| --- | --- | --- |
| Contracts | Inputs, results, evidence, limits, and typed errors | Independent of provider SDKs, graph wiring, and presentation |
| Repository tools | Read the selected snapshot and return bounded evidence | Cannot silently switch to the live working tree |
| Registry | Resolve available tool names and schemas | Availability does not itself authorize execution |
| Policy | Decide whether an actual operation is permitted | Evaluates normalized arguments and effective access |
| Executor | Validate, authorize, enforce timeout, execute, normalize outcomes | Every agent tool uses the same execution boundary |
| Provider | Translate model messages, responses, and capability differences | No Git access, context selection, or review policy |
| LangGraph coordinator | Select stages, track state, route completion/failure | Delegates operations to modules; does not implement them |
| Presentation | Render progress and finalized results | Does not own review state or finding validity |

The provider and repository layers must be usable without importing CLI or graph
code. Reuse existing modules only after checking their behavior against these
boundaries. Current code may contain scaffolding and older design assumptions.

## Model integration

Start with a fake provider that returns known candidates, then connect one real
provider through a small explicit interface. Define request, response, error,
usage, timeout, and cancellation behavior before expanding provider support.

- Record model identity and effective configuration with the review result.
- Declare structured-output and tool-call capabilities instead of assuming every
  compatible endpoint implements the same features.
- Validate returned candidates even when the provider advertises structured output.
- Do not require native tool calling for the v1 bounded analysis request.
- Apply the configured source-transmission policy before dispatch. Do not silently
  change provider or credentials after a failure.
- Bound retries and distinguish transport failure, invalid model output, and a valid
  empty finding list. A fake provider lets tests run without credentials.
- Keep credentials out of graph state, prompts, and logs. Initialize clients only
  when needed and close resources at their owning lifecycle boundary.

Existing [provider code](../../app/agent/providers/openai_compat.py) is a candidate
integration point. Its presence alone does not establish that the review interface
is complete. Repository comparison belongs in the review subsystem.

## Tools and execution, when needed

Begin with committed file reads and searches. Describe each tool's name, input
schema, result schema, effective capabilities, timeout, output limit, and concurrency
constraints. A command-capable tool needs argument-sensitive policy; a generic
read-only label is insufficient.

An execution path should perform:

1. Tool lookup and input validation.
2. Snapshot/path resolution and effective-operation classification.
3. Authorization and any required preconditions.
4. Bounded execution with cancellation support.
5. Result normalization, truncation disclosure, and evidence attribution.

Argument repair, if introduced, must be narrow and observable. Revalidate and
reauthorize the repaired operation; never repair a dangerous request into a different
operation invisibly. Return useful structured tool failures while keeping internal
tracebacks in controlled diagnostics. Preserve cancellation; do not swallow it as
an ordinary error. Unexpected programming errors must remain diagnosable.

Concurrency is permitted only when operations are independent and resources allow
it. Read-only operations can still contend for resources. Mutation tools, shell
execution, and read-before-edit preconditions are outside the reviewer v1 scope.

## Graph state and stopping

The v1 graph carries the review request, snapshot, evidence, candidates, stage
outcomes, and result. Keep live clients and services in runtime dependencies rather
than serializable state. Contracts should have one owner, even when a graph state
references them.

If a tool-calling investigator is later justified, give it explicit allowed tools,
a fixed snapshot, wall-clock/model/tool budgets, and termination behavior. It should
return candidates through the same validator. Detect repeated failures or lack of
progress; more model calls are not proof of better analysis.

Cancellation must reach pending provider requests and subprocesses. Durable
checkpointing is a separate decision: recording graph state alone does not make
external side effects exactly-once. The v1 reviewer can rerun after a crash.

## Verification

- Use fake providers for valid, empty, malformed, and failed responses.
- Verify context comes from captured revisions and discloses truncation.
- Verify a denied operation never reaches execution.
- Check timeout and cancellation behavior, including process cleanup.
- Measure orchestration time separately from provider latency; do not copy old
  machine-specific benchmark numbers as universal requirements.
- Use the reviewer guide's known-defect and clean-change examples to assess quality.

## Older phase references in source comments

These numbers identify earlier design topics, not current build milestones.
Historical details are available in Git; use the reviewer guide for today's order.

| Earlier reference | Topic in this guide |
| --- | --- |
| Phase 00 | Responsibility boundaries and verification |
| Phase 01 | Presentation and lifecycle boundaries |
| Phase 02 | Contracts, registry, and execution |
| Phases 03–04 | Read/search tools and bounded evidence |
| Phase 05 | Validation, authorization, execution, and errors |
| Phase 06 | Mutation tools, deferred from the reviewer |
| Phase 07 | Model integration |
| Phases 08–09 | Graph state and stopping |
| Phase 10 | State ownership and optional recovery |
| Phases 11–12 | Policy, credentials, and execution controls |
| Phase 13 | [Future capabilities](../platform/README.md) |

Older Tool Contract SRS and Tool Catalog references refer to the same tool and
execution topics above. This condensed guide supersedes their implementation
sketches; it does not assert those sketches were implemented.
