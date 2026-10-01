#!/usr/bin/env bash
# Deploy the current branch on the home server.
#
# Production needs only instance/bootstrap.json (database URL) and /etc/reelforge/master.key.
# Everything else is configured in Admin -> System settings / Payments and read from PostgreSQL;
# /etc/reelforge/runtime.env is an optional legacy fallback. See docs/PRODUCTION_BOOTSTRAP.md.
set -euo pipefail

cd "$(dirname "$0")"

echo "== 1. Pull latest source =="
git pull

echo "== 2. Activate Python venv =="
source .venv/bin/activate

echo "== 3. Install backend dependencies =="
python -m pip install -r requirements.txt

echo "== 4. Check the bootstrap: database URL and master key =="
if [ ! -f instance/bootstrap.json ] && [ -z "${REELFORGE_DATABASE_URL:-}" ]; then
  echo "instance/bootstrap.json is missing (database URL). See docs/PRODUCTION_BOOTSTRAP.md." >&2
  exit 1
fi
# Prints where the key comes from and any warning; never the key. Stops the deploy without a usable key.
if ! python -m app.master_key status; then
  echo >&2
  echo "No usable master key: restore /etc/reelforge/master.key from backup, or on a new installation run" >&2
  echo "  python -m app.master_key init" >&2
  exit 1
fi

echo "== 5. Run database migrations =="
python -m alembic upgrade head

echo "== 6. Install/build frontend =="
cd frontend
npm ci
npm run build
cd ..

echo "== 7. Restart API and frontend =="
sudo systemctl restart reelforge-api
sudo systemctl restart reelforge-frontend

echo "== 8. Restart workers if enabled =="
# Both layouts: one unit per worker (reelforge-text-worker) or the template (reelforge-worker@text).
for worker in text image video voice render source youtube social scheduler; do
  for unit in "reelforge-${worker}-worker" "reelforge-worker@${worker}"; do
    if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
      sudo systemctl restart "${unit}"
    fi
  done
done

echo "== 9. Check service status =="
systemctl is-active reelforge-api
systemctl is-active reelforge-frontend

echo "== 10. Health checks =="
curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null
curl -fsSI http://127.0.0.1:3001 >/dev/null

if ! cmp -s deploy/systemd/reelforge-worker@.service /etc/systemd/system/reelforge-worker@.service 2>/dev/null \
   && systemctl list-unit-files 'reelforge-worker@*' --no-legend 2>/dev/null | grep -q .; then
  echo "note: deploy/systemd/ changed; review it, copy it to /etc/systemd/system/ and run sudo systemctl daemon-reload."
fi

echo
echo "====================================="
echo " ReelForge deployment completed OK "
echo "====================================="
