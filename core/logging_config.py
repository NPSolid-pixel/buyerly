import logging
import re

_SECRET_PATTERNS = (
    re.compile(r"(?i)(access_token(?:=|%3D))([^&\s\"']+)"),
    re.compile(r"(?i)(appsecret_proof(?:=|%3D))([^&\s\"']+)"),
    re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/=-]+)"),
    re.compile(r"\b([0-9]{8,12}):AA[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
)


def redact_secrets(message: str) -> str:
    """Remove credentials from log messages before they reach any handler."""

    redacted = str(message)
    redacted = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[1].sub(r"\1[REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[2].sub(r"\1[REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[3].sub(r"\1:[REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[4].sub("[REDACTED_GITHUB_TOKEN]", redacted)
    redacted = _SECRET_PATTERNS[5].sub("[REDACTED_GITHUB_TOKEN]", redacted)
    return redacted


class RedactingFormatter(logging.Formatter):
    """Formatter that also redacts secrets inside formatted exceptions."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))
