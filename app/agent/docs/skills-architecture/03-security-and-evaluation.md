# Skill Security and Evaluation

> Treat skills as executable intent: review requested capabilities, source,
> workflow steps, hooks, and artifacts before allowing repeated invocation.

[Skills architecture index](README.md) | [Docs start page](../README.md) | [Diagram standard](../diagram-standard.md)

## Effective capability

```text
effective_skill_tools = deployment_policy
                      AND workspace_policy
                      AND current_run_scope
                      AND skill_allowed_tools
                      AND source_trust_policy
                      AND per-invocation_decision
```

A skill's `allowed-tools` is a maximum request, never a grant. Omitting a tool
does not deny the user from explicitly using it elsewhere; it constrains that
skill invocation.

## Source trust policy

| Source | Default posture |
| --- | --- |
| Managed | Organization-controlled; still schema-validated and versioned. |
| Bundled | Product-controlled; signed/build-pinned and tested. |
| User | User-owned; normal permissions and linting apply. |
| Project | Repository content; untrusted until workspace trust allows it. |
| Plugin | Bound to installed plugin identity/version/permissions. |
| MCP | Remote untrusted data; no shell expansion/hooks by default. |
| Remote catalog | Search/fetch only; no invocation/install until verified and policy allows. |

## Threats and controls

| Threat | Required control |
| --- | --- |
| Prompt injection in body | Treat instructions as workflow content constrained by policy; system/developer policy wins. |
| Over-broad tool pattern | Capability diff and explicit review; reject unknown pattern syntax. |
| Shell/frontmatter execution | Trusted local-only gate; no execution during discovery/fetch/lint. |
| Path traversal/support bundle | Resolve every bundled path under immutable skill root; reject symlinks/archives escaping it. |
| Dependency substitution | Pin provider, namespace, version, and digest. |
| Name shadowing | Qualified identity, deterministic precedence, conflict UI. |
| Skill update changes permissions | New digest/revision triggers capability diff and reapproval. |
| Secret exfiltration | Sandbox/network policy, DLP, no secret in prompts/logs/cache metadata. |
| Infinite recursive skill/agent use | Invocation stack, max depth, child/budget guards, no self-cycle. |
| Irreversible workflow step | Typed human checkpoint and exact action review. |
| False success | Required artifact and verification contracts. |

## Invocation stack

Track:

- invocation ID;
- skill ID/revision/digest;
- parent run and parent skill invocation;
- invocation depth;
- arguments digest;
- effective capability snapshot;
- current step and produced artifacts;
- budget and cancellation state.

Reject a direct/indirect cycle when the same skill digest and normalized
arguments reappear in the active stack, unless a tightly bounded recursion
contract explicitly allows it.

## Lint levels

| Level | Checks |
| --- | --- |
| Parse | YAML/frontmatter/Markdown bounds, supported schema version. |
| Identity | Name, source namespace, revision, digest, conflict. |
| Interface | Arguments, substitutions, triggers, path conditions. |
| Capability | Tool patterns, hooks, shell, model, agent, source trust. |
| Workflow | Steps, success criteria, artifacts, dependencies, checkpoints. |
| Safety | Secrets, suspicious exfiltration, traversal, recursive invocation. |
| Simulation | Dry run with denied side effects and fixture inputs. |

Unknown security-relevant fields fail closed. Cosmetic unknown metadata may be
preserved only under a namespaced extension object.

## Evaluation suite

Each important skill should have fixtures for:

1. positive trigger and explicit invocation;
2. near-miss prompt where it should not auto-invoke;
3. argument validation/defaults;
4. minimum permission set;
5. denied permission behavior;
6. expected step/artifact contract;
7. partial failure and resume;
8. user checkpoint and rejection;
9. cancellation;
10. secret/injection input;
11. source update/capability diff;
12. recursion/delegation budget.

Evaluation records model/profile, skill digest, fixture version, tool registry
snapshot, result artifacts, policy outcomes, usage, and deterministic assertions.
Do not require private chain-of-thought.

## Quality gates

A skill may be:

| Status | Meaning |
| --- | --- |
| `draft` | Builder output, not invocable by model. |
| `reviewed` | Human reviewed exact digest; local explicit invocation allowed. |
| `verified` | Lint and required evaluations pass. |
| `quarantined` | Security/quality issue; no invocation. |
| `revoked` | Provider/owner blocks this digest. |
| `superseded` | New revision available; old behavior remains auditable. |

Model auto-invocation should normally require `verified` plus source policy.
Explicit user invocation may allow `reviewed` with a warning and ordinary
permissions, depending on deployment.

## Update review

For every revision, show a semantic diff:

- source identity and signature;
- body digest and changed steps;
- added/removed arguments;
- trigger changes;
- added/removed tool patterns;
- inline/fork/model/agent/effort changes;
- hook/shell/path changes;
- changed human checkpoints;
- evaluation delta and known risks.

Never approve "always use future versions." Approve a bounded source/version
policy only when the user/admin explicitly chooses it.

## Runtime failure behavior

| Failure | Result |
| --- | --- |
| Skill body missing after registry selection | Fail invocation; refresh registry; do not select a different source silently. |
| Digest mismatch | Quarantine fetched/cache bytes and emit security event. |
| Requested tool unavailable | Return typed capability failure before workflow starts. |
| Inline context too large | Reject or propose fork; do not silently truncate required steps. |
| Forked child fails | Preserve child artifacts/partial result and skill step checkpoint. |
| User denies checkpoint | Skill becomes cancelled/blocked as defined; no later irreversible steps. |
| Skill reaches no progress | Stop with skill/run guard reason and retain diagnostic facts. |

## Repository evidence

| Source | Existing control |
| --- | --- |
| [`loadSkillsDir.ts`](../../skills/loadSkillsDir.ts) | Canonical path dedup, source policy gates, lazy loading, and remote shell restriction. |
| [`SkillTool.ts`](../../tools/SkillTool/SkillTool.ts) | Model-invocation disable, rule checks, safe-property allowlist, and inline/fork execution. |
| [`skillify.ts`](../../skills/bundled/skillify.ts) | Minimum permissions, success criteria, human checkpoints, and preview-before-write. |
| [`sandbox-adapter.ts`](../../utils/sandbox/sandbox-adapter.ts) | OS-level protection of auto-discovered `.claude/skills` paths from sandboxed commands. |
