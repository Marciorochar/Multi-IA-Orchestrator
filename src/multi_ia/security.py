"""Best-effort redaction for structured diagnostics, not a secret vault."""
from __future__ import annotations

import re
from typing import Any

_KEYS = frozenset({
    "token", "accesstoken", "refreshtoken", "sessiontoken", "idtoken",
    "password", "passwd", "secret", "clientsecret", "apikey",
    "authorization", "proxyauthorization", "credential", "credentials",
    "privatekey", "secretkey", "cookie", "setcookie",
})
_LABEL = (
    r"access[-_ ]?token|refresh[-_ ]?token|session[-_ ]?token|id[-_ ]?token|"
    r"client[-_ ]?secret|api[-_ ]?key|proxy[-_ ]?authorization|"
    r"private[-_ ]?key|secret[-_ ]?key|password|passwd|authorization|"
    r"credentials?|secret|token|set[-_ ]?cookie|cookie"
)
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:" + _LABEL + r")\b[\"']?\s*[:=]\s*)"
    r"(?:[\"'][^\"'\r\n]*[\"']|[^\s,;]+)"
)
_AUTH = re.compile(r"(?i)\b(?:bearer|basic)[ \t]+[A-Za-z0-9._~+/\-=]+")
_URL_AUTH = re.compile(r"(?i)(https?://)[^/\s@]+@")
_PROVIDER_TOKEN = re.compile(r"\b(?:sk-(?:proj-)?|ghp_|github_pat_|gho_)[A-Za-z0-9_-]{16,}")

def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if re.sub(r"[^a-z0-9]", "", str(key).casefold()) in _KEYS
                  else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _AUTH.sub("<redacted>", value)
        value = _ASSIGNMENT.sub(lambda match: match.group(1) + "<redacted>", value)
        value = _URL_AUTH.sub(r"\1<redacted>@", value)
        return _PROVIDER_TOKEN.sub("<redacted>", value)
    return value
