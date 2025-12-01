#!/bin/bash

set -e

# Auto-fix linting issues with ruff
uv run ruff check . --fix

# Auto-format code with ruff
uv run ruff format .

# Run mypy type checking (cannot be auto-fixed)
uv run mypy .
