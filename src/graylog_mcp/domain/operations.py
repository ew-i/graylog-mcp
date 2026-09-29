"""Health of the Graylog cluster: nodes, inputs, throughput, notifications."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InputNodeState:
    node_id: str
    state: str  # e.g. RUNNING, FAILED, STOPPED, STARTING
    started_at: str | None
    message: str | None

    @property
    def running(self) -> bool:
        return self.state.upper() == "RUNNING"


@dataclass(frozen=True)
class InputStatus:
    id: str
    title: str | None
    type: str | None
    is_global: bool
    node_id: str | None
    states: tuple[InputNodeState, ...]

    @property
    def health(self) -> str:
        if not self.states:
            return "unknown"
        return "ok" if all(s.running for s in self.states) else "problem"


@dataclass(frozen=True)
class NodeThroughput:
    node_id: str
    metrics: Mapping[str, float | None]  # friendly name -> value


@dataclass(frozen=True)
class SystemNotification:
    type: str | None
    severity: str | None
    timestamp: str | None
    node_id: str | None
    title: str | None
    description: str | None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NodeStatus:
    node_id: str
    reachable: bool
    hostname: str | None = None
    version: str | None = None
    is_leader: bool | None = None
    is_processing: bool | None = None
    lifecycle: str | None = None
    lb_status: str | None = None
    started_at: str | None = None
    last_seen: str | None = None
    transport_address: str | None = None

    @property
    def healthy(self) -> bool:
        return (
            self.reachable
            and self.is_processing is not False
            and (self.lifecycle is None or self.lifecycle.lower() == "running")
        )
