import os

import mlflow

from app.configs.config import settings
from app.configs.logger import get_logger

logger = get_logger()

MLFLOW_TRACKING_URI = "https://mlflow.ghoniem.online"
EXPERIMENT_NAME = "sera-ai"


def _export_s3_credentials_for_mlflow() -> None:
    """Bridge `settings.S3_*` to the env var names boto3 expects.

    MLflow's artifact backend uses standard AWS env vars internally; setting
    them here means we don't have to duplicate values into AWS_* in .env.
    """
    if settings.S3_ACCESS_KEY and not os.getenv("AWS_ACCESS_KEY_ID"):
        os.environ["AWS_ACCESS_KEY_ID"] = settings.S3_ACCESS_KEY
    if settings.S3_SECRET_KEY and not os.getenv("AWS_SECRET_ACCESS_KEY"):
        os.environ["AWS_SECRET_ACCESS_KEY"] = settings.S3_SECRET_KEY
    if settings.S3_ENDPOINT and not os.getenv("MLFLOW_S3_ENDPOINT_URL"):
        os.environ["MLFLOW_S3_ENDPOINT_URL"] = settings.S3_ENDPOINT


class MLflowTracker:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        _export_s3_credentials_for_mlflow()

        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(EXPERIMENT_NAME)
        mlflow.langchain.autolog()

        self._initialized = True
        logger.info("MLflow initialized -> %s | %s", MLFLOW_TRACKING_URI, EXPERIMENT_NAME)

    def register_prompt(self, name: str, template: str) -> None:
        try:
            mlflow.genai.register_prompt(name=name, template=template)
            logger.info("Prompt registered: %s", name)
        except Exception as exc:
            logger.warning("Failed to register prompt '%s': %s", name, exc)

    def get_prompt(self, name: str, version: str = "latest") -> str:
        prompt = mlflow.genai.load_prompt(f"prompts:/{name}@{version}")
        return prompt.template


# Singleton — import this everywhere you need MLflow
tracker = MLflowTracker()
