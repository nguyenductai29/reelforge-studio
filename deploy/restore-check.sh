#!/usr/bin/env bash
# Validate a ReelForge backup without touching production (docs/BACKUP_RECOVERY.md).
#
#   deploy/restore-check.sh --dump /srv/data/backups/reelforge/reelforge-20261002-023000.dump
#       reads the archive (pg_restore --list) and checks the tables that matter are in it;
#   ... --scratch-url postgresql://studio_admin@127.0.0.1:5432/reelforge_restore_test
#       also restores it into that separate database (its name must contain restore, rehearsal, scratch or
#       test, and it can never be the production database) and counts users, workspaces, projects, orders;
#   ... --master-key /path/to/a/copy/of/master.key
#       also decrypts every stored secret of the restored data with that key copy (never printed).
#
# Exit status 0 when every check passes. Put the scratch password in ~/.pgpass, not on the command line.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
exec "$PYTHON" -m app.restore_check "$@"
