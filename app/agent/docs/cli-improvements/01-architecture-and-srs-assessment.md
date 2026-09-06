# Architecture and SRS Assessment

> A release-oriented review of the source-backed target architecture and its
> software requirements. Scores use explicit criteria rather than presentation
> volume.

[CLI improvement index](README.md) | [Docs start page](../README.md) | [Runtime SRS](../runtime-srs/README.md)

## Assessment scope

This review covers:

- ownership and process boundaries;
- model/tool/permission control;
- API, events, persistence, recovery, and artifacts;
- React Ink state and interaction architecture;
- memory, skills, sandbox, and child-agent integration;
- requirement precision, traceability, testability, and measurable quality.

It does not score unimplemented code. No target FastAPI backend, React Ink
client package, generated protocol package, or test suite is present here.

## Architecture score

| Criterion | Weight | Score | Evidence and remaining issue |
| --- | ---: | ---: | --- |
| Ownership and separation | 15% | 9.5 | Backend/client/graph/tool responsibilities are explicit; deployment process topology still needs an ADR. |
| Security and permissions | 15% | 9.4 | Central executor, deterministic precedence, durable approvals, child-scope intersection, and fail-closed behavior are strong. |
| Agent loop and control | 15% | 9.3 | Natural completion, provider-valid tool pairing, pauses, budgets, no-progress, and cancellation are well specified. |
| Protocol and recovery | 15% | 9.1 | Sequenced events, snapshots, replay, idempotency, and outbox semantics are strong; executable fixtures do not yet exist. |
| Data and artifact integrity | 10% | 8.8 | Normalized records, artifact lineage, optimistic writes, and rewind are detailed; migrations are only planned. |
| CLI interaction architecture | 10% | 8.2 | Thin-client reducer, stable regions, steering, and stop scopes are strong; edge-case state contracts need IDs/tests. |
| Sandbox and resources | 8% | 8.5 | Provider-neutral isolation and leases are sound; no concrete Python provider has passed conformance. |
| Memory, skills, delegation | 7% | 8.4 | Scope/provenance and bounded child contracts are strong; advanced capability interactions need end-to-end tests. |
| Delivery and operability | 5% | 7.2 | Roadmap and risk matrix are useful; build manifests, version pins, ADRs, CI, and runbooks are absent. |
| **Weighted architecture score** | **100%** | **8.9** | Strong architecture with implementation-proof gaps, not a redesign problem. |

## SRS score

| Criterion | Weight | Score | Assessment |
| --- | ---: | ---: | --- |
| Functional coverage | 20% | 9.5 | Tools, permissions, API/events, data, graph, recovery, observability, memory, skills, and sandbox behavior are covered. |
| Requirement precision | 15% | 9.0 | Most critical backend behaviors use exact identities and invariants; some prose/checklists remain normative without IDs. |
| Source traceability | 15% | 9.5 | `CURRENT`, `TARGET`, and `GAP` are separated with repository links. |
| Internal consistency | 10% | 8.9 | Ownership precedence is stated; duplication across overview/SRS folders still needs automated consistency checks. |
| Verifiability | 15% | 8.4 | Acceptance and adversarial cases are detailed, but no executable fixtures or CI evidence exists. |
| Requirement-to-test traceability | 10% | 6.8 | Runtime/agent IDs exist, but there is no checked-in matrix mapping each P0 ID to a test and result. |
| Non-functional measurability | 10% | 7.8 | Initial latency and safety limits exist; hardware profiles, memory ceilings, and benchmark baselines are not measured. |
| Change/version governance | 5% | 7.4 | Protocol evolution is defined; ADRs, schema compatibility reports, and dependency/version policy are not implemented. |
| **Weighted SRS score** | **100%** | **8.7** | High-quality SRS content that needs executable traceability. |

## Per-file SRS ratings

### Runtime SRS

