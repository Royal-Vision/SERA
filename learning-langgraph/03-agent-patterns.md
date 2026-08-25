# 3. Agent patterns

Use the simplest pattern that solves the task. More agents increase latency,
cost, state-management complexity, and failure modes.

| Pattern | Shape | Use it when |
| --- | --- | --- |
| Tool-using agent | model ↔ tools | One agent has a manageable tool set. |
| Supervisor + subagents | supervisor → specialists | A central agent needs to delegate isolated work. |
| Router / fan-out | router → parallel specialists → synthesis | The request spans distinct, independent domains. |
| Handoff | agent A → agent B | A specialist needs to take over a user-facing conversation. |
| Custom workflow | fixed steps + agent nodes | Policy, approvals, branches, or business rules control part of the flow. |

## A good first production shape for SERA

```text
request → validate → policy/permission decision → model → tool executor
                                              ↑                 │
                                              └── tool result ──┘
                                                        │
                                                    final answer
```

This is a custom workflow with an agentic model/tool loop. Crucially, the model
may request a tool, but it does not decide whether a risky capability is
allowed. That belongs to the permission and executor layers.

## Add multi-agent only when justified

Choose a supervisor/subagent design if specialists need different tools or
large, separate context. Choose a router if the input categories are clear and
workers can run concurrently. For a few tools, a single agent is easier to
trace, test, and secure.

See the official [multi-agent visual overview](https://langchain-ai.github.io/langgraph/tutorials/multi_agent/multi-agent-collaboration/)
and the generated [pattern image](../docs/langgraph-agent-patterns.png).
