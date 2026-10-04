"""Build a disposable stack for the browser tests in e2e/.stack (never the developer's instance/ or database).

    python e2e/prepare.py               # SQLite, build the frontend
    E2E_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/reelforge_e2e_test python e2e/prepare.py
    python e2e/prepare.py --skip-build  # reuse the last frontend build

* ``.stack/api``: a copy of app/, migrations/ and alembic.ini with its own instance/bootstrap.json (the test
  database, frontend origin http://127.0.0.1:3010 over plain HTTP) and its own master key; migrated to head.
* ``.stack/web``: a copy of the frontend sources with instance/config.json pointing at the test API
  (http://127.0.0.1:8010); node_modules is linked, not copied; ``next build``.
* ``.mail``: where the SMTP sink (tests/smtp_sink.py, port 2526) writes every email as an .eml file.

Playwright (e2e/playwright.config.ts) then starts the sink, the API and the web server, and runs the specs.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from cryptography.fernet import Fernet

E2E = Path(__file__).resolve().parent
ROOT = E2E.parent
STACK = E2E / ".stack"
API_PORT, WEB_PORT = 8010, 3010
FRONTEND_SKIP = {"node_modules", ".next", ".next-dev", "instance", "tsconfig.tsbuildinfo"}


def remove(path: Path) -> None:
    """Delete a folder; a linked node_modules (symlink or Windows junction) is unlinked, never followed."""
    if not path.exists() and not path.is_symlink():
        return
    linked = path / "node_modules"
    if linked.is_symlink() or (hasattr(linked, "is_junction") and linked.is_junction()):
        os.unlink(linked) if linked.is_symlink() else os.rmdir(linked)
    shutil.rmtree(path)


def link(target: Path, source: Path) -> None:
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(source)], check=True, capture_output=True)
    else:
        target.symlink_to(source, target_is_directory=True)


def prepare_api(database_url: str | None) -> None:
    api = STACK / "api"
    remove(api)
    api.mkdir(parents=True)
    for folder in ("app", "migrations"):
        shutil.copytree(ROOT / folder, api / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "alembic.ini", api / "alembic.ini")
    (api / "instance").mkdir()
    url = database_url or f"sqlite:///{(api / 'instance' / 'e2e.db').as_posix()}"
    (api / "instance" / "bootstrap.json").write_text(json.dumps({
        "database_url": url, "frontend_origin": f"http://127.0.0.1:{WEB_PORT}", "secure_cookies": False}))
    (api / "instance" / "master.key").write_text(Fernet.generate_key().decode() + "\n")
    # Movie sources (e2e/tests/12-movie-sources.spec.ts): dummy FFmpeg tools (e2e/movie_driver.py fakes the work)
    # and an empty import root (the spec puts a movie in its studio's folder, <root>/<workspace id>/).
    tools = api / "instance" / "tools"
    tools.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (tools / name).write_text("")
    shutil.rmtree(STACK / "import", ignore_errors=True)
    (STACK / "import").mkdir(parents=True)
    env = {**os.environ, "PYTHONPATH": str(api), "REELFORGE_MASTER_KEY_FILE": str(api / "instance" / "master.key")}
    if url.startswith("postgresql"):
        subprocess.run([sys.executable, "-m", "alembic", "downgrade", "base"], cwd=api, env=env, check=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=api, env=env, check=True)


def prepare_web(build: bool) -> None:
    web = STACK / "web"
    if build:
        remove(web)
        shutil.copytree(ROOT / "frontend", web, ignore=lambda folder, names: [n for n in names if n in FRONTEND_SKIP])
        link(web / "node_modules", ROOT / "frontend" / "node_modules")
    (web / "instance").mkdir(exist_ok=True)
    (web / "instance" / "config.json").write_text(json.dumps({"api_base_url": f"http://127.0.0.1:{API_PORT}"}))
    if build:
        npx = "npx.cmd" if os.name == "nt" else "npx"
        subprocess.run([npx, "next", "build"], cwd=web, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the disposable E2E stack")
    parser.add_argument("--skip-build", action="store_true", help="Reuse .stack/web and its last build")
    args = parser.parse_args()
    prepare_api(os.environ.get("E2E_DATABASE_URL"))
    prepare_web(not args.skip_build)
    shutil.rmtree(E2E / ".mail", ignore_errors=True)
    (E2E / ".mail").mkdir()
    print(f"e2e stack ready: API :{API_PORT}, web :{WEB_PORT}, mail {E2E / '.mail'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
