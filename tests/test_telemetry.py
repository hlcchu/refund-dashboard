import sys
import types

import pytest
from fastapi import FastAPI

from app import telemetry


@pytest.fixture
def fake_azure_monitor(monkeypatch):
    calls: list[dict] = []
    module = types.ModuleType("azure.monitor.opentelemetry")
    module.configure_azure_monitor = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", module)
    monkeypatch.setattr(telemetry, "_exporter_configured", False)
    return calls


def test_disabled_without_connection_string(monkeypatch, fake_azure_monitor):
    monkeypatch.delenv(telemetry.CONNECTION_STRING_ENV, raising=False)
    app = FastAPI()
    assert telemetry.instrument_app(app) is False
    assert fake_azure_monitor == []
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)


def test_enabled_with_connection_string(monkeypatch, fake_azure_monitor):
    monkeypatch.setenv(telemetry.CONNECTION_STRING_ENV, "InstrumentationKey=00000000-0000")
    first, second = FastAPI(), FastAPI()
    assert telemetry.instrument_app(first) is True
    assert telemetry.instrument_app(second) is True
    assert len(fake_azure_monitor) == 1, "exporter is configured once per process"
    assert fake_azure_monitor[0]["logger_name"] == "app"
    assert first._is_instrumented_by_opentelemetry and second._is_instrumented_by_opentelemetry
