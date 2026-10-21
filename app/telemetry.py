"""Azure Monitor (Application Insights) telemetry, enabled only when a connection string is set."""

import os

from fastapi import FastAPI

CONNECTION_STRING_ENV = "APPLICATIONINSIGHTS_CONNECTION_STRING"
# Health probes fire every few seconds; tracing them would dominate ingestion cost.
EXCLUDED_URLS = "healthz"

_exporter_configured = False


def instrument_app(app: FastAPI) -> bool:
    global _exporter_configured
    if not os.environ.get(CONNECTION_STRING_ENV):
        return False
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    if not _exporter_configured:
        # Logs are collected from the app's own loggers only ("app.*"). FastAPI is instrumented
        # per app instance below, because create_app() builds the app after this module loads.
        configure_azure_monitor(
            logger_name="app", instrumentation_options={"fastapi": {"enabled": False}}
        )
        _exporter_configured = True
    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)
    return True
