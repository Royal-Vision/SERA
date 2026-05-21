import mlflow
from pydantic import BaseModel


class RewriterStructure(BaseModel):
    user_query: str          # rewritten in original language — shown to user
    normalized_query: str    # always English — sent to Qdrant for higher scores
    query_lang: str          # "ar" or "en"


QUERY_REWRITER_PROMPT = """\
You are a JSON-only API. You must always respond with a valid JSON object and nothing else.

You receive a search query in Arabic or English.
You must return exactly this JSON structure:

{{
  "user_query": "<rewrite the query with correct grammar in its ORIGINAL language>",
  "normalized_query": "<ENGLISH ONLY translation for vector search>",
  "query_lang": "<ar or en>"
}}

RULES:
- user_query: keep the original language. Arabic in → Arabic out. English in → English out.
- normalized_query: MUST be in ENGLISH. ALWAYS. Even if input is Arabic. TRANSLATE TO ENGLISH.
- query_lang: "ar" if input is Arabic, "en" if input is English.
- Output ONLY the JSON object. No extra text. No explanation.

EXAMPLES:

Input: فنادق رخيصه قريب مطار دبي
Output:
{{
  "user_query": "فنادق رخيصة قريبة من مطار دبي",
  "normalized_query": "cheap hotels near Dubai Airport",
  "query_lang": "ar"
}}

Input: cheap hotel near dubai airport wifi
Output:
{{
  "user_query": "Cheap hotels near Dubai Airport with Wi-Fi",
  "normalized_query": "cheap hotels near Dubai Airport with Wi-Fi",
  "query_lang": "en"
}}

Input: مطاعم إيطالية في وسط دبي
Output:
{{
  "user_query": "مطاعم إيطالية في وسط دبي",
  "normalized_query": "Italian restaurants in downtown Dubai",
  "query_lang": "ar"
}}

Input: شقق فندقية مع مسبح في دبي مارينا
Output:
{{
  "user_query": "شقق فندقية مع مسبح في دبي مارينا",
  "normalized_query": "hotel apartments with pool in Dubai Marina",
  "query_lang": "ar"
}}
"""


def register_prompts() -> None:
    """Register QueryRewriter in MLflow prompt registry."""
    mlflow.genai.register_prompt(
        name="QueryRewriter",
        template=QUERY_REWRITER_PROMPT,
        response_format=RewriterStructure,
        commit_message="QueryRewriter v3 — stronger normalization instruction for 1.5B model",
        tags={
            "language": "ar,en",
            "stage": "rewriter",
            "model": "Qwen2.5-1.5B-Instruct",
        },
    )