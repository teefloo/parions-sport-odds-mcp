# Contributing

Thanks for improving Parions Sport Odds MCP Server.

## Development

Install dependencies:

```bash
uv sync --extra dev
```

Run checks:

```bash
uv run ruff check .
uv run --extra dev pytest
uv build
```

Run the optional live FDJ schema check:

```bash
FDJ_LIVE_TESTS=1 uv run --extra dev pytest tests/test_live_fdj.py
```

## Pull Requests

- Keep changes focused.
- Add or update tests for behavior changes.
- Do not commit caches, virtual environments, build artefacts, local databases, secrets, or `.env` files.
- Do not add code intended to bypass DataDome, captchas, access controls, or protected FDJ APIs.

## Release Notes

Update `CHANGELOG.md` for user-visible changes.