| File | Rating | Strongest quality | Main improvement |
| --- | ---: | --- | --- |
| [Runtime SRS index](../runtime-srs/README.md) | 8.7 | Clear authority, scope, requirement families, and verification strategy. | Give cross-cutting acceptance rules stable IDs and trace them to CI. |
| [Tool Contract](../runtime-srs/01-tool-contract.md) | 9.3 | Strict stages, output/artifact policy, concurrency, idempotency, and executor invariants. | Turn model fragments into an importable reference package and contract fixtures. |
| [Complete Tool Catalog](../runtime-srs/02-tool-catalog.md) | 9.2 | Full source-backed inventory, priorities, aliases, dynamic tools, and unresolved references. | Add generated registry-to-catalog parity tests and one machine-readable catalog. |
| [Permission System](../runtime-srs/03-permission-system.md) | 9.5 | Excellent precedence, durable approval, exact hashes, child scope, shell/MCP policy, and audit. | Add executable policy decision tables/property tests and a formal rule migration format. |
| [API and Event Protocol](../runtime-srs/04-api-and-event-protocol.md) | 9.2 | Strong command identity, event ordering, replay, versioning, backpressure, and privacy. | Generate OpenAPI/JSON Schema/TS validators and publish golden compatibility reports. |
| [Data Model](../runtime-srs/05-data-model.md) | 9.0 | Detailed normalized ownership, constraints, indexes, events, artifacts, and recovery records. | Create actual SQLAlchemy/Alembic models and crash/migration fixtures. |
| [Python Types and Performance](../runtime-srs/06-python-types-and-performance.md) | 8.8 | Correct Pydantic/TypedDict/dataclass/ORM boundaries and hot-path guidance. | Pin supported versions and add representative validation/serialization/checkpoint benchmarks. |

### Agent SRS

| File | Rating | Strongest quality | Main improvement |
| --- | ---: | --- | --- |
| [Agent Runtime SRS](../agent-architecture/01-agent-runtime-srs.md) | 9.1 | Explicit graph ownership, node phases, child runs, skills/hooks/plugins, and result contracts. | Provide a compilable graph skeleton and topology/route fixtures. |
| [LangGraph Control Loop](../agent-architecture/02-langgraph-control-loop.md) | 9.5 | Best chapter: natural completion, exhaustive routing, budgets, no-progress, waits, recovery, and terminal semantics. | Convert route table and limits into executable property/state-machine tests. |
| [Checkpointing and Recovery](../agent-architecture/03-state-checkpointing-and-recovery.md) | 9.3 | Strong separation of application DB/checkpointer and ambiguous-side-effect recovery. | Choose/check a production checkpointer and run automated crash-point conformance. |
| [Observability and Interactions](../agent-architecture/04-observability-and-interactions.md) | 8.9 | Rich visible state without exposing hidden reasoning, plus audit/metrics/accessibility. | Define exact telemetry schemas, cardinality budgets, and client usability tests. |

### CLI specifications before this improvement folder

| File | Rating | Strongest quality | Main improvement |
| --- | ---: | --- | --- |
| [CLI Architecture](../cli-architecture/README.md) | 8.2 | Correct thin-client boundary, stable regions, and invariant-focused build order. | Add normative IDs, terminal conformance, and test traceability. |
| [Fast Response Pipeline](../cli-architecture/01-fast-response-pipeline.md) | 8.7 | Distinguishes acknowledgement/text/work latency and canonical/provisional events. | Implement benchmark profiles and regression evidence. |
| [Live Steering](../cli-architecture/02-live-steering.md) | 9.0 | Excellent safe-boundary, durable claim, cancel scope, and race semantics. | Add full interactive input/focus fixtures and multi-client steering tests. |
| [Multi-Agent Control](../cli-architecture/03-multi-agent-control.md) | 8.9 | Rational delegation, batch admission, child scope, messaging, and stop semantics. | Validate usability at scale and resource/merge conflict behavior end to end. |

## Quantitative audit

The current runtime, agent, and CLI architecture folders contain:

