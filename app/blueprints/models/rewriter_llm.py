import re
import json
import torch
import mlflow
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline

from langchain_huggingface import HuggingFacePipeline
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate
from langchain_core.messages import SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda

from app.blueprints.prompts.rewriter_prompt import RewriterStructure
from app.configs.logger import get_logger

logger = get_logger()

PROMPT_URI = "prompts:/QueryRewriter@latest"
mlflow_prompt = mlflow.genai.load_prompt(PROMPT_URI)

SYSTEM_PROMPT = mlflow_prompt.template

model_id = "Qwen/Qwen3-4B-Instruct-2507"

tokenizer = AutoTokenizer.from_pretrained(model_id)

model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    device_map="auto",
    attn_implementation="sdpa",
)

hf_pipeline = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
    max_new_tokens=120,
    do_sample=False,
    return_full_text=False,
    pad_token_id=tokenizer.eos_token_id,
    clean_up_tokenization_spaces=False,
)

llm = HuggingFacePipeline(pipeline=hf_pipeline)


def extract_first_json_object(text: str) -> dict:
    text = text.strip()

    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in model output: {text!r}")

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1

            if depth == 0:
                json_text = text[start:i + 1]
                return json.loads(json_text)

    raise ValueError(f"Incomplete JSON object in model output: {text!r}")


def clean_and_parse(text: str) -> RewriterStructure:
    data = extract_first_json_object(text)

    # Safety fallback: normalized_query must be English-ish.
    # If model returns Arabic here, fail fast and retry.
    if re.search(r"[\u0600-\u06FF]", data.get("normalized_query", "")):
        raise ValueError(f"normalized_query is not English: {data}")

    return RewriterStructure(**data)


prompt_template = ChatPromptTemplate.from_messages([
    SystemMessage(content=SYSTEM_PROMPT),
    HumanMessagePromptTemplate.from_template("Input: {query}\nOutput:"),
])

rewrite_chain = (
    prompt_template
    | llm
    | StrOutputParser()
    | RunnableLambda(clean_and_parse)
).with_config(run_name="query_rewriter")

robust_rewrite_chain = rewrite_chain.with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,
)

result = robust_rewrite_chain.invoke({
    "query": "فنادق رخيصه قريب مطار دبي"
})

print(f"✅ llm output: {result} | {type(result)}")