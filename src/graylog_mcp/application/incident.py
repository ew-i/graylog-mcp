"""Composite read-only workflow for investigating a Graylog incident."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..domain.errors import GraylogMcpError, error_payload
from ..domain.models import JsonDict
from .alerts import AlertService
from .analytics import AnalyticsService
from .configuration import ConfigurationService
from .operations import OperationsService
from .service import LogService

_TOP_FIELDS = ("source", "service", "level", "http_response_code")

_log = logging.getLogger(__name__)


class IncidentService:
    def __init__(
        self,
        search: LogService,
        analytics: AnalyticsService,
        alerts: AlertService,
        operations: OperationsService,
        configuration: ConfigurationService,
    ) -> None:
        self._search = search
        self._analytics = analytics
        self._alerts = alerts
        self._operations = operations
        self._configuration = configuration

    @staticmethod
    def _capture(action: Callable[[], JsonDict]) -> JsonDict:
        """Run one report section; a failure replaces that section, not the whole report."""
        try:
            return action()
        except Exception as exc:
            if not isinstance(exc, GraylogMcpError):
                _log.exception("incident section failed unexpectedly")
            return error_payload(exc)

    def investigate(
        self,
        *,
        query: str,
        stream_id: str,
        range_seconds: int = 3600,
        baseline_offset_seconds: int = 86400,
        alert_limit: int = 20,
        message_limit: int = 5,
        top_n: int = 10,
        context_field: str = "source",
        context_before: int = 3,
        context_after: int = 3,
        context_window_seconds: int = 300,
    ) -> JsonDict:
        representative = self._capture(
            lambda: self._search.search_recent(
                text=query,
                stream_id=stream_id,
                seconds=range_seconds,
                limit=message_limit,
            )
        )
        reference = next(
            (
                message
                for message in representative.get("messages", [])
                if message.get("_index") and message.get("_id")
            ),
            None,
        )
        context = None
        if reference is not None:
            context = self._capture(
                lambda: self._analytics.message_context(
                    index=reference["_index"],
                    message_id=reference["_id"],
                    stream_id=stream_id,
                    before=context_before,
                    after=context_after,
                    context_field=context_field,
                    window_seconds=context_window_seconds,
                )
            )

        return {
            "query": query,
            "stream_id": stream_id,
            "cluster_health": {
                "status": self._capture(self._search.cluster_info),
                "nodes": self._capture(self._operations.cluster_nodes),
            },
            "recent_alerts": self._capture(
                lambda: self._alerts.recent_alerts(
                    seconds=range_seconds,
                    limit=alert_limit,
                    alerts_only=True,
                )
            ),
            "message_volume": self._capture(
                lambda: self._analytics.compare_windows(
                    text=query,
                    stream_id=stream_id,
                    window_seconds=range_seconds,
                    baseline_offset_seconds=baseline_offset_seconds,
                )
            ),
            "top_values": {
                field: self._capture(
                    lambda field=field: self._analytics.exact_field_counts(
                        text=query,
                        field=field,
                        stream_id=stream_id,
                        seconds=range_seconds,
                        top=top_n,
                    )
                )
                for field in _TOP_FIELDS
            },
            "representative_messages": representative,
            "nearby_context": context,
            "configuration": {
                "stream": self._capture(
                    lambda: self._configuration.stream_rules(stream_id=stream_id)
                ),
                "pipelines": self._capture(
                    lambda: self._configuration.pipeline_rules(stream_id=stream_id)
                ),
            },
        }
