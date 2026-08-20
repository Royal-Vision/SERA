import mlflow

from app.configs.logger import get_logger

logger = get_logger()

MLFLOW_TRACKING_URI = "https://mlflow.ghoniem.online"
EXPERIMENT_NAME = "sera-ai"


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
