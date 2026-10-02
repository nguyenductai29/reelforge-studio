#!/usr/bin/env bash
# Deploy the current branch on the home server.
#
# Production needs only instance/bootstrap.json (database URL) and /etc/reelforge/master.key.
# The public origin is https://reelforge.mul-service.com (System Settings; Cloudflare Tunnel -> 127.0.0.1:3001);
# the frontend (127.0.0.1:3001) and the API (127.0.0.1:8000) stay private.
# Everything else is configured in Admin -> System settings / Payments and read from PostgreSQL;
# /etc/reelforge/runtime.env is an optional legacy fallback. See docs/PRODUCTION_BOOTSTRAP.md.
#
# Order: pull, verify the bootstrap, ensure the master key, install dependencies, migrate, build, restart, check
# every restarted service, /health/ready, unit files, timers. Any failure stops the deploy or ends it with exit
# status 1; it never reports success over a failed migration, build or service.
set -euo pipefail

cd "$(dirname "$0")"

echo "== 1. Pull latest source =="
# A build rewrites the tracked frontend/next-env.d.ts (see step 6): put the committed copy back so the pull cannot
# conflict on it.
git checkout -- frontend/next-env.d.ts 2>/dev/null || true
git pull
echo "commit $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "warning: tracked files differ from the commit (git status): the release pre-flight reports this as FAIL." >&2
fi

echo "== 2. Verify the bootstrap (database URL) =="
source .venv/bin/activate
if [ ! -f instance/bootstrap.json ] && [ -z "${REELFORGE_DATABASE_URL:-}" ]; then
  echo "instance/bootstrap.json is missing (database URL). See docs/PRODUCTION_BOOTSTRAP.md." >&2
  exit 1
fi
# frontend_origin / secure_cookies in instance/bootstrap.json override System Settings on one machine, for
# development (http://localhost:3000). On the server they would replace the public HTTPS origin.
if [ -f instance/bootstrap.json ] && python -c 'import json, sys
sys.exit(0 if {"frontend_origin", "secure_cookies"} & set(json.load(open("instance/bootstrap.json"))) else 1)'; then
  echo "STOP: instance/bootstrap.json sets frontend_origin or secure_cookies (a development override)." >&2
  echo "Production takes them from Admin -> System Settings -> General (https://reelforge.mul-service.com," >&2
  echo "secure cookies on). Remove those keys from instance/bootstrap.json, then run ./deploy.sh again." >&2
  exit 1
fi
if [ -f instance/bootstrap.json ] && [ -n "$(find instance/bootstrap.json -perm /077 2>/dev/null)" ]; then
  echo "warning: instance/bootstrap.json holds the database password and is readable by others: chmod 600 instance/bootstrap.json" >&2
fi
# The database must answer before anything is installed or migrated (only the error's type is printed).
if ! python - <<'EOF'
import sys
try:
    from sqlalchemy import text
    from app.db import engine
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
except Exception as exc:  # noqa: BLE001
    print(f"The database does not answer ({type(exc).__name__}). Check PostgreSQL and instance/bootstrap.json.",
          file=sys.stderr)
    sys.exit(1)
EOF
then
  exit 1
fi

echo "== 3. Ensure the master key =="
# A usable key: continue. No key: stop if PostgreSQL already holds encrypted data (restore the old key),
# otherwise create one (python -m app.master_key init), verify it, and continue. Never prints the key.
bash deploy/ensure-master-key.sh

echo "== 4. Install backend dependencies =="
python -m pip install -r requirements.txt

echo "== 5. Run database migrations =="
python -m alembic upgrade head
current="$(python -m alembic current 2>/dev/null | tail -n 1 || true)"
case "$current" in
  *"(head)"*) echo "migration: $current" ;;
  *) echo "The database is not at the migration head after upgrading (alembic current: ${current:-none})." >&2
     exit 1 ;;
esac

echo "== 6. Install/build frontend =="
cd frontend
npm ci
npm run build
cd ..
# next build rewrites frontend/next-env.d.ts (it names the build's folder). Put the committed copy back: the checkout
# stays exactly the deployed commit (the release pre-flight checks it) and the next pull cannot conflict on it.
git checkout -- frontend/next-env.d.ts 2>/dev/null || true

