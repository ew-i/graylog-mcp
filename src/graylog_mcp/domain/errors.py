"""Error hierarchy shared by every layer.

Only these types cross layer boundaries; transport-specific exceptions
(httpx, JSON decoding, ...) are translated at the infrastructure edge.
"""


class GraylogMcpError(Exception):
    """Base class for every error this package raises on purpose."""

    @property
    def kind(self) -> str:
        """Stable category shown to clients, e.g. ``InvalidRequest`` for InvalidRequestError."""
        name = type(self).__name__
        return name[: -len("Error")] if name.endswith("Error") and name != "Error" else name


class InvalidRequestError(GraylogMcpError):
    """The caller supplied arguments that can never succeed."""


class BackendError(GraylogMcpError):
    """The log backend failed to answer the request."""


class AccessDeniedError(BackendError):
    """Credentials were rejected or lack permission for the resource."""


class NotFoundError(BackendError):
    """The requested resource does not exist."""


class BackendUnavailableError(BackendError):
    """The backend could not be reached at all (DNS, TCP, TLS, timeout)."""


class UnsupportedError(BackendError):
    """The backend version does not offer the requested capability."""


def error_payload(exc: Exception) -> dict[str, str]:
    """The {"error", "kind"} payload clients see; unexpected exceptions get kind ``Internal``."""
    if isinstance(exc, GraylogMcpError):
        return {"error": str(exc), "kind": exc.kind}
    return {"error": f"Unexpected {type(exc).__name__}: {exc}", "kind": "Internal"}
