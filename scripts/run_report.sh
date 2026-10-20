#!/bin/sh
# Hourly cron job (:15): the report job, run as in the container. See nfl_alt_lines/cli.py.
cd "$(dirname "$0")/.." || exit 1
exec "${UV:-$HOME/.local/bin/uv}" run --frozen nfl-alt-lines report
