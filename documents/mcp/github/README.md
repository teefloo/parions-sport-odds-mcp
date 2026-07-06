# Parions Sport Odds MCP Server

[![CI](https://github.com/teefloo/parions-sport-odds-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/teefloo/parions-sport-odds-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-5c5cff.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An [MCP](https://modelcontextprotocol.io/) server that gives AI agents structured access to current odds from the official Parions Sport Point de Vente website operated by FDJ, plus finished match results from TheSportsDB.

The server reads FDJ's public mobile offer database, a SQLite ZIP served directly by their own infrastructure:

```
https://www.pointdevente.parionssport.fdj.fr/service-sport-pointdevente-bff/v1/files/spdv_mobile_offre.sqlite.zip
```

> [!IMPORTANT]
> This server does not bypass DataDome, solve captchas, or call protected betting APIs — it only reads FDJ's public offer file. Odds are informational and can change quickly; verify against the official FDJ site or a receipt before relying on them.

## Contents

- [Features](#features)
- [Tools](#tools)
- [Getting started](#getting-started)
- [Using it with an MCP client](#using-it-with-an-mcp-client)
- [Configuration](#configuration)
- [Output shape](#output-shape)
- [Development](#development)

## Features

- **Official public source** — reads FDJ's own offer database, no protected API scraping.
- **Multi-sport results** — finished match results across sports via TheSportsDB, with fuzzy matching back to a Parions Sport event.
- **stdio MCP server** — built with FastMCP, works with Claude Desktop, Cursor, and any other MCP client.
- **Cache-aware refresh** — respects `ETag`, `Last-Modified`, and `Cache-Control`, with stale-cache fallback if a refresh fails.
- **Structured JSON** — every response includes source, cache, and warning metadata alongside the payload.

## Tools

| Tool | Description |
| --- | --- |
| `list_sports()` | Sports currently present in the offer database. |
| `list_competitions(sport?)` | Competitions, optionally filtered by sport name, slug, shortcut, or numeric id. |
| `search_odds(sport?, competition?, event_query?, date_from?, date_to?, market?, limit=20, max_markets_per_event?)` | Search current events and return structured odds. |
| `get_event_odds(event_id, market?, max_markets?)` | All markets and odds for one event id. |
| `get_match_results(sport?, league?, team?, date?, finished_only=true, limit=20)` | Finished (or in-progress) match results across sports via TheSportsDB. |
| `get_event_result(event_id, day_window=1)` | Link a Parions Sport event to its finished result from TheSportsDB. |

### `search_odds`

Dates accept `YYYY-MM-DD` or ISO 8601 datetimes. Date-only values are interpreted as UTC calendar dates; `date_to` is exclusive of the following midnight.

### `get_event_odds`

The optional `market` filter matches market labels, market type names, and lines/handicaps.

### `get_match_results`

Provide at least one of `date` (`YYYY-MM-DD`), `league` (name or numeric id), or `team`. `sport` accepts names such as `football`, `basket`, `tennis`, `rugby`, `hockey`. Set `finished_only=false` to also include scheduled/in-progress fixtures for a date.

```json
{
  "count": 1,
  "results": [
    {
      "match_id": "2052305",
      "sport": "Soccer",
      "league": "French Ligue 1",
      "date": "2026-05-12T18:30:00",
      "status": "Match Finished",
      "home_team": "Red Star",
      "away_team": "Rodez",
      "score": { "home": 2, "away": 1 }
    }
  ],
  "source": { "name": "TheSportsDB" }
}
```

Results come from a different provider than the FDJ odds, so match ids do not map to FDJ `event_id`s; reconcile on team names and date if you need to link a result to its odds.

> [!NOTE]
> On TheSportsDB's free key, league-listing endpoints are capped, so `league` resolves common names through a built-in map (Ligue 1/2, Premier League, La Liga, Serie A, Bundesliga, Champions League, NBA, NFL, NHL, MLB, …) or any numeric league id. A bare `sport` like `basket` mixes leagues (e.g. NBA and WNBA) — pass `league="NBA"` to narrow it.

### `get_event_result`

Reads the event's teams and kickoff date from the FDJ offer, then fuzzy-matches a TheSportsDB result on team names and date. `day_window` widens the date search by N days each side (default 1) to absorb provider timezone differences. Returns the odds event, the matched `result`, an `orientation` (`same`/`swapped` home/away), and a `0..1` `match_confidence`; `found` is `false` below `0.6` and adds `NO_RESULT_MATCH` to `warnings`.

```json
{
  "event_id": 1275790,
  "found": true,
  "odds_event": {
    "event_name": "Red Star-Rodez",
    "teams": { "home": "Red Star", "away": "Rodez" }
  },
  "result": { "home_team": "Red Star FC", "away_team": "Rodez AF", "score": { "home": 2, "away": 1 } },
  "match_confidence": 0.95,
  "orientation": "same"
}
```

> [!WARNING]
> The two providers use different team names. Club names usually align ("Red Star" vs "Red Star FC"), but national teams differ by language — FDJ's French labels ("Turquie", "Allemagne") do not match TheSportsDB's English ones ("Turkey", "Germany"), so international fixtures often fall below the confidence threshold. Linking also only succeeds once the match is finished and present in TheSportsDB.

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+.

```bash
uv sync --extra dev
```

Start the server:

```bash
uv run parions-sport-mcp
```

Or as a module:

```bash
uv run python -m parions_sport_mcp
```

Inspect it during development with the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector):

```bash
uv run mcp dev src/parions_sport_mcp/server.py
```

Smoke-test it from an MCP client session:

```bash
uv run python - <<'PY'
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="uv", args=["run", "parions-sport-mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print([tool.name for tool in (await session.list_tools()).tools])

asyncio.run(main())
PY
```

## Using it with an MCP client

### Claude Desktop

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

### Cursor

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

All configuration is via environment variables; every variable is optional.

| Variable | Default | Description |
| --- | --- | --- |
| `PARIONS_SPORT_OFFER_URL` | official FDJ ZIP URL | Override the offer ZIP source. |
| `PARIONS_SPORT_CACHE_DIR` | `~/.cache/parions-sport-mcp` | Local cache directory for the downloaded database. |
| `PARIONS_SPORT_CACHE_TTL_SECONDS` | `120` | Fallback cache TTL when response headers omit `max-age`. |
| `PARIONS_SPORT_TIMEOUT_SECONDS` | `20` | HTTP timeout for ZIP refreshes. |
| `THESPORTSDB_API_KEY` | `3` (free public test key) | Key for the `get_match_results`/`get_event_result` provider. Set a personal/Patreon key for higher limits and fresher data. |
| `THESPORTSDB_BASE_URL` | `https://www.thesportsdb.com/api/v1/json` | Override the TheSportsDB base URL. |
| `THESPORTSDB_CACHE_TTL_SECONDS` | `300` | In-memory cache TTL for results responses. |
| `THESPORTSDB_TIMEOUT_SECONDS` | `20` | HTTP timeout for results requests. |

## Output shape

Every tool returns JSON with source/cache metadata and warnings. Odds responses look like:

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

## Development

Run unit tests:

```bash
uv run --extra dev pytest
```

Run the optional live FDJ ZIP test, which downloads the official ZIP and validates the expected SQLite schema:

```bash
FDJ_LIVE_TESTS=1 uv run --extra dev pytest tests/test_live_fdj.py
```

Run lint:

```bash
uv run ruff check .
```

## License

MIT. See [LICENSE](LICENSE).