| Measure | Result |
| --- | ---: |
| Audited Markdown specifications | 16 |
| Unique `TOOL/PERM/API/EVT/DATA/TYPE/AGT/LOOP/CHK/OBS` IDs | 302 |
| Duplicate requirement IDs | 0 |
| CLI-specific normative requirement IDs before this folder | 0 |

The absence of duplicate IDs is excellent. The largest structural SRS gap is
the missing CLI requirement family and requirement-to-test index, not backend
coverage.

## Findings

### P0: CLI requirements were not independently traceable

The CLI documents contain strong rules and checklists, but they did not assign
stable IDs comparable to `API-040`, `LOOP-021`, or `PERM-045`. This makes it
hard to prove that reconnect, focus, paste, cancellation, and permission UX all
have tests.

**Resolution:** this folder introduces `CLI-UX-*`, `CLI-RES-*`, and
`CLI-PERF-*`, with test mappings in
[04 - Traceability and Delivery](04-traceability-and-delivery.md).

### P0: contracts are descriptive, not generated

The architecture correctly chooses Pydantic as protocol source of truth and a
generated TypeScript client package. Those artifacts do not exist in the
snapshot. Hand-building Ink state before generation would create protocol
drift.

**Resolution:** make schema generation and fixture compatibility the first CLI
delivery gate.

### P0: interaction state needs one authoritative machine

Focus priority is documented, but exact transitions among composer,
autocomplete, permission, diff, reconnect, cancel, and exit are spread across
sections. `Esc` and `Ctrl+C` are safety-sensitive and need one deterministic
dispatcher contract.

**Resolution:** implement the state and input rules in
[02 - CLI UX and Terminal Contract](02-cli-ux-and-terminal-contract.md).

### P1: terminal portability needs conformance profiles

Responsive widths, color fallback, and `TERM=dumb` already exist. Remaining
cases include grapheme width, combining marks, terminal escape sequences,
bracketed paste, ConPTY/Windows Terminal, tmux/screen, redirected streams, and
very small heights.

**Resolution:** add capability fixtures and golden frames by terminal profile.

### P1: performance objectives need measured baselines

The fast-response document defines useful targets. There is not yet a harness
that records startup phases, reducer/render duration, event lag, resident
memory, snapshot bytes, and long-session behavior on named hardware profiles.

**Resolution:** add the benchmark and admission rules in
[03 - Resilience and Performance](03-cli-resilience-and-performance.md).

### P1: architecture decisions need durable records

Important choices are in prose but not in a compact decision log. Later teams
could reintroduce a client-side model loop, raw graph streaming, or a second
session database without noticing the original constraints.

**Resolution:** create ADRs for backend authority, event projection,
Pydantic-to-TypeScript generation, checkpointer separation, sandbox provider,
draft persistence, and terminal rendering strategy.

### P1: capability/version degradation needs UI contracts

The protocol negotiates versions/capabilities, but the CLI needs exact states
for server too old/new, unsupported event major, feature unavailable, schema
generation mismatch, and update required versus optional.

**Resolution:** add deterministic compatibility states and exit codes.

## What should not be redesigned

Keep these decisions unless source evidence or an ADR proves a stronger option:

1. One authoritative FastAPI backend for both clients.
2. A custom model-driven LangGraph loop inside a hard safety envelope.
3. One central tool executor and permission policy.
4. Durable commands/events plus replay, not WebSocket memory as truth.
5. A pure shared client reducer for replay and live events.
6. Provisional deltas separated from canonical terminal events.
7. File-first memory and content-addressed artifacts for the first version.
8. Independent child runs with intersected permissions and bounded budgets.
9. A restricted VS Code host bridge and an untrusted webview.

Changing those would increase risk without closing the current gaps.

## Target score after improvements

| Area | Current | Target after P0/P1 evidence |
| --- | ---: | ---: |
| Overall architecture | 8.9 | 9.3 |
| Runtime/agent SRS | 8.7 | 9.3 |
| CLI specification | 8.2 | 9.2 |
| Implementation readiness | 7.0 | 8.8 |

The score rises from executable contracts, measurements, and traceability, not
from adding more features.
