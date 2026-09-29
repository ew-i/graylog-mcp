"""Configuration inspection use cases (read-only)."""

from __future__ import annotations

from dataclasses import asdict

from ..domain.errors import InvalidRequestError
from ..domain.models import JsonDict
from .policy import Limits, clamp
from .ports import ConfigStore


def _require(value: str, name: str) -> str:
    value = value.strip()
    if not value:
        raise InvalidRequestError(f"{name} is required")
    return value


class ConfigurationService:
    def __init__(self, store: ConfigStore, limits: Limits | None = None) -> None:
        self._store = store
        self._limits = limits or Limits()

    def stream_rules(self, *, stream_id: str) -> JsonDict:
        stream = self._store.stream(_require(stream_id, "stream_id"))
        combine = "all" if (stream.matching_type or "AND").upper() == "AND" else "at least one"
        return {
            "stream_id": stream.id,
            "title": stream.title,
            "description": stream.description,
            "disabled": stream.disabled,
            "index_set_id": stream.index_set_id,
            "matching_type": stream.matching_type,
            "summary": (
                f"A message is routed here when it matches {combine} "
                f"of the {len(stream.rules)} rule(s)."
            ),
            "rules": [
                {
                    "field": r.field,
                    "type": r.type_name,
                    "value": r.value,
                    "inverted": r.inverted,
                    "description": r.description,
                    "meaning": r.meaning(),
                }
                for r in stream.rules
            ],
        }

    def pipeline_rules(self, *, stream_id: str = "", include_source: bool = True) -> JsonDict:
        pipelines = {p.id: p for p in self._store.pipelines()}
        connections = self._store.pipeline_connections()
        rules = {r.title: r for r in self._store.pipeline_rules()}

        wanted = stream_id.strip()
        if wanted:
            ids = [pid for c in connections if c.stream_id == wanted for pid in c.pipeline_ids]
        else:
            ids = list(pipelines)
        streams_for: dict[str, list[str]] = {}
        for c in connections:
            for pid in c.pipeline_ids:
                streams_for.setdefault(pid, []).append(c.stream_id)

        out = []
        for pid in dict.fromkeys(ids):
            pipeline = pipelines.get(pid)
            if pipeline is None:
                out.append({"id": pid, "missing": True})
                continue
            stages = []
            for stage in sorted(pipeline.stages, key=lambda s: s.stage):
                stage_rules = []
                for title in stage.rules:
                    rule = rules.get(title)
                    entry = {"title": title, "found": rule is not None}
                    if rule is not None:
                        entry["description"] = rule.description
                        if include_source:
                            entry["source"] = rule.source
                    stage_rules.append(entry)
                stages.append({"stage": stage.stage, "match": stage.match, "rules": stage_rules})
            out.append(
                {
                    "id": pipeline.id,
                    "title": pipeline.title,
                    "description": pipeline.description,
                    "connected_streams": streams_for.get(pipeline.id, []),
                    "stages": stages,
                }
            )
        return {"stream_id": wanted or None, "total": len(out), "pipelines": out}

    def index_sets(self, *, stream_id: str = "") -> JsonDict:
        used_by_stream = (
            self._store.stream(stream_id.strip()).index_set_id if stream_id.strip() else None
        )
        sets = self._store.index_sets()
        rows = [
            {**asdict(s), "used_by_stream": used_by_stream == s.id if used_by_stream else None}
            for s in sets
        ]
        if used_by_stream:
            rows.sort(key=lambda r: not r["used_by_stream"])
        return {"stream_id": stream_id.strip() or None, "total": len(rows), "index_sets": rows}

    def lookup_tables(self, *, text: str = "", limit: int = 50) -> JsonDict:
        tables = self._store.lookup_tables(text.strip(), clamp(limit, 1, self._limits.max_listing))
        return {"total": len(tables), "lookup_tables": [asdict(t) for t in tables]}

    def lookup_value(self, *, table: str, key: str) -> JsonDict:
        result = self._store.lookup(_require(table, "table"), _require(key, "key"))
        return asdict(result)
