# 2. Install and run

## 1. Use a virtual environment

From the repository root, activate the environment your team normally uses.
On Windows PowerShell this is commonly:

```powershell
.venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

## 2. Install LangGraph

Choose the package manager your project uses:

```bash
pip install -U langgraph
# or
uv add langgraph
```

Verify it is installed:

```bash
python -c "import langgraph; print('LangGraph is ready')"
```

## 3. Run the no-model examples

```bash
python learning-langgraph/examples/01_basic_graph.py
python learning-langgraph/examples/02_conditional_routing.py
```

Expected learning outcomes:

| Example | What it proves |
| --- | --- |
| `01_basic_graph.py` | State flows through a node and reaches `END`. |
| `02_conditional_routing.py` | An edge can select a route from the current state. |

## 4. Add a model only after the graph works

An agent requires a model provider credential. Keep that credential in an
environment variable or your approved secret manager—never hard-code it in a
graph or commit it. Tools should be schema-validated and authorized before
execution.

For SERA, begin with read-only tools and the contracts already defined in
[`app/agent/contracts.py`](../app/agent/contracts.py). The graph should live in
the agent runtime package; FastAPI routes should only validate a command and
start/resume a run.

## Useful troubleshooting

| Problem | Check |
| --- | --- |
| `ModuleNotFoundError: langgraph` | Activate the intended virtual environment, then install `langgraph`. |
| API-key/model error | Run the no-model examples first; add the provider key only for model-backed examples. |
| Graph loops forever | Add a stop condition, step budget, or explicit `END` route. |
| Unsafe tool action | Keep policy/authorization outside the model and use typed tool contracts. |
