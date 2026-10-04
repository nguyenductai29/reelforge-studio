#!/usr/bin/env bash
# Release pre-flight: what this server can check about itself before a release (docs/V1_RELEASE_STATUS.md).
#
#   bash deploy/release-preflight.sh                        # human-readable
#   bash deploy/release-preflight.sh --json                 # machine-readable
#   bash deploy/release-preflight.sh --expect-commit <release candidate> --expect-origin https://reelforge.mul-service.com
#
# Source, database and migration head, master key, services and timers, ports (127.0.0.1 only), /health,
# media root, backups, FFmpeg, system configuration and administrators: each PASS, WARN, FAIL or MANUAL.
# Read-only (one probe file in the media root, removed at once); never prints a password, the master key,
# an API key, a token or a payment secret. Exit status 1 when a check FAILs, else 0.
# Run it as the service account (the one deploy.sh and the units use), from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
exec "$PYTHON" -m app.release_check preflight "$@"
