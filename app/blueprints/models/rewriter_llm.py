"""
Multilingual Query Rewriter Pipeline
=====================================
Uses Qwen2.5-1.5B-Instruct to detect language (Arabic / English),
fix grammar, and rewrite queries — all wrapped in a LangChain
RunnableLambda pipeline with batching, retry, and passthrough support.
"""

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from app.blueprints.prompts.rewriter_prompt import register_prompts
import mlflow


MLFLOW_TRACKING_URI = "https://mlflow.ghoniem.online"
EXPERIMENT_NAME = "sera-ai"

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
mlflow.set_experiment(EXPERIMENT_NAME)
mlflow.langchain.autolog()

# ─────────────────────────────────────────────
# 1. Model setup
# ─────────────────────────────────────────────

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model_id = "Qwen/Qwen2.5-1.5B-Instruct"

print(f"Loading model on {device} …")

tokenizer = AutoTokenizer.from_pretrained(model_id)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    device_map=device,
)

print("Model loaded.\n")

# ─────────────────────────────────────────────
# 2. Core rewrite function
# ─────────────────────────────────────────────
PROMPT_URI = "prompts:/QueryRewriter@latest"

prompt = mlflow.genai.load_prompt(PROMPT_URI)

SYSTEM_PROMPT = prompt.template


def rewrite_query(query: str) -> str:
    """
    Rewrites a single query using the local Qwen model.
    Language is auto-detected; output stays in the same language.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    outputs = model.generate(
        **inputs,
        max_new_tokens=80,
        temperature=0.2,
        do_sample=False,
        pad_token_id=tokenizer.eos_token_id,
    )

    response = tokenizer.decode(
        outputs[0][inputs["input_ids"].shape[1] :],
        skip_special_tokens=True,
    )

    return response.strip()


# ─────────────────────────────────────────────
# 3. LangChain Runnables
# ─────────────────────────────────────────────

# --- 3a. Bare runnable (invoke / batch / with_retry)
rewrite_runnable = RunnableLambda(rewrite_query, name="query_rewriter")

# --- 3b. With automatic retry (useful when small model occasionally fails)
robust_rewriter = rewrite_runnable.with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,
)

# --- 3c. Passthrough: keeps original query AND adds rewritten version
#         Input:  str  →  Output: {"original": str, "rewritten": str}
passthrough_pipeline = RunnablePassthrough.assign(
    rewritten=rewrite_runnable
).with_config(run_name="rewrite_with_passthrough")


# ─────────────────────────────────────────────
# 4. Optional downstream step (stub — plug in your retriever here)
# ─────────────────────────────────────────────

def mock_hotel_search(query: str) -> list[dict]:
    """Stub retriever — replace with your real vector / keyword search."""
    return [
        {"rank": 1, "name": "Grand Hyatt Dubai", "match_query": query},
        {"rank": 2, "name": "Rove Downtown Dubai", "match_query": query},
    ]


full_pipeline = rewrite_runnable | RunnableLambda(
    mock_hotel_search, name="hotel_search"
)


# ─────────────────────────────────────────────
# 5. Demo runs
# ─────────────────────────────────────────────

def separator(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


if __name__ == "__main__":

    # ── A. Single invoke ──────────────────────────────────
    separator("A. Single invoke")
    q = "cheap apartments in dubai marina with pool"
    print(f"IN:  {q}")
    print(f"OUT: {rewrite_runnable.invoke(q)}")

    # ── B. Batch (parallel threads) ───────────────────────
    separator("B. Batch — mixed Arabic / English")
    queries = [
        "cheap apartments in dubai marina with pool",
        "شقق رخيصة في دبي مارينا فيها مسبح",
        "resturant near burj khalifa good view",
        "مطعم قريب من برج خليفه منظر حلو",
        "hotel downtown dubai near metro station",
        "فندق وسط دبي قرب محطة المترو",
    ]
    results = rewrite_runnable.batch(queries)
    for original, rewritten in zip(queries, results):
        print(f"\nIN:  {original}")
        print(f"OUT: {rewritten}")

    # ── C. Robust rewriter (with retry) ───────────────────
    separator("C. Robust rewriter (retry on failure)")
    q = "apartmens with gym near dxb airprot"
    print(f"IN:  {q}")
    print(f"OUT: {robust_rewriter.invoke(q)}")

    # ── D. Passthrough — keeps original + rewritten ───────
    separator("D. Passthrough pipeline")
    q = "فنادق رخيصه قريب مطار دبي واي فاي مجاني"
    result = passthrough_pipeline.invoke(q)
    print(f"ORIGINAL:  {result['original'] if 'original' in result else q}")
    print(f"REWRITTEN: {result['rewritten']}")

    # ── E. Full pipeline: rewrite → search ────────────────
    separator("E. Full pipeline: rewrite → hotel search")
    q = "cheap hotel with pool near dubai mall"
    print(f"IN:  {q}")
    hotels = full_pipeline.invoke(q)
    for h in hotels:
        print(f"  #{h['rank']} {h['name']}")

    print("\n✓ All demos complete.\n")