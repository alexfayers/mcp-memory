"""Pins the llm-prompts telemetry helpers that the server relies on."""

from __future__ import annotations

import inspect

import pytest

core = pytest.importorskip("llm_prompts.telemetry_core")

PARAMETERS = {
    "enabled": {},
    "install_id": {},
    "may_send": {"max_age": 86400},
    "send": {"event": inspect.Parameter.empty, "install_id": inspect.Parameter.empty, "module": core.__name__},
    "scrub": {"message": inspect.Parameter.empty, "config": inspect.Parameter.empty},
    "exception_items": {"exc": inspect.Parameter.empty, "config": inspect.Parameter.empty},
    "reportable": {"exc": inspect.Parameter.empty},
}


@pytest.mark.parametrize(("name", "parameters"), PARAMETERS.items())
def test_helper_parameters(name: str, parameters: dict[str, object]) -> None:
    actual = inspect.signature(getattr(core, name)).parameters
    for expected_name, default in parameters.items():
        assert actual[expected_name].default == default
    assert list(actual)[: len(parameters)] == list(parameters)
