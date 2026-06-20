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

`get_match_results(sport?, league?, team?, date?, finished_only=true, limit=20)`

Returns finished match results (teams, score, date, competition) across sports via [TheSportsDB](https://www.thesportsdb.com/). Provide at least one of `date` (`YYYY-MM-DD`), `league` (name or numeric id), or `team`. `sport` accepts names such as `football`, `basket`, `tennis`, `rugby`, `hockey`. Set `finished_only=false` to also include scheduled/in-progress fixtures for a date.

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

Notes: on TheSportsDB's free key, league-listing endpoints are capped, so `league` resolves common names via a built-in map (Ligue 1/2, Premier League, La Liga, Serie A, Bundesliga, Champions League, NBA, NFL, NHL, MLB, …) or any numeric league id. A bare `sport` like `basket` mixes leagues (e.g. NBA and WNBA) — pass `league="NBA"` to narrow it.

`get_event_result(event_id: int, day_window=1)`

Links a Parions Sport event to its finished result. It reads the event's teams and kickoff date from the FDJ offer, then fuzzy-matches a TheSportsDB result on team names and date. `day_window` widens the date search by N days each side (default 1) to absorb provider timezone differences. Returns the odds event, the matched `result`, an `orientation` (`same`/`swapped` home/away), and a `0..1` `match_confidence`; `found` is `false` below `0.6` and adds `NO_RESULT_MATCH` to `warnings`.

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

Limits: the two providers use different team names. Club names usually align ("Red Star" vs "Red Star FC"), but national teams differ by language — FDJ's French labels ("Turquie", "Allemagne") do not match TheSportsDB's English ones ("Turkey", "Germany"), so international fixtures often fall below the confidence threshold. Linking also only succeeds once the match is actually finished and present in TheSportsDB.

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

Smoke-test the server from an MCP client session:

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

`THESPORTSDB_API_KEY`: key for the `get_match_results` provider (TheSportsDB). Defaults to the free public test key `3`; set a personal/Patreon key for higher limits and fresher data.

`THESPORTSDB_BASE_URL`: override the TheSportsDB base URL. Defaults to `https://www.thesportsdb.com/api/v1/json`.

`THESPORTSDB_CACHE_TTL_SECONDS`: in-memory cache TTL for results responses. Defaults to `300`.

`THESPORTSDB_TIMEOUT_SECONDS`: HTTP timeout for results requests. Defaults to `20`.

## Tests

Run unit tests:

```bash
uv run --extra dev pytest
```

Run the optional live FDJ ZIP test:

```bash
FDJ_LIVE_TESTS=1 uv run --extra dev pytest tests/test_live_fdj.py
```

Run lint:

```bash
uv run ruff check .
```

The live test downloads the official ZIP and validates the expected SQLite schema.

## Repository Topics

`mcp`, `model-context-protocol`, `python`, `fastmcp`, `sports-odds`, `fdj`, `parions-sport`, `sqlite`, `claude-desktop`, `cursor`

## License

MIT. See [LICENSE](LICENSE).
