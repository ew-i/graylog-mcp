"""Composition root: the only place where concrete pieces are wired together."""

from __future__ import annotations

import os
import sys

import httpx

from .application.alerts import AlertService
from .application.analytics import AnalyticsService
from .application.configuration import ConfigurationService
from .application.operations import OperationsService
from .application.saved_views import SavedViewService
from .application.service import LogService
from .infrastructure.config import ConfigError, Settings
from .infrastructure.graylog_alerts import GraylogAlertStore
from .infrastructure.graylog_analytics import GraylogAnalyticsStore
from .infrastructure.graylog_api import build_http_client
from .infrastructure.graylog_config import GraylogConfigStore
from .infrastructure.graylog_http import GraylogStore
from .infrastructure.graylog_system import GraylogSystemStore
from .infrastructure.graylog_views import GraylogViewStore
from .interface.mcp_tools import ServiceBundle, build_server


def wire(http: httpx.Client) -> ServiceBundle:
    logs = GraylogStore(http)
    search = LogService(logs)
    return ServiceBundle(
        search=search,
        analytics=AnalyticsService(logs, GraylogAnalyticsStore(http)),
        alerts=AlertService(GraylogAlertStore(http)),
        views=SavedViewService(GraylogViewStore(http), search),
        operations=OperationsService(GraylogSystemStore(http)),
        configuration=ConfigurationService(GraylogConfigStore(http)),
    )


def main() -> int:
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional dependency
        pass
    else:
        load_dotenv()

    try:
        settings = Settings.from_env(os.environ)
    except ConfigError as exc:
        print(f"graylog-mcp: {exc}", file=sys.stderr)
        return 1

    with build_http_client(settings) as http:
        build_server(wire(http)).run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
