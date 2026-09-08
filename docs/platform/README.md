# Future platform decisions

[Project documentation](../README.md) | [Reviewer build guide](../reviewer/README.md)

Status: deferred design questions, not accepted reviewer v1 requirements.
This condenses the earlier platform specifications. Finish a useful local review
before adopting these capabilities. The current decision is one local application,
a LangGraph review workflow, fixed Git inputs, and in-memory execution state.

## Decide from an observed need

| Capability | Trigger to consider it | Questions to settle first |
| --- | --- | --- |
| PR integration | Developers need reviews attached to PRs | Target/head provenance, webhook deduplication, permissions, stale reviews, comment update identity |
| Hosted service | Reviews must run independently of developer machines | Tenant/workspace isolation, repository access, credential ownership, retention, deployment |
| Durable jobs / Temporal | Losing a worker must not lose the review job | Retry ownership, activity boundaries, recovery, idempotent publication, operating cost |
| LangGraph persistence | Investigations need pause/resume across restarts | Stable run identity, durable checkpoint storage, state versioning, resume authority |
| Tool-calling investigator | Prepared context misses useful dependencies | Tool permissions, fixed-snapshot reads, budgets, stopping, quality improvement |
| Multiple agents | Evaluations show useful independent investigations | Task ownership, inherited permissions, shared budget, cancellation, duplicate findings |
| Editor or richer CLI | A terminal report no longer supports the workflow | One result authority, event protocol, reconnection, safe rendering, user interaction |
| Memory or retrieval index | Repeated reviews need reusable project knowledge | Source/revision provenance, staleness, invalidation, privacy, deletion |
| Skills / plugins / MCP | Reusable tools or procedures have demonstrated value | Trust, schema/versioning, discovery, permissions, updates, evaluation |
| Automatic fixes / execution | Users explicitly need edits or runnable checks | Sandbox boundaries, subprocess control, mutation authorization, conflicts, rollback |

Do not select infrastructure simply because it appeared in an earlier plan.
Record an adopted decision in the reviewer guide with its rationale and tests.

## Reliable PR and hosted review

Use the PR's declared target and exact recorded commit IDs. Give a review request
an identity that includes repository, revision, and relevant configuration. Repeated
events should not start unintended duplicate work. Define whether a new head cancels
an older review or lets it finish as a clearly labeled historical result.

Publication needs a stable finding/publication identity and a reconciliation policy:
if a remote comment was created but the response was lost, retrying must not blindly
create another comment. Record external identifiers and reconcile uncertain outcomes.

If hosting is added, define credential and data ownership explicitly. Authentication,
workspace authorization, model-bound source policy, and execution permissions are
separate boundaries. Never infer multi-tenant isolation from a session ID alone.

## Recovery and state ownership

If Temporal and LangGraph coexist, a possible split is Temporal owning the review
job while LangGraph owns investigation state. This remains a proposal. Assign one
owner to each retry and specify how retries reuse investigation checkpoints.

Distinguish authoritative review results, execution checkpoints, event history,
large artifacts, and caches. Do not select multiple stores without defining which
one owns each datum and how failed writes are reconciled. Cache keys must account
for relevant source revisions, rules, analyzer/model settings, and schema versions.

For side effects, design idempotency, timeouts, cancellation, and uncertain outcomes
before promising recovery. Test worker termination at stage boundaries and during
publication. A durable workflow does not automatically make arbitrary actions safe
to repeat.

## Clients and observability

A future API, editor, and terminal client should consume the same result and policy
interfaces. Keep business decisions out of rendering code. If streaming becomes a
requirement, define event IDs/order, reconnect cursors, snapshot recovery, backpressure,
and a distinction between transient token output and durable state changes.

Show stage, coverage, tool failures, progress, and cancellation accurately. Reports
and logs should expose evidence and decisions without credentials or unnecessary
raw source. Decide what is retained and who can read it. Reconnect should not submit
the same review or approval again accidentally.

## Memory, skills, and extensions

Persistent project knowledge needs a source, revision or validity scope, declared
versus inferred status, and a deletion/invalidation policy. Old notes must not
silently override current architecture rules. Memory should be optional and bounded;
a dependency graph can be a rebuildable projection rather than authoritative state.

A skill is a versioned procedure with inputs, steps, and declared capabilities.
Discover metadata before fetching full content where useful. Validate sources and
updates; a skill cannot grant itself additional permissions. Treat repository,
retrieved, and tool-produced text as data rather than authority over execution.

Plugins and MCP tools enter through the same argument validation, authorization,
timeout, and result boundaries as built-in tools. Evaluate any extension for task
quality and failure behavior before making it a default dependency.

## Execution and artifacts

Executing untrusted repository code is a separate capability from reading a diff.
Before enabling it, define filesystem/network scope, resource quotas, subprocess
lifecycle, environment exposure, and behavior when isolation cannot be established.
A permission prompt is not a substitute for an execution boundary.

Edits require known input versions, conflict detection, an explicit authorization
model, and a recoverable record of changes. Large output should become a bounded
artifact reference with ownership and retention. Cleanup, cancellation, and rollback
must not destroy concurrent developer work.

## Adoption gate

Before adding a capability, document:

1. The user problem and evidence that the current reviewer cannot address it.
2. New responsibilities, dependencies, authoritative state, and privacy boundaries.
3. Failure, retry, cancellation, and resource-limit behavior.
4. A small user-visible milestone and meaningful tests/evaluation.
5. The operational or maintenance cost, and what would make us reverse the decision.

Detailed database schemas, protocol catalogs, UI specifications, and source maps
from earlier plans were removed from active docs because they assumed a much larger
product and sometimes referenced unavailable TypeScript source. Do not treat those
historical assumptions as current implementation facts.
