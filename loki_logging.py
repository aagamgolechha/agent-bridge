import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env before is_loki_enabled()/get_loki_url() are evaluated,
# so LOKI_ENABLED works whether logging is configured via app.py or directly.
load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

LOKI_URL_DEFAULT = "http://localhost:3100/loki/api/v1/push"


def is_loki_enabled() -> bool:
    return os.getenv("LOKI_ENABLED", "false").lower() == "true"


def get_loki_url() -> str:
    return os.getenv("LOKI_URL", LOKI_URL_DEFAULT)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("mock-payment")

    # Avoid duplicate handlers if configuration is called more than once.
    if is_loki_enabled() and not any(
        getattr(handler, "_is_loki_handler", False)
        for handler in logger.handlers
    ):
        try:
            import logging_loki

            handler = logging_loki.LokiHandler(
                url=get_loki_url(),
                tags={
                    "application": os.getenv("LOKI_SERVICE_NAME", "mock-payment"),
                    "environment": os.getenv("LOKI_ENVIRONMENT", "local"),
                },
                version="1",
            )
            handler._is_loki_handler = True
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s - %(message)s"
                )
            )
            logger.addHandler(handler)
        except Exception as e:
            print(f"Warning: Failed to initialize LokiHandler: {e}", file=sys.stderr)

    # Add console stream handler so logs also appear in the terminal
    if not any(
        isinstance(handler, logging.StreamHandler) and not getattr(handler, "_is_loki_handler", False)
        for handler in logger.handlers
    ):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s - %(message)s"
            )
        )
        logger.addHandler(console_handler)

    logger.setLevel(logging.INFO)
    logger.propagate = False

    return logger

