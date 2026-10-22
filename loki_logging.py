"""Central logging configuration with optional Loki shipping.

- Controlled by ``LOKI_ENABLED`` (default: true) and ``LOKI_URL``.
- Never assumes a localhost Loki is reachable in production.
- Falls back safely to console logging when Loki is disabled,
  the ``python-logging-loki`` package is missing, or init fails.
- Never logs secrets or full card numbers (callers must redact).
"""

import logging
import os
import sys


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


LOKI_ENABLED = _env_bool("LOKI_ENABLED", True)
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100/loki/api/v1/push")
LOKI_SERVICE = os.getenv("LOKI_SERVICE_NAME", "mock-payment")
LOKI_ENV = os.getenv("LOKI_ENVIRONMENT", os.getenv("ENVIRONMENT", "local"))


def is_loki_enabled() -> bool:
    """Return True when Loki shipping is enabled via env."""
    return _env_bool("LOKI_ENABLED", True)


def get_loki_url() -> str:
    """Return the configured Loki push URL."""
    return os.getenv("LOKI_URL", "http://localhost:3100/loki/api/v1/push")


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("mock-payment")

    # Avoid duplicate Loki handlers if configuration is called more than once.
    has_loki = any(
        getattr(handler, "_is_loki_handler", False)
        for handler in logger.handlers
    )
    if is_loki_enabled() and not has_loki:
        try:
            import logging_loki  # lazy import so app runs without the package

            handler = logging_loki.LokiHandler(
                url=get_loki_url(),
                tags={
                    "application": os.getenv("LOKI_SERVICE_NAME", "mock-payment"),
                    "environment": os.getenv("LOKI_ENVIRONMENT", os.getenv("ENVIRONMENT", "local")),
                },
                version="1",
            )
            handler._is_loki_handler = True  # type: ignore[attr-defined]
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s - %(message)s"
                )
            )
            logger.addHandler(handler)
        except Exception as e:
            # Never crash the app because Loki is unavailable.
            # Console handler below keeps logs visible.
            print(f"Warning: Loki logging disabled ({e})", file=sys.stderr)
    elif not is_loki_enabled():
        # Explicitly disabled — remove any stale Loki handlers (e.g. tests).
        logger.handlers = [
            h for h in logger.handlers if not getattr(h, "_is_loki_handler", False)
        ]

    # Add console stream handler so logs also appear in the terminal
    if not any(
        isinstance(handler, logging.StreamHandler)
        and not getattr(handler, "_is_loki_handler", False)
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

