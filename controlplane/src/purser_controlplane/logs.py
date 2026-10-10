"""Plain-text logging with structured extras. Callers pass identifiers and
reason codes only; never tokens, request bodies, or Keycloak responses."""

import logging
import sys

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD}
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        return base


def configure(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs every request URL at INFO, and admin-API URLs can carry an
    # email (a lookup by address). Keep HTTP client logs to warnings.
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
