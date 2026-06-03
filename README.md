# ZenMoney MCP Server

An MCP (Model Context Protocol) server for integrating Claude with [ZenMoney](https://zenmoney.app) — a personal finance tracking app.

## Features

- View accounts and balances
- Search and filter transactions
- Create, update, and delete transactions
- Browse the category tree and budgets
- Category suggestions by payee name

## Quick Start (uvx)

The easiest way to run the server is with [uvx](https://docs.astral.sh/uv/guides/tools/) — no cloning or manual installation needed. Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) to be installed.

Add the server to Claude Desktop (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "zenmoney": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/germanKoch/zenmoney-mcp", "zenmoney-mcp"]
    }
  }
}
```

Or add it to Claude Code:

```bash
claude mcp add zenmoney -- uvx --from git+https://github.com/germanKoch/zenmoney-mcp zenmoney-mcp
```

That's it. On first use, a browser window opens with the ZenMoney login page — sign in, and the token is saved to `~/.config/zenmoney-mcp/token.json` and refreshed automatically afterwards.

## Authorization

The server resolves credentials in the following priority order:

### 1. Environment variables

Pass token data directly via the environment — useful for headless setups or when you already have a token (e.g. from [zerro.app/token](https://zerro.app/token)):

| Variable | Description |
|---|---|
| `ZENMONEY_ACCESS_TOKEN` | OAuth access token (alias: `ZENMONEY_TOKEN`) |
| `ZENMONEY_REFRESH_TOKEN` | OAuth refresh token — lets the server refresh the access token when it expires |
| `ZENMONEY_TOKEN_TYPE` | Token type (usually `bearer`) |
| `ZENMONEY_EXPIRES_IN` | Token lifetime in seconds |

```json
{
  "mcpServers": {
    "zenmoney": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/germanKoch/zenmoney-mcp", "zenmoney-mcp"],
      "env": {
        "ZENMONEY_ACCESS_TOKEN": "your_access_token",
        "ZENMONEY_REFRESH_TOKEN": "your_refresh_token",
        "ZENMONEY_TOKEN_TYPE": "bearer",
        "ZENMONEY_EXPIRES_IN": "86400"
      }
    }
  }
}
```

If only `ZENMONEY_ACCESS_TOKEN` is set (no refresh token), the token is used as-is and won't be refreshed — access tokens expire after 24 hours.

### 2. Saved token file

Tokens obtained via OAuth (or refreshed) are stored in `~/.config/zenmoney-mcp/token.json`. Override the location with the `ZENMONEY_TOKEN_FILE` environment variable.

### 3. Browser OAuth flow (automatic)

If no valid token is found, the server:

1. Starts a local HTTP server on port `3000`
2. Opens a browser with the ZenMoney login page
3. Exchanges the authorization code for a token after you sign in
4. Saves the token and refreshes it automatically when it expires

## Running from source

```bash
git clone https://github.com/germanKoch/zenmoney-mcp.git
cd zenmoney-mcp
uv sync
uv run zenmoney-mcp
```

Claude Desktop config for a local checkout:

```json
{
  "mcpServers": {
    "zenmoney": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/zenmoney-mcp", "zenmoney-mcp"]
    }
  }
}
```

## Available tools

| Tool | Description |
|---|---|
| `get_accounts` | List accounts with balances |
| `get_transactions` | Transactions filtered by date, account, or category |
| `create_transaction` | Create an expense, income, or transfer |
| `update_transaction` | Update an existing transaction |
| `delete_transaction` | Delete a transaction |
| `get_categories` | Category tree |
| `get_budgets` | Budgets per category for a period |
| `suggest_category` | Category suggestion by payee name |

## Example prompts

- "Show my accounts and balances"
- "What did I spend last week?"
- "Record a 500 RUB grocery expense from my Tinkoff card"
- "How much did I spend on cafes in January?"
