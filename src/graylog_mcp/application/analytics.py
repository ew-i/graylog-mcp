"""Analysis use cases: counts, histograms, comparisons, statistics, context."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain.aggregation import (
    AggregationQuery,
    Metric,
    TimeGrouping,
    ValuesGrouping,
    auto_interval,
    interval_seconds,
    require_field_name,
)
from ..domain.errors import BackendError, InvalidRequestError
from ..domain.models import Between, JsonDict, LastSeconds, LogEntry, LogQuery, Sort, SortDirection
from ..domain.timeutil import format_timestamp, parse_timestamp
from .policy import Limits, clamp, parse_field_list, parse_percentiles
from .ports import AggregationStore, LogStore

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _lucene_phrase(value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


class AnalyticsService:
    def __init__(
        self,
        logs: LogStore,
        aggregations: AggregationStore,
        limits: Limits | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._logs = logs
        self._agg = aggregations
        self._limits = limits or Limits()
        self._clock = clock

    def _recent(self, seconds: int) -> LastSeconds:
        return LastSeconds(clamp(seconds, 1, self._limits.max_window_seconds))

    # -- count_matches -------------------------------------------------------

    def count_matches(self, *, text: str, stream_id: str, seconds: int) -> JsonDict:
        window = self._recent(seconds)
        page = self._logs.search(
            LogQuery(text=text, stream_id=stream_id, window=window, max_results=1)
        )
        return {"query": text, "window": window.describe(), "total_results": page.total_hits}

    # -- count_over_time -----------------------------------------------------

    def count_over_time(
        self,
        *,
        text: str,
        stream_id: str,
        seconds: int,
        interval: str = "",
        split_by: str = "",
        split_limit: int = 5,
    ) -> JsonDict:
        window = self._recent(seconds)
        interval = interval.strip() or auto_interval(window.seconds, self._limits.default_buckets)
        buckets = -(-window.seconds // interval_seconds(interval))  # ceil
        if buckets > self._limits.max_buckets:
            raise InvalidRequestError(
                f"interval {interval} would produce {buckets} buckets "
                f"(max {self._limits.max_buckets}); use a larger interval or a shorter range"
            )
        groups: list = [TimeGrouping(interval)]
        split = split_by.strip()
        if split:
            groups.append(ValuesGrouping(split, clamp(split_limit, 1, 20)))
        table = self._agg.aggregate(
            AggregationQuery(text, stream_id, window, tuple(groups), (Metric("count"),))
        )

        series: dict[str, Any] = {}
        for row in table.rows:
            time_key = row.keys[0] or "unknown"
            count = int(row.values.get("count") or 0)
            if split:
                series.setdefault(time_key, {})[row.keys[1] or "(missing)"] = count
            else:
                series[time_key] = series.get(time_key, 0) + count

        ordered = sorted(series.items())
        if split:
            points = [{"time": t, "total": sum(c.values()), "counts": c} for t, c in ordered]
        else:
            points = [{"time": t, "count": c} for t, c in ordered]
        totals = [p["total"] if split else p["count"] for p in points]
        peak = max(points, key=lambda p: p["total"] if split else p["count"]) if points else None
        return {
            "query": text,
            "window": window.describe(),
            "interval": interval,
            "split_by": split or None,
            "total": sum(totals),
            "peak": {"time": peak["time"], "count": peak["total"] if split else peak["count"]}
            if peak
            else None,
            "buckets": points,
            "note": "Buckets with no messages are omitted." if points else None,
        }

    # -- exact_field_counts --------------------------------------------------

    def exact_field_counts(
        self, *, text: str, stream_id: str, field: str, seconds: int, top: int
    ) -> JsonDict:
        window = self._recent(seconds)
        name = require_field_name(field)
        table = self._agg.aggregate(
            AggregationQuery(
                text,
                stream_id,
                window,
                (ValuesGrouping(name, clamp(top, 1, self._limits.max_top_values)),),
                (Metric("count", descending=True),),
            )
        )
        values = sorted(
            ({"value": r.keys[0], "count": int(r.values.get("count") or 0)} for r in table.rows),
            key=lambda v: -v["count"],
        )
        return {"query": text, "field": name, "window": window.describe(), "top_values": values}

    # -- compare_windows -----------------------------------------------------

    def compare_windows(
        self,
        *,
        text: str,
        stream_id: str,
        window_seconds: int,
        baseline_offset_seconds: int = 86400,
        field: str = "",
        top: int = 10,
    ) -> JsonDict:
        length = clamp(window_seconds, 60, self._limits.max_window_seconds)
        offset = clamp(baseline_offset_seconds, 1, self._limits.max_window_seconds)
        if offset < length:
            raise InvalidRequestError(
                "baseline_offset_seconds must be at least window_seconds "
                "so the windows do not overlap"
            )

        now = self._clock()
        current = Between(format_timestamp(now - timedelta(seconds=length)), format_timestamp(now))
        base_end = now - timedelta(seconds=offset)
        baseline = Between(
            format_timestamp(base_end - timedelta(seconds=length)), format_timestamp(base_end)
        )

        def total(window) -> int:
            return self._logs.search(
                LogQuery(text=text, stream_id=stream_id, window=window, max_results=1)
            ).total_hits

        cur_total, base_total = total(current), total(baseline)
        result: dict[str, Any] = {
            "query": text,
            "current_window": current.describe(),
            "baseline_window": baseline.describe(),
            "totals": self._change(cur_total, base_total),
        }

        name = field.strip()
        if name:
            name = require_field_name(name)

            def counts(window) -> dict[str, int]:
                table = self._agg.aggregate(
                    AggregationQuery(
                        text,
                        stream_id,
                        window,
                        (ValuesGrouping(name, self._limits.compare_candidates),),
                        (Metric("count", descending=True),),
                    )
                )
                return {str(r.keys[0]): int(r.values.get("count") or 0) for r in table.rows}

            cur, base = counts(current), counts(baseline)
            changes = [
                {"value": value, **self._change(cur.get(value, 0), base.get(value, 0))}
                for value in dict.fromkeys([*cur, *base])
            ]
            changes.sort(key=lambda c: (-abs(c["change"]), c["value"]))
            result["field"] = name
            result["biggest_changes"] = changes[: clamp(top, 1, self._limits.max_top_values)]
            result["note"] = (
                f"Per-value counts consider the top {self._limits.compare_candidates} "
                "values of each window."
            )
        return result

    @staticmethod
    def _change(current: int, baseline: int) -> JsonDict:
        if baseline == 0:
            status = "new" if current else "unchanged"
            ratio = None
        else:
            ratio = round(current / baseline, 3)
            status = (
                "gone"
                if current == 0
                else (
                    "up" if current > baseline else "down" if current < baseline else "unchanged"
                )
            )
        return {
            "current": current,
            "baseline": baseline,
            "change": current - baseline,
            "ratio": ratio,
            "status": status,
        }

    # -- field_stats ---------------------------------------------------------

    def field_stats(
        self,
        *,
        text: str,
        stream_id: str,
        field: str,
        seconds: int,
        percentiles: str = "50,90,99",
        group_by: str = "",
        group_limit: int = 10,
    ) -> JsonDict:
        window = self._recent(seconds)
        name = require_field_name(field)
        metrics = [
            Metric("count", name, descending=True),
            Metric("avg", name),
            Metric("min", name),
            Metric("max", name),
            Metric("sum", name),
            *(Metric("percentile", name, percentile=p) for p in parse_percentiles(percentiles)),
        ]
        group = group_by.strip()
        groups = (
            (ValuesGrouping(group, clamp(group_limit, 1, self._limits.max_top_values)),)
            if group
            else ()
        )
        table = self._agg.aggregate(
            AggregationQuery(text, stream_id, window, groups, tuple(metrics))
        )

        def stats(values) -> JsonDict:
            out = {m.label.split("(")[0]: values.get(m.label) for m in metrics}
            out["count"] = int(out.get("count") or 0)
            return out

        result: dict[str, Any] = {"query": text, "field": name, "window": window.describe()}
        if group:
            result["group_by"] = group
            result["groups"] = [{"value": r.keys[0], "stats": stats(r.values)} for r in table.rows]
        else:
            result["stats"] = stats(table.rows[0].values) if table.rows else stats({})
        return result

    # -- message_context -----------------------------------------------------

    def message_context(
        self,
        *,
        index: str,
        message_id: str,
        stream_id: str,
        before: int = 5,
        after: int = 5,
        context_field: str = "source",
        window_seconds: int = 300,
        fields: str = "",
    ) -> JsonDict:
        if not index.strip() or not message_id.strip():
            raise InvalidRequestError("both index and message_id are required")
        if not stream_id.strip():
            raise InvalidRequestError("a stream_id is required; call browse_streams to find one")
        before = clamp(before, 0, self._limits.max_context)
        after = clamp(after, 0, self._limits.max_context)
        span = timedelta(seconds=clamp(window_seconds, 1, self._limits.max_context_window_seconds))
        only = parse_field_list(fields)

        anchor = self._logs.fetch(index.strip(), message_id.strip())
        raw_ts = anchor.fields.get("timestamp")
        if raw_ts is None:
            raise BackendError("the message has no timestamp, so its neighbours cannot be located")
        moment = parse_timestamp(str(raw_ts))

        scope_field: str | None = (
            require_field_name(context_field) if context_field.strip() else None
        )
        scope_value = anchor.fields.get(scope_field) if scope_field else None
        query_text = (
            f"{scope_field}:{_lucene_phrase(scope_value)}" if scope_value is not None else "*"
        )

        def neighbours(
            start: datetime, end: datetime, direction: SortDirection, count: int
        ) -> list[LogEntry]:
            if count == 0:
                return []
            page = self._logs.search(
                LogQuery(
                    text=query_text,
                    stream_id=stream_id,
                    window=Between(format_timestamp(start), format_timestamp(end)),
                    max_results=count
                    + 10,  # headroom for the anchor and same-timestamp duplicates
                    sort=Sort("timestamp", direction),
                )
            )
            return list(page.entries)

        anchor_id = anchor.fields.get("_id", message_id.strip())
        seen = {anchor_id}

        def take(entries: list[LogEntry], count: int) -> list[LogEntry]:
            picked = []
            for entry in entries:
                entry_id = entry.fields.get("_id")
                if entry_id in seen:
                    continue
                if entry_id is not None:
                    seen.add(entry_id)
                picked.append(entry)
                if len(picked) == count:
                    break
            return picked

        earlier = take(neighbours(moment - span, moment, SortDirection.DESC, before), before)
        later = take(neighbours(moment, moment + span, SortDirection.ASC, after), after)
        earlier.reverse()  # chronological

        return {
            "anchor": anchor.visible(only),
            "anchor_time": format_timestamp(moment),
            "context_field": scope_field if scope_value is not None else None,
            "context_value": scope_value,
            "before": [e.visible(only) for e in earlier],
            "after": [e.visible(only) for e in later],
            "note": None
            if scope_value is not None or not scope_field
            else f"The message has no {scope_field!r} field, "
            "so neighbours come from the whole stream.",
        }
