#!/usr/bin/env bash
# Back up the ReelForge database now: pg_dump (custom format) into the backup directory, chmod 600, then the
# retention (14 daily, 8 weekly, 6 monthly; the newest dump is never removed). The reelforge-backup timer runs
# the same command daily. The database password is passed to pg_dump through PGPASSWORD only: never printed.
#
#   deploy/backup.sh                 # into Admin -> System settings -> Backups (default /srv/data/backups/reelforge)
#   deploy/backup.sh --dir /mnt/usb  # somewhere else, once
#
# The master key is not copied: keep /etc/reelforge/master.key in a separate, off-server place.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
exec "$PYTHON" -m app.backup run "$@"
