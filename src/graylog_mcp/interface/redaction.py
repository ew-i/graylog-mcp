"""Remove credential-like values before data is returned through MCP."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "[REDACTED]"

# A key containing one of these words is sensitive (`db_password`, `x-api-key`)...
_SENSITIVE_WORDS = (
    "accesskey",
    "accountkey",
    "apikey",
    "clientsecret",
    "credential",
    "passphrase",
    "passwd",
    "password",
    "privatekey",
    "secret",
    "token",
    "webhook",
)
# ...these only at its end, since they also occur in harmless names (`author`, `session_count`).
_SENSITIVE_ENDINGS = (
    "auth",
    "authorization",
    "cookie",
    "pwd",
    "session",
    "sessionid",
    "signature",
)
# Keys that contain a sensitive word but describe the secret instead of holding it.
_SAFE_ENDINGS = ("count", "expiresat", "expiresin", "expiry", "length", "ttl", "type")
_SAFE_KEYS = frozenset(
    {
        "completiontokens",
        "inputtokens",
        "maxtokens",
        "outputtokens",
        "prompttokens",
        "totaltokens",
    }
)

# Where a use case puts the values of the field it was asked about.
_FIELD_VALUE_KEYS = frozenset({"value", "single_value", "multi_value", "string_list_value"})
_FIELD_VALUE_MAPS = frozenset({"counts"})

# Shorter configured secrets would replace ordinary words.
_MIN_LITERAL_SECRET_LENGTH = 8

_SECRET_LABEL = (
    r"(?:access|account|api|private|shared[_ -]?access)[_ -]?key"
    r"|(?:(?:access|auth|bearer|id|refresh|security|session)[_ -]?)?token"
    r"|(?:client[_ -]?)?secret|passphrase|passw(?:or)?d|pwd"
    r"|(?:shared[_ -]?access[_ -]?)?signature"
)
_NOT_YET_REDACTED = r"(?!\[REDACTED)"
_LABELLED_SECRET = re.compile(
    rf"(?i)(?<![a-z0-9])(?P<label>(?:{_SECRET_LABEL})[\"']?\s*[:=]\s*){_NOT_YET_REDACTED}"
    r"(?P<value>\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^\s,;&\"'}\]]+)"
)
_AUTHORIZATION = re.compile(
    r"(?i)(?<![a-z0-9])(authorization[\"']?\s*[:=]\s*[\"']?(?:[a-z][\w-]*\s+)?)"
    rf"{_NOT_YET_REDACTED}[^\s\"',;]+"
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)[\w.~+/-]{16,}=*")
_JWT = re.compile(r"\beyJ[\w-]{4,}\.eyJ[\w-]{4,}\.[\w-]*")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY[A-Z ]*-----.*?"
    r"(?:-----END [A-Z0-9 ]*PRIVATE KEY[A-Z ]*-----|\Z)",
    re.DOTALL,
)
_CREDENTIAL_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]*:)[^@\s/]+(@)")
_WEBHOOK_URL = re.compile(
    r"(?i)((?:hooks\.slack\.com/(?:services|workflows|triggers)"
    r"|discord(?:app)?\.com/api/webhooks"
    r"|(?:[\w-]+\.)?webhook\.office\.com/webhookb2|outlook\.office(?:365)?\.com/webhook)/)"
    r"[\w@/-]+"
)
_SAS_SIGNATURE = re.compile(r"(?i)([?&]sig=)[^&\s#\"']+")
_KNOWN_TOKEN = re.compile(
    r"\b(?:(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])|AIza[\w-]{35}|github_pat_\w+|gh[pousr]_\w+"
    r"|glpat-[\w-]{20,}|xox[abeprs]-[\w-]+|[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|sk-[\w-]{16,}|npm_[A-Za-z0-9]{36}|pypi-[\w-]{50,})"
)


def _normalized(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _is_flag(value: Any) -> bool:
    return value is None or isinstance(value, bool)


def _redact_labelled(match: re.Match[str]) -> str:
    value = match["value"]
    quote = value[0] if value[0] in "\"'" else ""
    return f"{match['label']}{quote}{REDACTED}{quote}"


class Redactor:
    """Redacts credentials by key name, by known value shapes, and by exact value.

    `fields` are extra key names, matched exactly after dropping case and
    punctuation; `secrets` are literal values, such as the server's own API
    token, replaced wherever they appear.
    """

    def __init__(self, fields: Iterable[str] = (), secrets: Iterable[str] = ()) -> None:
        self._fields = frozenset(_normalized(name) for name in fields if name.strip())
        literals = sorted(
            {value for value in secrets if len(value) >= _MIN_LITERAL_SECRET_LENGTH},
            key=len,
            reverse=True,
        )
        self._literals = re.compile("|".join(map(re.escape, literals))) if literals else None

    def is_sensitive_field(self, name: str) -> bool:
        key = _normalized(name)
        if key in self._fields:
            return True
        if key in _SAFE_KEYS or key.endswith(_SAFE_ENDINGS):
            return False
        return key.endswith(_SENSITIVE_ENDINGS) or any(word in key for word in _SENSITIVE_WORDS)

    def redact(self, value: Any, fields: Iterable[str] = ()) -> Any:
        """Redact `value`; when one of `fields` (the fields a tool reported on) is
        sensitive, its values are first replaced by numbered placeholders, so counts
        and repeats stay visible without the values themselves."""
        if any(self.is_sensitive_field(name) for name in fields):
            value = _mask_field_values(value)
        return self._redact(value)

    def _redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return self._redact_mapping(value)
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact(item) for item in value)
        if isinstance(value, str):
            return self._redact_string(value)
        return value

    def _redact_mapping(self, value: Mapping[Any, Any]) -> dict[Any, Any]:
        # message_context reports the anchor's value of `context_field` as `context_value`.
        context_field = value.get("context_field")
        context_secret = isinstance(context_field, str) and self.is_sensitive_field(context_field)
        return {
            self._redact_key(key): self._redact_entry(
                key, item, context_secret and key == "context_value"
            )
            for key, item in value.items()
        }

    def _redact_key(self, key: Any) -> Any:
        # Keys can be values too, such as the per-value counts of count_over_time.
        return self._redact_string(key) if isinstance(key, str) else key

    def _redact_entry(self, key: Any, item: Any, sensitive: bool) -> Any:
        sensitive = sensitive or (isinstance(key, str) and self.is_sensitive_field(key))
        # A flag or a missing value says nothing about the secret itself.
        if sensitive and not _is_flag(item):
            return REDACTED
        return self._redact(item)

    def _redact_string(self, value: str) -> str:
        if self._literals:
            value = self._literals.sub(REDACTED, value)
        value = _PRIVATE_KEY.sub(REDACTED, value)
        value = _CREDENTIAL_URL.sub(rf"\1{REDACTED}\2", value)
        value = _WEBHOOK_URL.sub(rf"\1{REDACTED}", value)
        value = _SAS_SIGNATURE.sub(rf"\1{REDACTED}", value)
        value = _AUTHORIZATION.sub(rf"\1{REDACTED}", value)
        value = _LABELLED_SECRET.sub(_redact_labelled, value)
        value = _BEARER.sub(rf"\1{REDACTED}", value)
        value = _JWT.sub(REDACTED, value)
        return _KNOWN_TOKEN.sub(REDACTED, value)


def _mask_field_values(payload: Any) -> Any:
    placeholders: dict[str, str] = {}

    def mask(value: Any) -> Any:
        if value is None:
            return None
        identity = json.dumps(value, sort_keys=True, default=str)
        return placeholders.setdefault(identity, f"[REDACTED-{len(placeholders) + 1}]")

    def entry(key: Any, item: Any) -> Any:
        if key in _FIELD_VALUE_KEYS:
            return mask(item)
        if key in _FIELD_VALUE_MAPS and isinstance(item, Mapping):
            return {mask(value): count for value, count in item.items()}
        return walk(item)

    def walk(node: Any) -> Any:
        if isinstance(node, Mapping):
            return {key: entry(key, item) for key, item in node.items()}
        if isinstance(node, (list, tuple)):
            return [walk(item) for item in node]
        return node

    return walk(payload)
