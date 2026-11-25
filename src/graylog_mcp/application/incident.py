"""Composite read-only workflow for investigating a Graylog incident."""

from __future__ import annotations

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor, wait
from typing import TYPE_CHECKING, Any

from ..domain.errors import BackendUnavailableError, GraylogMcpError, error_payload

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..domain.models import JsonDict
    from .alerts import AlertService
    from .analytics import AnalyticsService
    from .configuration import ConfigurationService
    from .operations import OperationsService
    from .service import LogService

_TOP_FIELDS = ("source", "service", "level", "http_response_code")
# The whole report answers within this, even when Graylog is slow: a section still
# running then is reported as unanswered instead of holding up the rest.
DEADLINE_SECONDS = 60.0

_log = logging.getLogger(__name__)


class IncidentService:
    def __init__(
        self,
        search: LogService,
        analytics: AnalyticsService,
        alerts: AlertService,
        operations: OperationsService,
        configuration: ConfigurationService,
        deadline_seconds: float = DEADLINE_SECONDS,
    ) -> None:
        self._search = search
        self._analytics = analytics
        self._alerts = alerts
        self._operations = operations
        self._configuration = configuration
        self._deadline = deadline_seconds

    @staticmethod
    def _capture(action: Callable[[], Any]) -> Any:
        """Run one report section; a failure replaces that section, not the whole report."""
        try:
            return action()
        except Exception as exc:
            if not isinstance(exc, GraylogMcpError):
                _log.exception("incident section failed unexpectedly")
            return error_payload(exc)

    def _run_all(self, sections: dict[str, Callable[[], Any]]) -> dict[str, Any]:
        """Run the sections at once and wait at most the deadline for all of them."""
        pool = ThreadPoolExecutor(max_workers=len(sections), thread_name_prefix="incident")
        futures = {
            name: pool.submit(contextvars.copy_context().run, self._capture, section)
            for name, section in sections.items()
        }
        wait(futures.values(), timeout=self._deadline)
        pool.shutdown(wait=False, cancel_futures=True)
        late = error_payload(
            BackendUnavailableError(f"no answer from Graylog within {self._deadline:g} s")
        )
        return {name: f.result() if f.done() else late for name, f in futures.items()}

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
        def messages_and_context() -> tuple[JsonDict, JsonDict | None]:
            """The context needs a message from the search, so these two run in order."""
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
            if reference is None:
                return representative, None
            return representative, self._capture(
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

        top_values = {
            f"top_values.{field}": (
                lambda field=field: self._analytics.exact_field_counts(
                    text=query, field=field, stream_id=stream_id, seconds=range_seconds, top=top_n
                )
            )
            for field in _TOP_FIELDS
        }
        done = self._run_all(
            {
                "messages": messages_and_context,
                "status": self._search.cluster_info,
                "nodes": self._operations.cluster_nodes,
                "recent_alerts": lambda: self._alerts.recent_alerts(
                    seconds=range_seconds, limit=alert_limit, alerts_only=True
                ),
                "message_volume": lambda: self._analytics.compare_windows(
                    text=query,
                    stream_id=stream_id,
                    window_seconds=range_seconds,
                    baseline_offset_seconds=baseline_offset_seconds,
                ),
                **top_values,
                "stream": lambda: self._configuration.stream_rules(stream_id=stream_id),
                "pipelines": lambda: self._configuration.pipeline_rules(stream_id=stream_id),
            }
        )

        messages = done["messages"]
        representative, context = messages if isinstance(messages, tuple) else (messages, None)
        return {
            "query": query,
            "stream_id": stream_id,
            "cluster_health": {"status": done["status"], "nodes": done["nodes"]},
            "recent_alerts": done["recent_alerts"],
            "message_volume": done["message_volume"],
            "top_values": {field: done[f"top_values.{field}"] for field in _TOP_FIELDS},
            "representative_messages": representative,
            "nearby_context": context,
            "configuration": {"stream": done["stream"], "pipelines": done["pipelines"]},
        }
