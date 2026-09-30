#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "== 1. Pull latest source =="
git pull

echo "== 2. Activate Python venv =="
source .venv/bin/activate

echo "== 3. Install backend dependencies =="
python -m pip install -r requirements.txt

echo "== 4. Run database migrations =="
python -m alembic upgrade head

echo "== 5. Install/build frontend =="
cd frontend
npm ci
npm run build
cd ..

echo "== 6. Restart backend =="
sudo systemctl restart reelforge-api

echo "== 7. Restart frontend =="
sudo systemctl restart reelforge-frontend

echo "== 8. Restart workers if enabled =="

if systemctl is-enabled --quiet reelforge-video-worker 2>/dev/null; then
  sudo systemctl restart reelforge-video-worker
fi

if systemctl is-enabled --quiet reelforge-youtube-worker 2>/dev/null; then
  sudo systemctl restart reelforge-youtube-worker
fi

echo "== 9. Check service status =="

systemctl is-active reelforge-api
systemctl is-active reelforge-frontend

echo "== 10. Health checks =="

curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null
curl -fsSI http://127.0.0.1:3001 >/dev/null

echo
echo "====================================="
echo " ReelForge deployment completed OK "
echo "====================================="
