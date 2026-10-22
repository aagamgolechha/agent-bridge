import logging
import sys
import logging_loki

LOKI_URL = "http://localhost:3100/loki/api/v1/push"


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("mock-payment")

    # Avoid duplicate handlers if configuration is called more than once.
    if not any(
        getattr(handler, "_is_loki_handler", False)
        for handler in logger.handlers
    ):
        try:
            handler = logging_loki.LokiHandler(
                url=LOKI_URL,
                tags={
                    "application": "mock-payment",
                    "environment": "local",
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

