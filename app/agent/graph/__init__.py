"""LangGraph orchestration -- L4. Phases 08-09.

The ONLY package allowed to import langgraph (Phase 00 §6). Everything below it
runs headless, which is what makes the Step 4 stdio loop and every engine test
independent of the framework.

What we take from LangGraph -- scheduling primitives, never execution semantics:
    astream(stream_mode="messages")   token streaming with real backpressure
    Send                              parallel fan-out in one superstep
    interrupt / Command(resume=...)   the approval flow (Phase 11)
    checkpointers + durability        resumable sessions (Phase 10)
    conditional edges                 control flow that stays readable at 10 nodes

We do NOT take ToolNode. Tool execution is engine/executor.py.
"""
