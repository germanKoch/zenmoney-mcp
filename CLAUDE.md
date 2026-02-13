# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ZenMoney MCP — a Python MCP (Model Context Protocol) server for integrating with ZenMoney (financial tracking app).

## Development Environment

- **Python**: 3.12 (pinned in `.python-version`)
- **Package manager**: `uv`
- **Virtual environment**: `.venv/` (managed by uv)

## Commands

```bash
# Run the server
python main.py

# Install dependencies (when added)
uv sync

# Add a dependency
uv add <package>
```

## Architecture

Early-stage project. Currently a single `main.py` entry point with a placeholder `main()` function. The project is set up to become an MCP server with ZenMoney API integration.