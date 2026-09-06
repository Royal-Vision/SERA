# Multi-Agent Orchestration

**Part of the [SERA Agent implementation plan](README.md).**

---

## 7. Multi-agent

### 7.1 Default: don't

Every handoff costs an LLM call plus a context re-read. A supervisor delegating to two
specialists is **three** LLM calls minimum. On a 700 ms-TTFT provider that is 2.1 s before a
token. Most requests that "feel" multi-agent are one agent with more tools.

**Use a single agent until you can name the specific capability that fails without a second one.**

### 7.2 When it is justified

| Pattern | Use when | Latency |
|---|---|---|
| **Agent-as-tool** | a sub-task needs its own tool set and prompt, and its result is a *value* (e.g. `clinical_reviewer(answer)` → risk assessment) | +1 LLM call, sequential |
| **Parallel fan-out (`Send`)** | N independent sub-tasks, each a different specialist | +1× LLM (they run **concurrently**) |
| **Supervisor handoff** | genuinely distinct long-lived roles | +2 or more, sequential |
| **Sequential pipeline** | fixed stages with no routing decision | use a `StateGraph`, not agents |

### 7.3 Parallel fan-out is the only latency-neutral option

`Send` (verified in `langgraph.types`) dispatches to N nodes in the **same superstep** — they
execute concurrently. Wall-clock cost is the slowest branch, not the sum.

```python
def fan_out(state: SeraState):
    return [
        Send("retrieval_specialist",   {"query": state["query_norm"]}),
        Send("interaction_specialist", {"drugs": state["drugs"]}),
        Send("guideline_specialist",   {"topic": state["topic"]}),
    ]
```

Three specialists, one LLM call's worth of wall clock. Rules:
- Only fan out when the branches are **truly independent** — any dependency serializes it.
- Reduce with an `Annotated[list, operator.add]` state field, not a second LLM call, whenever
  the merge is mechanical.
- Give each branch its own deadline. One slow branch must not hold the response — take what
  finished and note what did not.

### 7.4 Concretely, for SERA

Ship **one** agent. Add a second only for the complex-clinical case, and only via `Send`:

```
AGENTIC → fan_out ─┬─► retrieval_specialist   (rag_search, rag_fetch_doc)
                   ├─► clinical_specialist    (dosage, interactions, units)
                   └─► guideline_specialist   (web_search, doc corpus)
                        └─► reduce → synthesis (1 LLM call) → stream
```

Four LLM calls of work in two calls of wall clock. Everything else stays single-agent.

---

---

← [Previous](06-langgraph.md) · [Index](README.md) · [Next](08-providers.md) →