echo "== 7. Restart the API, the frontend and the enabled workers =="
sudo systemctl restart reelforge-api
sudo systemctl restart reelforge-frontend
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

echo "== 8. Check every restarted service =="
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

echo "== 9. Health: /health/ready and the frontend =="
# /health/ready: the database answers, migrations are at head, the master key is usable (app/health.py).
problems=()
ready=no
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
    ready=yes
    break
  fi
  sleep 1
done
if [ "$ready" = yes ]; then
  echo "  /health/ready                        ok"
else
  echo "The API is not ready after 30 s:" >&2
  curl -sS http://127.0.0.1:8000/health/ready >&2 || true
  echo >&2
  problems+=("the API is not ready (/health/ready)")
fi
frontend=no
for _ in $(seq 1 30); do
  if curl -fsSI http://127.0.0.1:3001 >/dev/null 2>&1; then
    frontend=yes
    break
  fi
  sleep 1
done
if [ "$frontend" = yes ]; then
  echo "  http://127.0.0.1:3001                ok"
else
  echo "The frontend (127.0.0.1:3001) does not answer after 30 s: journalctl -u reelforge-frontend -n 50" >&2
  problems+=("the frontend does not answer (127.0.0.1:3001)")
fi
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

echo "== 10. Unit files =="
# Units installed from deploy/systemd/ that differ from the repository's copy, and new ones not installed yet.
# Nothing is copied automatically: an installed unit may carry this server's User or paths.
for unit in deploy/systemd/*.service deploy/systemd/*.timer; do
  installed="/etc/systemd/system/$(basename "$unit")"
  if [ -f "$installed" ] && ! cmp -s "$unit" "$installed"; then
    echo "note: $unit changed; review it, copy it to /etc/systemd/system/ and run sudo systemctl daemon-reload."
  elif [ ! -f "$installed" ]; then
    echo "note: $unit is not installed (new). See docs/PRODUCTION_BOOTSTRAP.md, step 7."
  fi
done
for unit in reelforge-api reelforge-frontend ${workers[@]+"${workers[@]}"}; do
  if [ "$(systemctl show -p NeedDaemonReload --value "${unit}" 2>/dev/null || true)" = yes ]; then
    echo "note: ${unit} changed on disk since systemd loaded it: sudo systemctl daemon-reload, then ./deploy.sh again."
  fi
done

echo "== 11. Timers =="
for timer in reelforge-backup.timer reelforge-media-maintenance.timer; do
  printf '  %-36s %s, %s\n' "${timer}" "$(systemctl is-enabled "${timer}" 2>/dev/null || true)" \
    "$(systemctl is-active "${timer}" 2>/dev/null || true)"
  if ! systemctl is-enabled --quiet "${timer}" 2>/dev/null; then
    echo "note: ${timer} is not enabled: sudo systemctl enable --now ${timer}"
  fi
done

# No administrator yet: the first one is created on this server (public setup is refused through Cloudflare).
if curl -fsS http://127.0.0.1:8000/health/ready 2>/dev/null | python -c 'import json, sys
sys.exit(0 if "setup_open" in json.load(sys.stdin).get("warnings", []) else 1)'; then
  echo
  echo "!! No administrator exists yet. Create it now, on this server:" >&2
  echo "!!     (cd frontend && npm run create-admin)" >&2
  echo "!! The public site refuses first-run setup; see docs/PRODUCTION_BOOTSTRAP.md." >&2
fi

if [ "${#failed[@]}" -gt 0 ] || [ "${#problems[@]}" -gt 0 ]; then
  echo >&2
  if [ "${#failed[@]}" -gt 0 ]; then
    echo "FAILED: these services are not running: ${failed[*]-}" >&2
  fi
  for problem in ${problems[@]+"${problems[@]}"}; do
    echo "FAILED: ${problem}" >&2
  done
  echo "Look at: journalctl -u <unit> -n 100 --no-pager" >&2
  exit 1
fi

echo
echo "====================================="
echo " ReelForge deployment completed OK "
echo "====================================="
echo "Release checks: bash deploy/release-preflight.sh (docs/V1_RELEASE_STATUS.md)"
