#!/usr/bin/env bash
# Deploy the current branch on the home server.
#
# Production needs only instance/bootstrap.json (database URL) and /etc/reelforge/master.key.
# The public origin is https://studio.imokome-cloud.com (System Settings; Cloudflare Tunnel -> 127.0.0.1:3001);
# the frontend (127.0.0.1:3001) and the API (127.0.0.1:8000) stay private.
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
# frontend_origin / secure_cookies in instance/bootstrap.json override System Settings on one machine, for
# development (http://localhost:3000). On the server they would replace the public HTTPS origin.
if [ -f instance/bootstrap.json ] && python -c 'import json, sys
sys.exit(0 if {"frontend_origin", "secure_cookies"} & set(json.load(open("instance/bootstrap.json"))) else 1)'; then
  echo "STOP: instance/bootstrap.json sets frontend_origin or secure_cookies (a development override)." >&2
  echo "Production takes them from Admin -> System Settings -> General (https://studio.imokome-cloud.com," >&2
  echo "secure cookies on). Remove those keys from instance/bootstrap.json, then run ./deploy.sh again." >&2
  exit 1
fi
# A usable key: continue. No key: stop if PostgreSQL already holds encrypted data (restore the old key),
# otherwise create one (python -m app.master_key init), verify it, and continue. Never prints the key.
bash deploy/ensure-master-key.sh

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
workers=()
for worker in text image video voice render source youtube social scheduler; do
  for unit in "reelforge-${worker}-worker" "reelforge-worker@${worker}"; do
    if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
      sudo systemctl restart "${unit}"
      workers+=("${unit}")
    fi
  done
done
if [ "${#workers[@]}" -eq 0 ]; then
  echo "warning: no worker unit is enabled; workflows, publishing, email retries and alerts will not run." >&2
fi

echo "== 9. Health checks =="
# /health/ready: the database answers, migrations are at head, the master key is usable (app/health.py).
ready=no
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
    ready=yes
    break
  fi
  sleep 1
done
if [ "$ready" != yes ]; then
  echo "The API is not ready after 30 s:" >&2
  curl -sS http://127.0.0.1:8000/health/ready >&2 || true
  echo >&2
  exit 1
fi
frontend=no
for _ in $(seq 1 30); do
  if curl -fsSI http://127.0.0.1:3001 >/dev/null 2>&1; then
    frontend=yes
    break
  fi
  sleep 1
done
if [ "$frontend" != yes ]; then
  echo "The frontend (127.0.0.1:3001) does not answer after 30 s: journalctl -u reelforge-frontend -n 50" >&2
  exit 1
fi

echo "== 10. Check every service =="
# A unit that crashes at start is only visible a few seconds later (Restart=always); never report success then.
sleep 5
failed=()
for unit in reelforge-api reelforge-frontend ${workers[@]+"${workers[@]}"}; do
  if systemctl is-active --quiet "${unit}"; then
    printf '  %-36s active\n' "${unit}"
  else
    printf '  %-36s %s\n' "${unit}" "$(systemctl is-active "${unit}" 2>/dev/null || true)"
    failed+=("${unit}")
  fi
done
# The public origin as stored in System Settings. Not fatal: the Cloudflare Tunnel runs on its own.
public_origin="$(python - <<'EOF' 2>/dev/null || true
import json
from app.db import Session
from app.models import SystemSetting
with Session() as db:
    row = db.get(SystemSetting, "frontend_origin")
    print(json.loads(row.value) if row else "")
EOF
)"
if [ -n "$public_origin" ]; then
  if curl -fsSI --max-time 15 "$public_origin" >/dev/null; then
    echo "public origin OK: $public_origin"
  else
    echo "warning: $public_origin did not answer. The local services are up; check the Cloudflare Tunnel route" >&2
    echo "         (service http://127.0.0.1:3001) and Admin -> System Settings -> General." >&2
  fi
fi

# Units installed from deploy/systemd/ that differ from the repository's copy, and new ones not installed yet.
for unit in deploy/systemd/*.service deploy/systemd/*.timer; do
  installed="/etc/systemd/system/$(basename "$unit")"
  if [ -f "$installed" ] && ! cmp -s "$unit" "$installed"; then
    echo "note: $unit changed; review it, copy it to /etc/systemd/system/ and run sudo systemctl daemon-reload."
  elif [ ! -f "$installed" ]; then
    echo "note: $unit is not installed (new). See docs/PRODUCTION_BOOTSTRAP.md, step 7."
  fi
done
if systemctl list-unit-files reelforge-backup.timer --no-legend 2>/dev/null | grep -q enabled; then
  :
else
  echo "note: the daily database backup timer is not enabled: sudo systemctl enable --now reelforge-backup.timer"
fi

# No administrator yet: the first one is created on this server (public setup is refused through Cloudflare).
if curl -fsS http://127.0.0.1:8000/health/ready 2>/dev/null | python -c 'import json, sys
sys.exit(0 if "setup_open" in json.load(sys.stdin).get("warnings", []) else 1)'; then
  echo
  echo "!! No administrator exists yet. Create it now, on this server:" >&2
  echo "!!     (cd frontend && npm run create-admin)" >&2
  echo "!! The public site refuses first-run setup; see docs/PRODUCTION_BOOTSTRAP.md." >&2
fi

if [ "${#failed[@]}" -gt 0 ]; then
  echo >&2
  echo "FAILED: these services are not running: ${failed[*]-}" >&2
  echo "Look at: journalctl -u <unit> -n 100 --no-pager" >&2
  exit 1
fi

echo
echo "====================================="
echo " ReelForge deployment completed OK "
echo "====================================="
