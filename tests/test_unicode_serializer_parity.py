# SPDX-License-Identifier: Apache-2.0
"""Unicode output parity for native and Python serialization."""

import pytest

import jmd


@pytest.mark.parametrize("key", [
    "Nächsten Forecast anlegen?", "日本語", "Status 😀", 'ä"quoted',
    "é\\path", "ö\tcontrol", "e\u0301", "ä\nline",
])
@pytest.mark.parametrize("position", ["object", "array", "nested"])
def test_unicode_keys_and_quoted_values_match(
    key: str, position: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both backends retain Unicode while still escaping JSON syntax."""
    if not jmd._HAS_CSERIALIZER:
        pytest.skip("C serializer is unavailable")
    value = key + "\t"
    item = {key: value, "after": False}
    payload: object = item
    if position == "array":
        payload = [item]
    elif position == "nested":
        payload = {"data": [item]}
    native = jmd.serialize(payload, label="Probe")
    monkeypatch.setattr(jmd, "_HAS_CSERIALIZER", False)
    python = jmd.serialize(payload, label="Probe")
    assert native == python
    assert jmd.quote_key(key) in native
    assert jmd.JMDParser().parse(native).value == payload
    assert jmd.parse(native).value == payload
