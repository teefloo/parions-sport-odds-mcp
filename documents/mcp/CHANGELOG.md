# Changelog

## 0.1.3 - 2026-05-11

- Update GitHub Actions to current Node 24-compatible major versions.
- Raise the minimum MCP SDK requirement to `mcp[cli]>=1.27.1`.

## 0.1.2 - 2026-05-11

- Opt GitHub Actions into Node 24 to avoid Node 20 deprecation annotations.

## 0.1.1 - 2026-05-11

- Add repository hardening: package URLs, lint in CI, contribution guidance, GitHub templates, and typed package marker.
- Improve README development commands and MCP smoke-test instructions.

## 0.1.0 - 2026-05-11

- Initial FastMCP server for Parions Sport Point de Vente odds.
- Uses FDJ's public SQLite offer ZIP as the structured source.
- Adds tools for sports, competitions, odds search, and event odds lookup.
- Includes cache metadata, stale-cache fallback, unit tests, and a gated live FDJ schema test.
