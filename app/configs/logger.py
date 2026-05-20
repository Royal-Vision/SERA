import logging
import sys
import os
from logging.handlers import RotatingFileHandler
import threading


class SingletonLogger:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_logger()
        return cls._instance

    def _init_logger(self):
        self.logger = logging.getLogger("viral_flow_ai")

        # Prevent duplicate handlers in reload / uvicorn workers
        if self.logger.handlers:
            return

        self.logger.setLevel(logging.INFO)

        # =========================
        # Ensure log directory exists
        # =========================
        os.makedirs("log", exist_ok=True)

        # =========================
        # Formatter (includes file + line)
        # =========================
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # =========================
        # Console Handler (UTF-8 + emojis)
        # =========================
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)

        # =========================
        # File Handler (ROTATING LOG)
        # =========================
        file_handler = RotatingFileHandler(
            filename="log/app.log",
            maxBytes=5 * 1024 * 1024,  # 🔥 5MB per file
            backupCount=3,             # 🔥 keeps last 3 files only
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)

        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)

        # Prevent propagation to root logger
        self.logger.propagate = False

    def get_logger(self):
        return self.logger


def get_logger():
    return SingletonLogger().get_logger()