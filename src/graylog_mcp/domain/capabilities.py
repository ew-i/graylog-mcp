"""Optional Graylog APIs whose presence depends on the server version."""

from __future__ import annotations

from enum import Enum


class Capability(str, Enum):
    AGGREGATION = "aggregation"  # Search Scripting API, Graylog 5.1+
    TIME_GROUPING = "time_grouping"  # Search Scripting grouped by time; not in 5.1
    FIELD_TYPES = "field_types"
    QUERY_VALIDATION = "query_validation"
    PAGINATED_STREAMS = "paginated_streams"
