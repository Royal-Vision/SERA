# SERA documentation

Start with the [reviewer architecture and build guide](reviewer/README.md).
It is the implementation blueprint for the small local code reviewer you are
building. It includes the architecture, contracts, build order, and Git exercise.

## Four documents, four purposes

| Document | Purpose | When to read |
| --- | --- | --- |
| [Reviewer guide](reviewer/README.md) | Current reviewer scope, workflow, rules, and implementation milestones | Start here |
| [Agent runtime reference](agent/README.md) | Provider, tool, policy, and graph responsibilities | When integrating a provider or adding agent tools |
| [Future platform notes](platform/README.md) | Requirements to settle before adding hosted reviews, durable jobs, clients, memory, or skills | After a working reviewer establishes a need |
| This index | Navigation and documentation authority | When looking for the right document |

## Your first task

Implement the [first Git milestone](reviewer/README.md#8-start-here-first-git-milestone):
accept a repository and explicit target, resolve fixed commits, calculate their
merge base, and list changed files. Leave model calls and LangGraph wiring until
that boundary works.

The [developer build checklist](reviewer/README.md#7-build-sequence) lists the
suggested modules, responsibilities, six milestones, and completion checks. Create
files progressively as each milestone needs them. The guide describes intended
behavior; it does not claim the implementation exists.

## Authority and maintenance

- The reviewer guide defines the current v1 scope. Runtime and future-platform
  notes do not silently add requirements to it.
- Source establishes current implementation. A proposal, example, model-generated
  note, or framework migration plan is not proof of implemented behavior.
- Update the relevant existing document when a decision changes. State the reason,
  affected boundaries, and verification rather than creating another competing plan.
- Keep debugging notes out of normative architecture. Record a lasting decision
  only when it is deliberately adopted.
- Keep diagrams small, explain their arrows, distinguish current behavior from
  proposed behavior, and use text/tables for contracts and failure cases.

## What was consolidated

The old agent phases and tool tutorial are summarized in the runtime reference.
The overlapping platform architecture, runtime SRS, CLI, skills, memory, and
execution specifications are summarized as future decisions. Separate diagram
instructions are reduced to the guidance above.

Removed material includes archived agent drafts, stale implementation critiques,
the unrelated medical RAG SRS, and unused/duplicate images. Earlier tracked text
remains available in Git history; it is not an active specification. Some old
source comments still mention phase numbers; the runtime reference maps those
numbers to their current topics.
