"""`ConfigStore` backed by Graylog's stream, pipeline, index-set and lookup APIs."""

from __future__ import annotations

from typing import Any

import httpx

from ..domain.configuration import (
    IndexSet,
    LookupResult,
    LookupTable,
    Pipeline,
    PipelineConnection,
    PipelineRule,
    PipelineStage,
    Strategy,
    StreamConfig,
    StreamRule,
)
from ..domain.models import ListingPage, ListingQuery
from .graylog_api import (
    GraylogApi,
    integer,
    objects,
    page_number,
    segment,
    short_class,
    text,
    total_count,
)


def _strategy(class_name: Any, config: Any) -> Strategy:
    config = config if isinstance(config, dict) else {}
    settings = {k: v for k, v in config.items() if k != "type"}
    return Strategy(name=short_class(class_name or config.get("type")), config=settings)


def _stage_match(stage: dict[str, Any]) -> str | None:
    if stage.get("match") is not None:
        return str(stage["match"])
    if "match_all" in stage:  # older Graylog versions
        return "ALL" if stage["match_all"] else "EITHER"
    return None


class GraylogConfigStore:
    def __init__(self, client: httpx.Client) -> None:
        self._api = GraylogApi(client)

    def stream(self, stream_id: str) -> StreamConfig:
        body = self._api.get_object(f"/api/streams/{segment(stream_id)}")
        return StreamConfig(
            id=str(body.get("id", stream_id)),
            title=text(body.get("title")),
            description=text(body.get("description")) or None,
            matching_type=text(body.get("matching_type")),
            disabled=bool(body.get("disabled", False)),
            index_set_id=text(body.get("index_set_id")),
            rules=tuple(
                StreamRule(
                    field=text(r.get("field")),
                    type_code=integer(r.get("type")),
                    value=text(r.get("value")),
                    inverted=bool(r.get("inverted", False)),
                    description=text(r.get("description")) or None,
                )
                for r in objects(body.get("rules"))
            ),
        )

    def pipelines(self) -> list[Pipeline]:
        return [
            Pipeline(
                id=str(p.get("id")),
                title=text(p.get("title")),
                description=text(p.get("description")) or None,
                stages=tuple(
                    PipelineStage(
                        stage=integer(s.get("stage")) or 0,
                        match=_stage_match(s),
                        rules=tuple(str(r) for r in s.get("rules") or []),
                    )
                    for s in objects(p.get("stages"))
                ),
            )
            for p in objects(self._api.get_list("/api/system/pipelines/pipeline"))
            if p.get("id")
        ]

    def pipeline_connections(self) -> list[PipelineConnection]:
        return [
            PipelineConnection(
                stream_id=str(c.get("stream_id")),
                pipeline_ids=tuple(str(p) for p in c.get("pipeline_ids") or []),
            )
            for c in objects(self._api.get_list("/api/system/pipelines/connections"))
            if c.get("stream_id")
        ]

    def pipeline_rules(self) -> list[PipelineRule]:
        return [
            PipelineRule(
                id=str(r.get("id")),
                title=str(r.get("title")),
                description=text(r.get("description")) or None,
                source=text(r.get("source")),
            )
            for r in objects(self._api.get_list("/api/system/pipelines/rule"))
            if r.get("title")
        ]

    def index_sets(self) -> list[IndexSet]:
        body = self._api.get_object(
            "/api/system/indices/index_sets", params={"skip": 0, "limit": 0, "stats": "false"}
        )
        return [
            IndexSet(
                id=str(s.get("id")),
                title=text(s.get("title")),
                description=text(s.get("description")) or None,
                index_prefix=text(s.get("index_prefix")),
                is_default=bool(s.get("default", False)),
                writable=bool(s.get("writable", True)),
                shards=integer(s.get("shards")),
                replicas=integer(s.get("replicas")),
                rotation=_strategy(s.get("rotation_strategy_class"), s.get("rotation_strategy")),
                retention=_strategy(
                    s.get("retention_strategy_class"), s.get("retention_strategy")
                ),
                data_tiering=s.get("data_tiering")
                if isinstance(s.get("data_tiering"), dict)
                else None,
            )
            for s in objects(body.get("index_sets"))
            if s.get("id")
        ]

    def lookup_tables(self, query: ListingQuery) -> ListingPage:
        params: dict[str, Any] = {
            "page": page_number(query.offset, query.limit),
            "per_page": query.limit,
        }
        if query.text:
            params["query"] = query.text
        body = self._api.get_object("/api/system/lookup/tables", params=params)
        tables = tuple(
            LookupTable(
                id=text(t.get("id")),
                name=str(t.get("name")),
                title=text(t.get("title")),
                description=text(t.get("description")) or None,
            )
            for t in objects(body.get("lookup_tables"))
            if t.get("name")
        )
        total = total_count(body, "total", "total_results", "count")
        return ListingPage(
            total=total if total is not None else len(tables),
            items=tables,
            next_offset=query.offset + query.limit,
        )

    def lookup(self, table: str, key: str) -> LookupResult:
        body = self._api.get_object(
            f"/api/system/lookup/tables/{segment(table)}/query", params={"key": key}
        )
        return LookupResult(
            table=table,
            key=key,
            single_value=body.get("single_value"),
            multi_value=body.get("multi_value"),
            string_list_value=body.get("string_list_value"),
            has_error=bool(body.get("has_error", False)),
            ttl=integer(body.get("ttl")),
        )
