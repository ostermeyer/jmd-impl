"""JMD scalar parsing, serialization, and inline field helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Local binding for hot-path performance (avoids module attribute lookup)
_json_loads = json.loads

# Section 5a: structural prefixes that must never appear unquoted in a value.
_STRUCTURAL_PREFIXES = ("# ", "- ")


def parse_scalar(raw: str) -> Any:
    """Parse a raw scalar string into a Python value.

    Applies JMD disambiguation rules: null, true, false, number, string.
    Raises ValueError for unquoted structural prefixes.

    Args:
        raw: Raw value text (should already be stripped by caller).

    Returns:
        Parsed Python value (None, bool, int, float, or str).

    Raises:
        ValueError: If the value contains an unquoted structural prefix.
    """
    # Fast path: check first character to quickly dispatch
    c0 = raw[0] if raw else '\0'

    if c0 == '"':
        if raw[-1] == '"' and len(raw) >= 2:
            return _json_loads(raw)
        return raw

    if c0 == 'n' and raw == "null":
        return None
    if c0 == 't' and raw == "true":
        return True
    if c0 == 'f' and raw == "false":
        return False

    # Number detection: starts with digit or '-' followed by digit
    if c0.isdigit() or (c0 == '-' and len(raw) > 1 and raw[1].isdigit()):
        try:
            if '.' in raw or 'e' in raw or 'E' in raw:
                return float(raw)
            return int(raw)
        except ValueError:
            pass
    elif c0 == '-':
        if len(raw) == 1:
            raise ValueError(
                "Bare '-' as value is ambiguous. Quote the value: \"-\""
            )

    if c0 == '#' or (c0 == '-' and len(raw) > 1 and raw[1] == ' '):
        for prefix in _STRUCTURAL_PREFIXES:
            if raw.startswith(prefix):
                raise ValueError(
                    f"Structural prefix {prefix!r} in unquoted string {raw!r}. "
                    f'Quote the value: "{raw}"'
                )
    return raw


def parse_key(raw: str) -> str:
    """Parse a key string, stripping quotes if present.

    Args:
        raw: Raw key text, possibly JSON-quoted (should already be stripped
            by caller).

    Returns:
        Unquoted key string.
    """
    if raw[0] == '"':
        if raw[-1] == '"' and len(raw) >= 2:
            return str(_json_loads(raw))
    return raw


def _needs_quote(s: str) -> bool:
    """Check whether a string value needs quoting.

    Args:
        s: String value to check.

    Returns:
        True if the value must be quoted for unambiguous parsing.
    """
    if s == "":
        # A bare empty string would be indistinguishable from a blockquote
        # field start (`key:`); emit `""` to preserve the empty-string
        # value through round-trip.  The C accelerator already does this.
        return True
    if s == "-":
        return True
    if s in ("null", "true", "false"):
        return True
    try:
        float(s)
        return True
    except ValueError:
        pass
    for prefix in _STRUCTURAL_PREFIXES:
        if s.startswith(prefix):
            return True
    return False


def serialize_scalar(value: Any) -> str:
    """Serialize a Python value to its JMD scalar representation.

    Args:
        value: A Python value (None, bool, int, float, or str).

    Returns:
        JMD scalar text, quoted if necessary.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "sha256:" + hashlib.sha256(value).hexdigest()
    s = str(value)
    if _needs_quote(s) or s.startswith('"') or "\n" in s or "\t" in s:
        return json.dumps(s, ensure_ascii=False)
    return s


def quote_key(key: str) -> str:
    """Quote a key if it contains special characters.

    Args:
        key: Key string.

    Returns:
        Bare key or JSON-quoted key.
    """
    if re.fullmatch(r"[a-zA-Z0-9_\-]+", key):
        return key
    return json.dumps(key, ensure_ascii=False)
