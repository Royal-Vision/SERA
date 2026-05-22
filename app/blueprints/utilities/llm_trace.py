import mlflow
import logging

from app.blueprints.prompts.rewriter_prompt import register_prompts

logger = logging.getLogger(__name__)

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
        # register_prompts()

        self._initialized = True
        logger.info(f"MLflow initialized → {MLFLOW_TRACKING_URI} | {EXPERIMENT_NAME}")

    def register_prompt(self, name: str, template: str) -> None:
        """Register a single prompt in MLflow prompt registry."""
        try:
            mlflow.genai.register_prompt(name=name, template=template)
            logger.info(f"Prompt registered: {name}")
        except Exception as e:
            logger.warning(f"Failed to register prompt '{name}': {e}")
    
    def get_prompt(self, name: str, version: str = "latest") -> str:
        """Fetch prompt template from MLflow registry."""
        prompt = mlflow.genai.load_prompt(f"prompts:/{name}@{version}")
        return prompt.template




# Singleton instance — import this everywhere
tracker = MLflowTracker()