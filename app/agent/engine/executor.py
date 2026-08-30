"""ToolEngine -- Phase 05. The pipeline every tool call passes through.

Order is load-bearing:

    resolve -> repair -> coerce -> validate -> preconditions -> permission -> execute

Each stage may reject, and a rejection is rendered as a PROMPT, not an
exception: the model has to be able to read what went wrong and fix its next
call without a human in the loop.

Also owns what a per-call function cannot see:
  - conflict-aware batching  (ConcurrencyClass + write-set overlap)
  - the circuit breaker      (a tool failing the same way repeatedly stops)
  - budget enforcement       (AgentContext.budget_for, contracts.py)

Import rule: stdlib + pydantic + anyio + app.agent.{contracts,base,tools}.
Never graph/, never langgraph.
"""
