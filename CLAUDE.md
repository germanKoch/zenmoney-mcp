# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ZenMoney MCP — a Python MCP (Model Context Protocol) server for integrating with ZenMoney (financial tracking app). Distributed as an installable package runnable via `uvx`.

## Development Environment

- **Python**: 3.12 (pinned in `.python-version`)
- **Package manager**: `uv`
- **Build backend**: hatchling (src layout, console script `zenmoney-mcp`)

## Commands

```bash
# Install dependencies
uv sync

# Run the server locally
uv run zenmoney-mcp

# Run via uvx (as end users do)
uvx --from git+https://github.com/germanKoch/zenmoney-mcp zenmoney-mcp

# Add a dependency
uv add <package>
```

## Architecture

- `src/zenmoney_mcp/server.py` — FastMCP server with tools (accounts, transactions, categories, budgets, suggest); `main()` is the console script entry point
- `src/zenmoney_mcp/zen_client.py` — ZenMoney API client (diff-based sync protocol) and OAuth token handling
- `src/zenmoney_mcp/models.py` — Pydantic models for ZenMoney entities

### Token resolution order

1. Env vars: `ZENMONEY_ACCESS_TOKEN` (alias `ZENMONEY_TOKEN`), `ZENMONEY_REFRESH_TOKEN`, `ZENMONEY_TOKEN_TYPE`, `ZENMONEY_EXPIRES_IN`
2. Token file `~/.config/zenmoney-mcp/token.json` (override via `ZENMONEY_TOKEN_FILE`)
3. Local browser OAuth flow on port 3000

Expired access tokens are refreshed via the refresh token; refreshed tokens are persisted to the token file.
