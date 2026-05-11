# Parions Sport Odds MCP Server

[![CI](https://github.com/teefloo/parions-sport-odds-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/teefloo/parions-sport-odds-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-5c5cff.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An MCP server that gives AI agents structured access to current odds from the official Parions Sport Point de Vente website operated by FDJ.

The server uses FDJ's public mobile offer database ZIP as its primary source:

`https://www.pointdevente.parionssport.fdj.fr/service-sport-pointdevente-bff/v1/files/spdv_mobile_offre.sqlite.zip`

It does not bypass DataDome, solve captchas, or call protected betting APIs. Odds are informational and can change quickly; verify official FDJ site or receipt data before relying on them.

## Highlights

- Official public FDJ source: no protected API scraping.
- FastMCP stdio server for Claude Desktop, Cursor, and other MCP clients.
- Structured JSON output for sports, competitions, events, markets, outcomes, odds, source metadata, and cache state.
- Cache-aware refresh with `ETag`, `Last-Modified`, `Cache-Control`, and stale-cache fallback.
- Unit tests plus an opt-in live schema check against the official ZIP.

## Tools

`list_sports()`

Returns sports currently present in the offer database.

`list_competitions(sport?: str | int)`

Returns competitions, optionally filtered by sport name, slug, shortcut, or numeric sport id.

`search_odds(sport?, competition?, event_query?, date_from?, date_to?, market?, limit=20, max_markets_per_event=50)`

Searches current events and returns structured odds. Dates accept `YYYY-MM-DD` or ISO 8601 datetimes. Date-only values are interpreted as UTC calendar dates; `date_to` is exclusive of the following midnight.

`get_event_odds(event_id: int, market?, max_markets=200)`

Returns all available markets and odds for one event id. The optional `market` filter matches market labels, market type names, and lines/handicaps.

## Output Shape

Each tool returns JSON with source/cache metadata and warnings. Odds responses include:

```json
{
  "events": [
    {
      "event_id": 1275790,
      "event_name": "Red Star-Rodez",
      "sport": { "sport_id": 100, "name": "Football", "slug": "football" },
      "competition": { "competition_id": 45452, "name": "L1 McDonald's" },
      "start_time": "2026-05-12T18:30:00Z",
      "betting_closes_at": "2026-05-12T18:25:00Z",
      "markets": [
        {
          "market_id": 33122218,
          "market_name": "1/N/2",
          "market_type_name": "1/N/2",
          "line": null,
          "outcomes": [
            { "label": "1", "team": "Red Star", "odds": 1.82 }
          ]
        }
      ]
    }
  ],
  "source": { "name": "Parions Sport Point de Vente", "operator": "FDJ" },
  "cache": { "stale": false },
  "warnings": []
}
```

If a refresh fails but a previous database exists, the server serves stale data and includes `STALE_CACHE_USED` in `warnings`.

## Installation

Install `uv` if needed, then run:

```bash
uv sync --extra dev
```

Start the server manually:

```bash
uv run parions-sport-mcp
```

Or run it as a module:

```bash
uv run python -m parions_sport_mcp
```

Inspect it during development:

```bash
uv run mcp dev src/parions_sport_mcp/server.py
```

## Claude Desktop

Add this to your Claude Desktop MCP configuration, replacing the path with this repository's absolute path:

```json
{
  "mcpServers": {
    "parions-sport-odds": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/TO/parions-sport-odds-mcp",
        "run",
        "parions-sport-mcp"
      ]
    }
  }
}
```

Restart Claude Desktop after editing the configuration.

## Cursor

Use the same command and args in Cursor's MCP server configuration:

```json
{
  "parions-sport-odds": {
    "command": "uv",
    "args": [
      "--directory",
      "/ABSOLUTE/PATH/TO/parions-sport-odds-mcp",
      "run",
      "parions-sport-mcp"
    ]
  }
}
```

## Configuration

Environment variables:

`PARIONS_SPORT_OFFER_URL`: override the official offer ZIP URL.

`PARIONS_SPORT_CACHE_DIR`: override the cache directory. Defaults to `~/.cache/parions-sport-mcp`.

`PARIONS_SPORT_CACHE_TTL_SECONDS`: fallback cache TTL when response headers do not include `max-age`. Defaults to `120`.

`PARIONS_SPORT_TIMEOUT_SECONDS`: HTTP timeout for ZIP refreshes. Defaults to `20`.

## Tests

Run unit tests:

```bash
uv run --extra dev pytest
```

Run the optional live FDJ ZIP test:

```bash
FDJ_LIVE_TESTS=1 uv run pytest tests/test_live_fdj.py
```

The live test downloads the official ZIP and validates the expected SQLite schema.

## Repository Topics

`mcp`, `model-context-protocol`, `python`, `fastmcp`, `sports-odds`, `fdj`, `parions-sport`, `sqlite`, `claude-desktop`, `cursor`

## License

MIT. See [LICENSE](LICENSE).
