"""Domain errors surfaced by the MCP tools."""


class ParionsSportError(RuntimeError):
    """Base class for recoverable Parions Sport MCP failures."""


class SourceUnavailableError(ParionsSportError):
    """The official FDJ source could not be reached or returned an error."""


class RateLimitedError(SourceUnavailableError):
    """The official FDJ source rejected or rate-limited the request."""


class InvalidSourceDataError(ParionsSportError):
    """The downloaded FDJ payload is not a valid offer database."""


class SchemaDriftError(InvalidSourceDataError):
    """The SQLite schema no longer matches the expected FDJ offer layout."""


class InputValidationError(ParionsSportError):
    """A tool input failed validation."""
