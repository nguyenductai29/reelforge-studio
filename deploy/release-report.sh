#!/usr/bin/env bash
# Release report: the pre-flight, the readiness checks needing attention, the release gates recorded in
# Admin -> Verification (status, who, when, note), the CI result GitHub reports for this exact commit, and
# the verdict (docs/V1_RELEASE_STATUS.md).
#
#   bash deploy/release-report.sh                 # human-readable
#   bash deploy/release-report.sh --json          # machine-readable
#   bash deploy/release-report.sh --no-network    # without asking GitHub (CI then rests on the recorded gate)
#
# Verdict READY_FOR_TAG only when no pre-flight check fails, CI is not failing on this commit and every gate
# is passed (or not applicable where allowed); otherwise RELEASE_CANDIDATE with the blockers. A gate nobody
# recorded is MANUAL, never passed. Read-only; never prints a secret. Exit status 0 only for READY_FOR_TAG.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
exec "$PYTHON" -m app.release_check report "$@"
