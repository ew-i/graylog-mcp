"""Remove credential-like values before data is returned through MCP."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

_DEFAULT_SENSITIVE_KEYS = frozenset(
    {
        "accesskey",
        "apikey",
        "authorization",
        "clientsecret",
        "cookie",
        "credential",
        "password",
        "passwd",
        "privatekey",
        "secret",
        "session",
        "sessionid",
        "sessiontoken",
        "token",
        "webhook",
    }
)

_LABELED_SECRET = re.compile(
    r"(?i)(\b(?:authorization\s*:\s*(?:bearer|basic)\s+|"
    r"(?:access[_ -]?key|api[_ -]?key|client[_ -]?secret|password|passwd|"
    r"refresh[_ -]?token|secret|token)\s*[:=]\s*))"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_CREDENTIAL_URL = re.compile(r"(?i)(https?://[^/\s:@]+:)[^@\s]+(@)")
_KNOWN_TOKEN = re.compile(
    r"(?i)\b(?:AKIA[0-9A-Z]{16}|github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]+|"
    r"xox[baprs]-[A-Za-z0-9-]+|sk-[A-Za-z0-9]{16,})\b"
)


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _sensitive_keys() -> frozenset[str]:
    configured = os.environ.get("GRAYLOG_REDACT_FIELDS", "")
    extra = {_normalized_key(value) for value in configured.split(",") if value.strip()}
    return _DEFAULT_SENSITIVE_KEYS | extra


def _is_sensitive_key(key: str, sensitive_keys: frozenset[str]) -> bool:
    normalized = _normalized_key(key)
    if normalized in sensitive_keys:
        return True
    return normalized.endswith(tuple(sensitive_keys))


def _redact_string(value: str) -> str:
    value = _PRIVATE_KEY.sub(REDACTED, value)
    value = _LABELED_SECRET.sub(lambda match: f"{match.group(1)}{REDACTED}", value)
    value = _CREDENTIAL_URL.sub(rf"\1{REDACTED}\2", value)
    return _KNOWN_TOKEN.sub(REDACTED, value)


def redact(value: Any) -> Any:
    return _redact(value, _sensitive_keys())


def _redact(value: Any, sensitive_keys: frozenset[str]) -> Any:
    if isinstance(value, Mapping):
        return {
            key: REDACTED if isinstance(key, str) and _is_sensitive_key(key, sensitive_keys)
            else _redact(item, sensitive_keys)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, sensitive_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item, sensitive_keys) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value
