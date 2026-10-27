"""Pins the llm-prompts telemetry helpers that the server relies on."""

from __future__ import annotations

import inspect

from llm_prompts import telemetry_core as core
import pytest

PARAMETERS = {
    "enabled": {},
    "install_id": {},
    "active_install_id": {},
    "is_set": {"name": inspect.Parameter.empty},
    "may_send": {"max_age": 86400},
    "post": {"body": inspect.Parameter.empty},
    "post_event": {"event": inspect.Parameter.empty, "install_id": inspect.Parameter.empty},
    "send": {"event": inspect.Parameter.empty, "install_id": inspect.Parameter.empty, "module": core.__name__},
    "versions": {"config": inspect.Parameter.empty, "found": None},
    "overlay_versions": {"config": inspect.Parameter.empty},
    "resolve_versions": {"body": inspect.Parameter.empty},
    "resolved_versions": {},
    "context": {"schema": inspect.Parameter.empty},
    "bucket": {"count": inspect.Parameter.empty},
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
