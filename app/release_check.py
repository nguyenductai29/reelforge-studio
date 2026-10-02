"""Release pre-flight and release report (V1.0 release closure, docs/V1_RELEASE_STATUS.md).

    bash deploy/release-preflight.sh [--json] [--expect-commit SHA] [--expect-origin URL]
    bash deploy/release-report.sh    [--json] [--no-network] [--expect-commit SHA] [--expect-origin URL]

**Pre-flight** (``python -m app.release_check preflight``): what this server can tell about itself, safely. The
source (commit, branch, a clean working tree), the database (connection, migration at head, a role that is not a
superuser), the master key (file, permissions, every stored secret decrypts), the services and timers (systemd),
the API and the frontend listening on 127.0.0.1 only, ``/health/live`` and ``/health/ready``, the media root
(exists, writable, free space), the backups (timer, last success, age, newest dump, no key beside them), FFmpeg,
the system configuration (no development override, no legacy runtime values, the public HTTPS origin, Secure
cookies, a trusted proxy that does not trust everyone) and the accounts (setup closed, an active system
administrator). Each check is PASS, WARN, FAIL or MANUAL (only a person can check it; the line says where to
record it). Exit status 1 when a check FAILs, else 0.

**Report** (``python -m app.release_check report``): the pre-flight, the readiness checks needing attention, the
release gates an administrator recorded in Admin → Verification (status, who, when, note), the CI result GitHub
reports for this exact commit (read-only and unauthenticated; ``--no-network`` skips it) and the verdict:
``READY_FOR_TAG`` only when no pre-flight check fails, CI is not failing on this commit, and every gate is passed
(or not applicable where that is allowed); otherwise ``RELEASE_CANDIDATE`` with the blockers. A gate nobody recorded
is MANUAL, never passed. Exit status 0 only for ``READY_FOR_TAG``.

Both only read: the one write is a probe file in the media root, removed at once. Nothing is configured, migrated,
restarted, paid or published, and nothing printed contains a password, the master key, an API key, an OAuth token
or a payment secret (names and counts only; the database password is scrubbed from any error). Run them as the
service account from the application folder, as the wrappers in ``deploy/`` do.
"""
import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000"
FRONTEND = "http://127.0.0.1:3001"
PORTS = (("API", 8000), ("frontend", 3001))
CORE_UNITS = ("reelforge-api", "reelforge-frontend")
BACKUP_TIMER, MAINTENANCE_TIMER = "reelforge-backup.timer", "reelforge-media-maintenance.timer"
SYSTEMD_DIR = Path("/etc/systemd/system")
JOURNALD_CONF = Path("/etc/systemd/journald.conf.d/reelforge.conf")
# systemd's EnvironmentFile for every unit; .env.runtime (or REELFORGE_ENV_FILE) is what every process loads itself.
SYSTEM_RUNTIME_ENV = Path("/etc/reelforge/runtime.env")
PASS, WARN, FAIL, MANUAL = "PASS", "WARN", "FAIL", "MANUAL"
LEVELS = (PASS, WARN, FAIL, MANUAL)
READY, CANDIDATE = "READY_FOR_TAG", "RELEASE_CANDIDATE"
# Variables a server may keep outside the database by design (app/system_config.py ENVIRONMENT, "bootstrap").
ENVIRONMENT_ONLY = frozenset({"REELFORGE_DATABASE_URL", "REELFORGE_MASTER_KEY_FILE", "REELFORGE_LOG_FORMAT",
                              "REELFORGE_LOG_LEVEL"})
_CREDENTIALS_IN_URL = re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+@")
_GITHUB = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: str = ""


class Probe:
    """What the checks ask of the system: commands, local HTTP and GitHub. Tests replace it."""

    def run(self, command: list[str], timeout: int = 30) -> tuple[int, str]:
        try:
            done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout, check=False, stdin=subprocess.DEVNULL, cwd=ROOT)
        except FileNotFoundError:
            return 127, ""
        except (OSError, subprocess.SubprocessError):
            return 126, ""
        return done.returncode, done.stdout or ""

    def fetch(self, url: str, *, timeout: float = 10, headers: dict | None = None,
              local: bool = True) -> tuple[int, bytes]:
        """(HTTP status, body); status 0 when nothing answered. Local addresses never go through a proxy."""
        handlers = [urllib.request.ProxyHandler({})] if local else []
        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as response:
                return response.status, response.read(2_000_000)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read(2_000_000)
            except OSError:
                return exc.code, b""
        except (urllib.error.URLError, OSError, ValueError):
            return 0, b""


# --- helpers -----------------------------------------------------------------------------------------------

def _mode(path: Path) -> int | None:
    if os.name != "posix":
        return None
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - only used in messages
        return "this account"


def _size(count: int | None) -> str:
    value = float(count or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _http(status: int) -> str:
    return f"HTTP {status}" if status else "no answer"


def _json(body: bytes) -> dict:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {text[0][:200]}" if text else type(exc).__name__


def _secrets_to_hide() -> list[str]:
    """The database password, so that no error message can carry it (whatever a driver puts in one)."""
    try:
        from app.db import url
    except Exception:  # noqa: BLE001 - no URL, nothing to hide
        return []
    return [url.password] if url.password else []


def scrub(text: str, hidden: list[str] | None = None) -> str:
    text = _CREDENTIALS_IN_URL.sub("***@", text)
    for secret in hidden if hidden is not None else _secrets_to_hide():
        if secret:
            text = text.replace(secret, "***")
    return text


def counts(checks: list[Check]) -> dict:
    found = dict.fromkeys(LEVELS, 0)
    for check in checks:
        found[check.status] += 1
    return found


def _workers() -> tuple[str, ...]:
    from app.heartbeat import WORKERS

    return tuple(name.removesuffix("_worker") for name in WORKERS)


def mirror_service_environment() -> dict[str, list[str] | None]:
    """Load the legacy runtime files the services see, so settings read here as they do there.

    Returns each file found with the names it sets (None: it exists but cannot be read). Values stay in this
    process; nothing prints them."""
    from app import runtime_env

    found: dict[str, list[str] | None] = {}
    try:
        named = runtime_env.env_file_path()  # REELFORGE_ENV_FILE, else .env.runtime: every process loads it
    except runtime_env.RuntimeEnvError:
        named = None
        found["REELFORGE_ENV_FILE (names a missing file)"] = None
    for path in (named, SYSTEM_RUNTIME_ENV):
        if path is None or not path.exists():
            continue
        try:
            names = sorted(name for name, value in runtime_env.parse_env_file(path).items() if value)
            # As systemd and every process do: an empty value sets nothing, a variable already set wins.
            runtime_env.load_runtime_env(path)
        except (runtime_env.RuntimeEnvError, OSError):
            found[str(path)] = None
            continue
        found[str(path)] = names
    return found


# --- pre-flight areas --------------------------------------------------------------------------------------

def source_checks(probe: Probe, expect_commit: str | None = None) -> list[Check]:
    area = "SOURCE"
    code, out = probe.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    commit = out.strip() if code == 0 else ""
    if not commit:
        unknown = [Check(area, "commit", WARN,
                         "not a git checkout (or git refused it): the deployed commit is unknown")]
        return unknown + ([Check(area, "expected commit", FAIL, f"cannot tell whether HEAD is {expect_commit}")]
                          if expect_commit else [])
    checks = [Check(area, "commit", PASS, commit)]
    if expect_commit:
        same = commit.startswith(expect_commit.strip().lower())
        checks.append(Check(area, "expected commit", PASS if same else FAIL,
                            f"HEAD is {expect_commit}" if same else f"HEAD is {commit[:12]}, not {expect_commit}"))
    _, branch = probe.run(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip()
    checks.append(Check(area, "branch", PASS, "detached HEAD (a tag or commit checkout)" if branch == "HEAD"
                        else branch or "unknown"))
    code, out = probe.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"])
    changed = [line[3:].strip() for line in out.splitlines() if line.strip()]
    if code != 0:
        checks.append(Check(area, "working tree", WARN,
                            "git status failed (run it as the account that owns the folder)"))
    elif changed:
        checks.append(Check(area, "working tree", FAIL, f"{len(changed)} tracked file(s) differ from the commit "
                            f"({', '.join(changed[:5])}): the running code is not the code CI tested"))
    else:
        checks.append(Check(area, "working tree", PASS, "clean: no tracked file changed"))
    checks.append(Check(area, "CI on this commit", MANUAL, "GitHub Actions green on this exact commit (the report "
                        "reads it); record it as release_ci_green in Admin → Verification"))
    return checks


def database_checks() -> tuple[list[Check], bool]:
    area = "DATABASE"
    try:
        from sqlalchemy import text

        from app.db import engine, url
    except Exception as exc:  # noqa: BLE001 - reported
        return [Check(area, "connection", FAIL, f"cannot read the database URL ({type(exc).__name__}); "
                      "see docs/PRODUCTION_BOOTSTRAP.md, step 3")], False
    dialect = url.get_backend_name()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - reported (scrubbed of the password)
        return [Check(area, "connection", FAIL, _first_line(exc))], False
    checks = [Check(area, "connection", PASS, f"postgresql, database {url.database}") if dialect == "postgresql"
              else Check(area, "connection", FAIL, f"{dialect}: production runs on PostgreSQL")]
    from app import health

    head = health.migration_head()
    try:
        with engine.connect() as connection:
            current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:  # noqa: BLE001
        current = None
        checks.append(Check(area, "alembic current", FAIL, f"no alembic_version ({type(exc).__name__}): "
                            "python -m alembic upgrade head"))
    else:
        checks.append(Check(area, "alembic current", PASS if current else FAIL, current or "none"))
    if current is not None:
        checks.append(Check(area, "expected head", PASS if current == head else FAIL,
                            f"{head}" if current == head else f"the database is at {current}, the code expects "
                                                              f"{head}: python -m alembic upgrade head"))
    if dialect == "postgresql":
        try:
            with engine.connect() as connection:
                superuser = connection.execute(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar()
        except Exception:  # noqa: BLE001
            superuser = None
        if superuser is None:
            checks.append(Check(area, "role", MANUAL, "could not read the role: check it is not a superuser"))
        else:
            checks.append(Check(area, "role", WARN if superuser else PASS,
                                "the application's role is a superuser: give it a role that owns only its database"
                                if superuser else "not a superuser"))
    return checks, True


def master_key_checks(db_ok: bool) -> list[Check]:
    area = "MASTER KEY"
    from app import master_key

    info = master_key.status()
    resolved = master_key.resolve()
    if info["problem"]:
        where = f" {info['path']}" if info["path"] else ""
        return [Check(area, "key", FAIL, f"{info['source']}{where}: {info['problem']}; restore the backed-up key file "
                      "(docs/BACKUP_RECOVERY.md), never generate a new one over encrypted data")]
    checks = []
    path = Path(info["path"]) if info["source"] == "file" and info["path"] else None
    if path is None:
        checks.append(Check(area, "key", WARN, "from the legacy REELFORGE_TOKEN_ENCRYPTION_KEY variable: move it into "
                            "/etc/reelforge/master.key (python -m app.master_key init)"))
    else:
        checks.append(Check(area, "key", PASS, f"file {path}"))
        mode = _mode(path)
        if mode is None:
            checks.append(Check(area, "permissions", MANUAL,
                                "not a POSIX system: only the service account may read it"))
        else:
            checks.append(Check(area, "permissions", FAIL if mode & 0o077 else PASS,
                                f"mode {mode:03o}: chmod 600 {path}" if mode & 0o077 else f"mode {mode:03o}"))
            if hasattr(os, "geteuid"):
                owner = os.stat(path).st_uid
                checks.append(Check(area, "owner", PASS if owner == os.geteuid() else WARN,
                                    f"owned by {_user()}" if owner == os.geteuid() else
                                    f"owned by uid {owner}, not {_user()}: run this check as the service account"))
        if info["legacy_env_matches"] is False:
            checks.append(Check(area, "legacy variable", WARN, "REELFORGE_TOKEN_ENCRYPTION_KEY holds another key: "
                                "remove it from the runtime file (the file wins)"))
    if db_ok and resolved["key"]:
        try:
            if path is not None:
                from app import restore_check
                from app.db import url

                result = restore_check.decrypt(url, path)
                ok, failed = result["ok"], result["failed"]
            else:
                from app import health
                from app.db import engine

                with engine.connect() as connection:
                    failed, ok = health.undecryptable_secrets(connection), None
        except Exception as exc:  # noqa: BLE001 - reported without values
            checks.append(Check(area, "stored secrets", FAIL, f"could not check ({type(exc).__name__})"))
        else:
            if failed:
                checks.append(Check(area, "stored secrets", FAIL, f"{failed} stored secret(s) do not decrypt with this "
                                    "key: the key is not the one the data was encrypted with"))
            else:
                checks.append(Check(area, "stored secrets", PASS, "no stored secret yet" if ok == 0
                                    else f"all {ok} stored secrets decrypt" if ok else "the sampled secrets decrypt"))
        try:
            from app import readiness
            from app.db import Session

            with Session() as db:
                confirmation = next((item for item in readiness.security_checks(db)
                                     if item["key"] == "master_key_backup"), None)
        except Exception:  # noqa: BLE001
            confirmation = None
        if confirmation is not None and confirmation["status"] != "off":
            checks.append(Check(area, "backup confirmed", PASS if confirmation["status"] == "ok" else WARN,
                                "confirmed in Admin → System settings → Backups" if confirmation["status"] == "ok"
                                else f"{confirmation.get('detail')}: confirm the off-server copy in Admin → System "
                                     "settings → Backups"))
    checks.append(Check(area, "off-server copy", MANUAL, "a copy off the server, apart from the dumps: record "
                        "master_key_file and backup_offsite in Admin → Verification"))
    return checks


def _enabled(probe: Probe, unit: str) -> str:
    return probe.run(["systemctl", "is-enabled", unit])[1].strip() or "not-found"


def _active(probe: Probe, unit: str) -> str:
    return probe.run(["systemctl", "is-active", unit])[1].strip() or "unknown"


def _timer_check(probe: Probe, area: str, timer: str) -> Check:
    enabled, active = _enabled(probe, timer), _active(probe, timer)
    good = enabled.startswith("enabled") and active == "active"
    return Check(area, timer, PASS if good else FAIL, "enabled, active" if good
                 else f"{enabled}, {active}: sudo systemctl enable --now {timer}")


def service_checks(probe: Probe, db_ok: bool) -> list[Check]:
    area = "SERVICES"
    code, _ = probe.run(["systemctl", "--version"])
    if code != 0:
        return [Check(area, "systemd", MANUAL, "systemctl is not available here: check the units by hand "
                      "(docs/PRODUCTION_BOOTSTRAP.md, step 7)")]
    checks = []
    for unit in CORE_UNITS:
        active = _active(probe, unit)
        checks.append(Check(area, unit, PASS if active == "active" else FAIL,
                            active if active == "active" else f"{active}: journalctl -u {unit} -n 100"))
    beats: dict[str, dict] = {}
    if db_ok:
        try:
            from app import heartbeat
            from app.db import Session

            with Session() as db:
                beats = {item["worker"]: item for item in heartbeat.worker_health(db)}
        except Exception:  # noqa: BLE001 - the unit state still says enough
            beats = {}
    not_enabled = []
    for name in _workers():
        # Both layouts, as deploy.sh: one unit per worker, or the template.
        unit = next((candidate for candidate in (f"reelforge-{name}-worker", f"reelforge-worker@{name}")
                     if _enabled(probe, candidate).startswith("enabled")), None)
        if unit is None:
            not_enabled.append(name)
            continue
        active = _active(probe, unit)
        beat = beats.get(f"{name}_worker")
        if active != "active":
            checks.append(Check(area, unit, FAIL, f"{active}: journalctl -u {unit} -n 100"))
        elif beat and beat["status"] != "ok":
            checks.append(Check(area, unit, WARN, f"active, but its heartbeat is {beat['status']}"))
        else:
            seen = f", heartbeat {beat['seconds_ago']} s ago" if beat and beat.get("seconds_ago") is not None else ""
            checks.append(Check(area, unit, PASS, f"active{seen}"))
    if not_enabled:
        checks.append(Check(area, "workers not enabled", WARN, f"{', '.join(not_enabled)}: steps that need them wait "
                            "(sudo systemctl enable --now reelforge-worker@<name>)"))
    code, out = probe.run(["systemctl", "list-units", "--state=failed", "--plain", "--no-legend", "reelforge*"])
    failed = [line.split()[0] for line in out.splitlines() if line.strip()]
    checks.append(Check(area, "failed units", FAIL if failed else PASS, ", ".join(failed) if failed else "none"))
    checks.append(_timer_check(probe, area, MAINTENANCE_TIMER))
    reload = [unit for unit in CORE_UNITS
              if probe.run(["systemctl", "show", "-p", "NeedDaemonReload", "--value", unit])[1].strip() == "yes"]
    stale, missing = [], []
    for unit in sorted((ROOT / "deploy" / "systemd").glob("reelforge*")):
        installed = SYSTEMD_DIR / unit.name
        if not installed.is_file():
            missing.append(unit.name)
        else:
            try:
                if installed.read_bytes() != unit.read_bytes():
                    stale.append(unit.name)
            except OSError:
                stale.append(unit.name)
    if reload:
        checks.append(Check(area, "daemon-reload", WARN,
                            f"{', '.join(reload)} changed on disk: sudo systemctl daemon-reload"))
    if missing or stale:
        parts = [f"not installed: {', '.join(missing)}"] if missing else []
        if stale:
            parts.append(f"differ from deploy/systemd/ (review; adjusted User or paths are fine): {', '.join(stale)}")
        checks.append(Check(area, "unit files", WARN, "; ".join(parts)))
    else:
        checks.append(Check(area, "unit files", PASS,
                            "every unit in deploy/systemd/ is installed as in the repository"))
    checks.append(Check(area, "journald limits", PASS if JOURNALD_CONF.is_file() else WARN,
                        str(JOURNALD_CONF) if JOURNALD_CONF.is_file()
                        else "not installed: sudo cp deploy/journald/reelforge.conf /etc/systemd/journald.conf.d/"))
    return checks


def listeners(output: str) -> dict[int, list[str]]:
    """``ss -ltn`` output → {port: [local addresses]}."""
    found: dict[int, list[str]] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "LISTEN":
            continue
        host, _, port = parts[3].rpartition(":")
        if not port.isdigit():
            continue
        found.setdefault(int(port), []).append(host.strip("[]").split("%", 1)[0])
    return found


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # "*" and names: every interface


def port_checks(probe: Probe) -> list[Check]:
    area = "PORTS"
    code, out = probe.run(["ss", "-ltn"])
    if code != 0:
        return [Check(area, "listeners", MANUAL,
                      "ss is not available: ss -ltnp must show 8000 and 3001 on 127.0.0.1 only")]
    found = listeners(out)
    checks = []
    for label, port in PORTS:
        hosts = sorted(set(found.get(port, [])))
        shown = ", ".join(f"[{host}]:{port}" if ":" in host else f"{host}:{port}" for host in hosts)
        if not hosts:
            checks.append(Check(area, label, FAIL, f"nothing listens on port {port}"))
        elif all(_loopback(host) for host in hosts):
            checks.append(Check(area, label, PASS, shown))
        else:
            checks.append(Check(area, label, FAIL, f"listens on {shown}: reachable from the network; it must be "
                                f"127.0.0.1:{port} only"))
    return checks


def health_checks(probe: Probe) -> list[Check]:
    area = "HEALTH"
    checks = []
    status, _ = probe.fetch(f"{API}/health/live")
    checks.append(Check(area, "/health/live", PASS if status == 200 else FAIL,
                        "200" if status == 200 else f"{_http(status)}: is reelforge-api running?"))
    status, body = probe.fetch(f"{API}/health/ready")
    data = _json(body)
    if status == 200 and data.get("status") == "ok":
        warnings = [str(item) for item in data.get("warnings") or []]
        checks.append(Check(area, "/health/ready", WARN if warnings else PASS,
                            f"ready, warnings: {', '.join(warnings)}" if warnings
                            else "ready: database, migrations at head, master key decrypting"))
    else:
        failing = [f"{name} {check.get('status')}" for name, check in (data.get("checks") or {}).items()
                   if isinstance(check, dict) and check.get("status") != "ok"]
        checks.append(Check(area, "/health/ready", FAIL,
                            _http(status) + (f": {', '.join(failing)}" if failing else "")))
    status, _ = probe.fetch(f"{FRONTEND}/")
    checks.append(Check(area, "frontend", PASS if 200 <= status < 400 else FAIL,
                        f"{FRONTEND} answers ({status})" if 200 <= status < 400
                        else f"{FRONTEND}: {_http(status)}; journalctl -u reelforge-frontend -n 50"))
    status, body = probe.fetch(f"{API}/internal/metrics")
    checks.append(Check(area, "/internal/metrics", PASS if status == 200 else WARN,
                        "answers on 127.0.0.1" if status == 200 else f"{_http(status)}"))
    return checks


def storage_checks(db) -> list[Check]:
    area = "STORAGE"
    from app import alerts, storage

    root = storage.media_root(db)
    if not root.is_dir():
        return [Check(area, "media root", FAIL, f"{root} does not exist: create it for the service account "
                      "(Admin → System settings → Storage)")]
    checks = [Check(area, "media root", PASS, str(root))]
    probe = root / f".release-preflight-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"ok")
    except OSError as exc:
        checks.append(Check(area, "writable", FAIL,
                            f"not writable by {_user()} ({exc.strerror or type(exc).__name__})"))
    else:
        checks.append(Check(area, "writable", PASS, f"writable by {_user()} (a probe file was written and removed)"))
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    disk = storage.disk_usage(db)
    if disk is None or disk.get("percent") is None:
        checks.append(Check(area, "free space", WARN, "unknown"))
    else:
        used = disk["percent"]
        level = FAIL if used >= alerts.DISK_CRITICAL else WARN if used >= alerts.DISK_WARNING else PASS
        checks.append(Check(area, "free space", level, f"{_size(disk['free_bytes'])} free of "
                            f"{_size(disk['total_bytes'])} ({used}% used; alerts at {alerts.DISK_WARNING} and "
                            f"{alerts.DISK_CRITICAL} %)"))
    return checks


def backup_checks(db, probe: Probe, now: datetime) -> list[Check]:
    area = "BACKUPS"
    from app import backup

    checks = []
    code, _ = probe.run(["systemctl", "--version"])
    if code == 0:
        checks.append(_timer_check(probe, area, BACKUP_TIMER))
    else:
        checks.append(Check(area, BACKUP_TIMER, MANUAL, "systemctl is not available: systemctl list-timers by hand"))
    info = backup.status(db, now)
    directory = Path(info["directory"])
    problem = backup.directory_problem(directory)
    if problem:
        checks.append(Check(area, "directory", FAIL, f"{directory}: {problem}"))
    elif not directory.is_dir():
        checks.append(Check(area, "directory", FAIL,
                            f"{directory} does not exist: sudo systemctl start reelforge-backup"))
    else:
        mode = _mode(directory)
        checks.append(Check(area, "directory", WARN if mode is not None and mode & 0o077 else PASS,
                            f"{directory}, mode {mode:03o}: chmod 700 {directory}" if mode is not None and mode & 0o077
                            else f"{directory}" + (f", mode {mode:03o}" if mode is not None else "")))
        try:
            keys = sorted(path.name for path in directory.iterdir()
                          if path.suffix == ".key" or "master" in path.name.lower())
        except OSError:
            keys = None
        if keys is None:
            checks.append(Check(area, "key apart", WARN, f"cannot list {directory} as {_user()}"))
        else:
            checks.append(Check(area, "key apart", FAIL if keys else PASS,
                                f"{', '.join(keys)} beside the dumps: keep the master key elsewhere" if keys
                                else "no key file beside the dumps"))
    last, failure = info["last_success"], info["last_failure"]
    limit = info["max_age_hours"]
    if last is None:
        checks.append(Check(area, "last success", FAIL, "no successful backup recorded: sudo systemctl start "
                            "reelforge-backup, then journalctl -u reelforge-backup -n 50"))
    else:
        overdue = last["age_hours"] > limit
        checks.append(Check(area, "last success", FAIL if overdue else PASS,
                            f"{last['at']} ({last['age_hours']} h ago, limit {limit} h)"))
    if failure and (last is None or failure["at"] > last["at"]):
        checks.append(Check(area, "last run", WARN, f"failed at {failure['at']}: {(failure.get('error') or '')[:160]}"))
    try:
        dumps = backup.backups(directory) if directory.is_dir() else []
    except OSError:
        dumps = None
    if dumps is None:
        checks.append(Check(area, "newest dump", WARN, f"cannot list {directory} as {_user()}"))
    elif not dumps:
        checks.append(Check(area, "newest dump", FAIL, f"no dump in {directory}"))
    else:
        newest = dumps[0][0]
        size = newest.stat().st_size
        mode = _mode(newest)
        recorded = last["file"] if last else None
        if size == 0:
            checks.append(Check(area, "newest dump", FAIL, f"{newest.name} is empty"))
        elif recorded and not (directory / recorded).is_file():
            checks.append(Check(area, "newest dump", FAIL, f"the last recorded dump {recorded} is missing"))
        else:
            loose = mode is not None and mode & 0o077
            checks.append(Check(area, "newest dump", WARN if loose else PASS,
                                f"{newest.name}, {_size(size)}" + (f", mode {mode:03o}" if mode is not None else "")
                                + (": chmod 600" if loose else "")))
    checks.append(Check(area, "off-server copy", MANUAL, "the dumps copied off the server (encrypted), apart from "
                        "the key: record backup_offsite in Admin → Verification"))
    checks.append(Check(area, "restore rehearsal", MANUAL, "deploy/restore-check.sh --dump <newest> --scratch-url "
                        "…/reelforge_restore_test --master-key <off-server copy>: record restore_rehearsal"))
    return checks


def ffmpeg_checks(probe: Probe) -> list[Check]:
    area = "FFMPEG"
    from app import render

    checks = []
    for name, path in zip(("ffmpeg", "ffprobe"), render.tools()):
        if path is None:
            checks.append(Check(area, name, FAIL, "not found: sudo apt install ffmpeg (or RENDER_FFMPEG_PATH)"))
            continue
        _, out = probe.run([path, "-version"])
        version = out.splitlines()[0][:60] if out.strip() else "version unknown"
        checks.append(Check(area, name, PASS, f"{path}: {version}"))
    try:
        font = render.font_issue()
    except Exception:  # noqa: BLE001
        font = ("font_unavailable", "")
    checks.append(Check(area, "subtitle font", WARN if font else PASS,
                        "missing: sudo apt install fonts-noto-core fonts-noto-cjk" if font else render.subtitle_font()))
    return checks


def config_checks(db, legacy: dict[str, list[str] | None], expect_origin: str | None = None) -> list[Check]:
    area = "SYSTEM CONFIG"
    from app import db as database
    from app import system_config

    checks = []
    bootstrap = database.CONFIG_FILE
    if bootstrap.is_file():
        try:
            keys = set(json.loads(bootstrap.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            keys = None
        if keys is None:
            checks.append(Check(area, "bootstrap.json", FAIL, "instance/bootstrap.json is not readable JSON"))
        else:
            overrides = sorted(keys & set(database.LOCAL_SETTINGS))
            extra = sorted(keys - {"database_url"} - set(overrides))
            if overrides:
                checks.append(Check(area, "bootstrap.json", FAIL, f"sets {', '.join(overrides)} (a development "
                                    "override): remove them; the server takes them from System settings"))
            elif extra:
                checks.append(Check(area, "bootstrap.json", WARN, f"unexpected keys: {', '.join(extra)}"))
            else:
                checks.append(Check(area, "bootstrap.json", PASS, "holds only database_url"))
        mode = _mode(bootstrap)
        if mode is not None:
            level = FAIL if mode & 0o007 else WARN if mode & 0o070 else PASS
            checks.append(Check(area, "bootstrap.json mode", level, f"mode {mode:03o}" + (
                ": chmod 600 instance/bootstrap.json (it holds the database password)" if level != PASS else "")))
    elif database.source_file.is_file() and database.config.get("database_url"):
        checks.append(Check(area, "bootstrap.json", WARN, f"the database URL comes from the legacy "
                            f"{database.source_file.name}: move it into instance/bootstrap.json"))
    else:
        # Without a file, app/db.py read REELFORGE_DATABASE_URL (the connection check says whether it worked).
        checks.append(Check(area, "bootstrap.json", PASS, f"no file: the database URL comes from "
                            f"{database.DATABASE_URL_ENV}"))
    if not legacy:
        checks.append(Check(area, "legacy values", PASS, "no runtime file (/etc/reelforge/runtime.env, .env.runtime)"))
    for path, names in legacy.items():
        label = f"legacy values: {Path(path).name}"
        if names is None:
            checks.append(Check(area, label, WARN, f"{path} exists but cannot be read here; its values still reach "
                                "the services"))
            continue
        unexpected = [name for name in names if name not in ENVIRONMENT_ONLY]
        checks.append(Check(area, label, WARN if unexpected else PASS,
                            f"{path}: {len(unexpected)} legacy value(s) ({', '.join(unexpected[:10])}"
                            f"{'…' if len(unexpected) > 10 else ''}): move them to Admin → System settings, then "
                            "remove them" if unexpected else f"{path}: only {', '.join(names) or 'empty values'}"))
    if db is not None:
        checks += _guarded(area, lambda: _origin_checks(db, expect_origin))
    raw = str(system_config.get("security.trusted_proxies") or "")
    networks, invalid = [], []
    for part in re.split(r"[,\s]+", raw.strip()):
        if part:
            try:
                networks.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                invalid.append(part)
    broad = [str(network) for network in networks if network.prefixlen == 0]
    if broad or invalid:
        checks.append(Check(area, "trusted proxies", FAIL, f"{', '.join(broad)} trusts every address: anyone could "
                            "forge a client address" if broad else f"invalid entries: {', '.join(invalid)}"))
    else:
        checks.append(Check(area, "trusted proxies", PASS if networks else WARN, raw if networks else
                            "none: every request shows the tunnel's address (Admin → System settings → Security)"))
    header = str(system_config.get("security.client_ip_header") or "")
    checks.append(Check(area, "client address header", PASS if header.lower() == "cf-connecting-ip" else WARN,
                        header if header.lower() == "cf-connecting-ip"
                        else f"{header or 'none'}: Cloudflare sends CF-Connecting-IP"))
    checks.append(Check(area, "public HTTPS", MANUAL, "through Cloudflare: HTTPS, HSTS, CSP, nosniff, Referrer-Policy, "
                        "cookie flags, a foreign Origin refused: record security_headers and security_foreign_origin"))
    return checks


def _origin_checks(db, expect_origin: str | None) -> list[Check]:
    """The public origin and Secure cookies as System Settings store them (Admin → System settings → General)."""
    area = "SYSTEM CONFIG"
    from app.models import SystemSetting

    def stored(key):
        row = db.get(SystemSetting, key)
        return json.loads(row.value) if row else None

    checks = []
    origin = stored("frontend_origin")
    if origin is None:
        checks.append(Check(area, "public origin", WARN, "not stored yet: the API stores "
                            "https://reelforge.mul-service.com when it starts"))
    else:
        origin = str(origin).rstrip("/")
        host = (urlsplit(origin).hostname or "").lower()
        public = origin.startswith("https://") and host not in ("localhost", "127.0.0.1", "::1") \
            and not host.endswith(".localhost")
        if expect_origin and origin != expect_origin.rstrip("/"):
            checks.append(Check(area, "public origin", FAIL, f"{origin}, not {expect_origin.rstrip('/')}"))
        else:
            checks.append(Check(area, "public origin", PASS if public else FAIL, origin if public else
                                f"{origin}: the server needs its public https origin "
                                "(Admin → System settings → General)"))
    secure = stored("secure_cookies")
    checks.append(Check(area, "secure cookies", PASS if secure is True else FAIL, "on" if secure is True
                        else "off: turn Secure cookies on (Admin → System settings → General)"))
    if origin is not None:
        checks.append(_redirect_check(origin))
    return checks


# The OAuth redirect overrides (Admin → System settings → Social OAuth, else a legacy environment variable).
REDIRECT_OVERRIDES = (("youtube", "GOOGLE_OAUTH_REDIRECT_URI"), ("tiktok", "TIKTOK_REDIRECT_URI"),
                      ("facebook", "FACEBOOK_REDIRECT_URI"))


def _redirect_check(origin: str) -> Check:
    """An override on another origin (the old domain, say) sends OAuth sign-ins where the API refuses them."""
    from app import system_config

    elsewhere = []
    for channel, name in REDIRECT_OVERRIDES:
        override = system_config.env(name).strip()
        if override and not override.startswith(origin + "/"):
            elsewhere.append(f"{channel} {override}")
    if elsewhere:
        return Check("SYSTEM CONFIG", "OAuth redirects", WARN, f"{'; '.join(elsewhere)}: not under {origin}; update "
                     "or clear the override (Admin → System settings → Social OAuth, or the legacy runtime file)")
    return Check("SYSTEM CONFIG", "OAuth redirects", PASS, f"under {origin} (derived, or overrides that match)")


def account_checks(db, probe: Probe) -> list[Check]:
    area = "SECURITY"
    from sqlalchemy import func, select

    from app.models import User

    checks = []
    users = db.scalar(select(func.count()).select_from(User)) or 0
    checks.append(Check(area, "first-run setup", PASS if users else FAIL, "closed (accounts exist)" if users
                        else "open: create the administrator on this server: (cd frontend && npm run create-admin)"))
    admins = db.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(True))) or 0
    checks.append(Check(area, "first administrator", PASS if admins else FAIL,
                        f"{admins} system administrator account(s)" if admins else "no system administrator"))
    active = db.scalars(select(User).where(User.is_admin.is_(True), User.is_active.is_(True))).all()
    checks.append(Check(area, "active administrator", PASS if active else FAIL, f"{len(active)} active" if active
                        else "none active: .venv/bin/python -m app.account_recovery (docs/SECURITY.md)"))
    without = [admin for admin in active if not admin.totp_enabled_at]
    if active:
        checks.append(Check(area, "administrator 2FA", WARN if without else PASS,
                            f"{len(without)} of {len(active)} without two-factor authentication" if without
                            else "every active administrator uses it"))
    code, _ = probe.run([sys.executable, "-m", "app.account_recovery", "--help"])
    checks.append(Check(area, "break-glass command", PASS if code == 0 else WARN,
                        "python -m app.account_recovery --help runs" if code == 0
                        else "python -m app.account_recovery --help failed"))
    return checks


def _guarded(area: str, run) -> list[Check]:
    """An area that cannot run (a schema not migrated, a missing tool) is a FAIL line, never a crash."""
    try:
        return run()
    except Exception as exc:  # noqa: BLE001 - reported without its message, which could carry a value
        return [Check(area, "check", FAIL, f"could not run ({type(exc).__name__})")]


def _with_database(area: str, db_ok: bool, run) -> list[Check]:
    """``run(db)`` in its own session, so that one area's error never poisons the next one's transaction."""
    if not db_ok:
        return [Check(area, "database", FAIL, "not checked: no database connection")]

    def go():
        from app.db import Session

        with Session() as db:
            return run(db)
    return _guarded(area, go)


def preflight(probe: Probe | None = None, *, expect_commit: str | None = None, expect_origin: str | None = None,
              now: datetime | None = None) -> list[Check]:
    probe = probe or Probe()
    now = now or datetime.now(timezone.utc)
    legacy = mirror_service_environment()
    from app import system_config

    system_config.activate()
    checks = _guarded("SOURCE", lambda: source_checks(probe, expect_commit))
    database, db_ok = database_checks()
    checks += database
    checks += _guarded("MASTER KEY", lambda: master_key_checks(db_ok))
    checks += _guarded("SERVICES", lambda: service_checks(probe, db_ok))
    checks += _guarded("PORTS", lambda: port_checks(probe))
    checks += _guarded("HEALTH", lambda: health_checks(probe))
    checks += _with_database("STORAGE", db_ok, storage_checks)
    checks += _with_database("BACKUPS", db_ok, lambda db: backup_checks(db, probe, now))
    checks += _guarded("FFMPEG", lambda: ffmpeg_checks(probe))
    if db_ok:
        checks += _with_database("SYSTEM CONFIG", db_ok, lambda db: config_checks(db, legacy, expect_origin))
    else:
        checks += _guarded("SYSTEM CONFIG", lambda: config_checks(None, legacy, expect_origin))
    checks += _with_database("SECURITY", db_ok, lambda db: account_checks(db, probe))
    hidden = _secrets_to_hide()
    return [Check(check.area, check.name, check.status, scrub(check.detail, hidden)) for check in checks]


# --- report ------------------------------------------------------------------------------------------------

def recorded_gates(db) -> tuple[list[dict], dict]:
    """The release gates as administrators recorded them in Admin → Verification (never set here)."""
    from sqlalchemy import select

    from app import readiness
    from app.models import User, VerificationCheck

    rows = {row.key: row for row in db.scalars(select(VerificationCheck))}
    emails = dict(db.execute(select(User.id, User.email).where(
        User.id.in_([row.verified_by_user_id for row in rows.values() if row.verified_by_user_id]))).all())
    items = []
    for key, group, paid, how in readiness.CHECKLIST:
        row = rows.get(key)
        status = (row.status if row else None) or "not_checked"
        recorded = row is not None and row.status is not None
        items.append({"key": key, "group": group, "paid": paid, "optional": key in readiness.OPTIONAL,
                      "status": status,
                      "recorded_by": emails.get(row.verified_by_user_id) if recorded else None,
                      "recorded_at": row.verified_at.isoformat() if recorded and row.verified_at else None,
                      "note": row.note if row else None, "how": how})
    return items, readiness.checklist_summary({item["key"]: item["status"] for item in items})


def readiness_attention(db) -> dict:
    """Readiness checks (Admin → Verification) that are not ok.

    This process's own environment and stream count say nothing about the services: those sections are left out."""
    from app import readiness

    data = readiness.report(db, streams=0, poll_seconds=0)
    attention = [{"section": section["key"], "check": check["key"], "status": check["status"],
                  "detail": check.get("detail")}
                 for section in data["sections"] if section["key"] not in ("configuration", "realtime", "support")
                 for check in section["checks"] if check["status"] not in ("ok", "off")]
    return {"checked_at": data["checked_at"], "attention": attention}


def github_ci(probe: Probe, commit: str) -> dict:
    """What GitHub's check runs say about this exact commit (public API, read-only, no token)."""
    code, remote = probe.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"])
    match = _GITHUB.search(remote.strip()) if code == 0 else None
    if not match or not commit:
        return {"status": "unknown", "detail": "no GitHub origin remote or no commit"}
    owner, repository = match.groups()
    name = f"{owner}/{repository}"
    status, body = probe.fetch(f"https://api.github.com/repos/{name}/commits/{commit}/check-runs?per_page=100",
                               headers={"Accept": "application/vnd.github+json",
                                        "User-Agent": "reelforge-release-report"}, timeout=15, local=False)
    if status != 200:
        return {"status": "unknown", "repository": name, "detail": f"GitHub answered {_http(status)}"}
    runs = _json(body).get("check_runs") or []
    jobs = [{"name": str(run.get("name")), "status": str(run.get("status")), "conclusion": run.get("conclusion")}
            for run in runs if isinstance(run, dict)]
    if not jobs:
        overall = "none"
    elif any(job["status"] != "completed" for job in jobs):
        overall = "pending"
    elif any(job["conclusion"] not in ("success", "skipped", "neutral") for job in jobs):
        overall = "failure"
    else:
        overall = "success"
    return {"status": overall, "repository": name, "commit": commit, "jobs": jobs,
            "url": f"https://github.com/{name}/commit/{commit}/checks"}


def verdict(checks: list[Check], gates: list[dict] | None, summary: dict | None, ci: dict) -> tuple[str, list[str]]:
    """READY_FOR_TAG only without a pre-flight FAIL, with CI not failing on this commit and every gate decided.

    CI that GitHub could not be asked about ("unknown", "not_checked") leaves the decision to the recorded
    release_ci_green gate; a CI run that failed, is still running or never ran on this commit blocks."""
    blockers = [f"pre-flight FAIL: {check.area} / {check.name}: {check.detail}"
                for check in checks if check.status == FAIL]
    if ci.get("status") in ("failure", "pending", "none"):
        failing = [job["name"] for job in ci.get("jobs", [])
                   if job.get("conclusion") not in ("success", "skipped", "neutral")]
        blockers.append(f"CI on this commit: {ci['status']}" + (f" ({', '.join(failing[:8])})" if failing else ""))
    if summary is None or gates is None:
        blockers.append("release gates: not readable (no database connection)")
    else:
        status = {item["key"]: item["status"] for item in gates}
        for state, label in (("failed", "failed"), ("not_applicable", "not applicable but required"),
                             ("not_checked", "not checked")):
            keys = [key for key in summary["open"] if status.get(key, "not_checked") == state]
            if keys:
                blockers.append(f"gates {label} ({len(keys)}): {', '.join(keys)}")
    return (READY if not blockers else CANDIDATE), blockers


def report(probe: Probe | None = None, *, network: bool = True, expect_commit: str | None = None,
           expect_origin: str | None = None, now: datetime | None = None) -> dict:
    probe = probe or Probe()
    checks = preflight(probe, expect_commit=expect_commit, expect_origin=expect_origin, now=now)
    commit = next((check.detail for check in checks if check.area == "SOURCE" and check.name == "commit"
                   and check.status == PASS), "")
    branch = next((check.detail for check in checks if check.area == "SOURCE" and check.name == "branch"), "")
    gates, summary, attention = None, None, None
    try:
        from app.db import Session

        with Session() as db:
            gates, summary = recorded_gates(db)
            attention = readiness_attention(db)
    except Exception:  # noqa: BLE001 - the verdict then says the gates could not be read
        gates, summary = (gates, summary) if summary is not None else (None, None)
    ci = github_ci(probe, commit) if network else {"status": "not_checked", "detail": "--no-network"}
    result, blockers = verdict(checks, gates, summary, ci)
    hidden = _secrets_to_hide()
    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "host": socket.gethostname(), "commit": commit, "branch": branch,
        "verdict": result, "blockers": [scrub(item, hidden) for item in blockers],
        "preflight": {"counts": counts(checks), "checks": [asdict(check) for check in checks]},
        "readiness": attention, "ci": ci, "gates": {"summary": summary, "items": gates},
    }


# --- output ------------------------------------------------------------------------------------------------

def render_preflight(checks: list[Check], *, now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"ReelForge release pre-flight · {moment} · {socket.gethostname()} · as {_user()}", ""]
    width = max((len(check.name) for check in checks), default=0) + 2
    area = None
    for check in checks:
        if check.area != area:
            area = check.area
            lines.append(area)
        lines.append(f"  {check.status:<7}{check.name:<{width}}{check.detail}")
    found = counts(checks)
    lines += ["", " · ".join(f"{found[level]} {level}" for level in LEVELS)]
    lines.append("Result: FAIL (fix the FAIL lines, exit status 1)" if found[FAIL]
                 else "Result: no FAIL (MANUAL lines are for a person: record them in Admin → Verification)")
    return "\n".join(lines)


GATE_LEVEL = {"passed": PASS, "failed": FAIL, "not_applicable": "N/A", "not_checked": MANUAL}


def render_report(data: dict) -> str:
    lines = [f"ReelForge release report · {data['generated_at']} · {data['host']}",
             f"Commit {data['commit'] or 'unknown'} ({data['branch'] or 'unknown branch'})", "",
             f"Verdict: {data['verdict']}"]
    if data["blockers"]:
        lines.append(f"  {len(data['blockers'])} blocker(s):")
        lines += [f"    - {item}" for item in data["blockers"][:80]]
        if len(data["blockers"]) > 80:
            lines.append(f"    … and {len(data['blockers']) - 80} more")
    found = data["preflight"]["counts"]
    lines += ["", "Pre-flight: " + " · ".join(f"{found[level]} {level}" for level in LEVELS)
              + "  (bash deploy/release-preflight.sh shows every line)"]
    lines += [f"  {check['status']:<7}{check['area']} / {check['name']}: {check['detail']}"
              for check in data["preflight"]["checks"] if check["status"] in (FAIL, WARN)]
    if data["readiness"] is not None:
        lines += ["", "Readiness needing attention (Admin → Verification):"]
        lines += [f"  {item['status']:<8}{item['section']} / {item['check']}"
                  + (f": {item['detail']}" if item["detail"] else "") for item in data["readiness"]["attention"]] \
            or ["  none"]
    ci = data["ci"]
    lines += ["", f"CI on this commit: {ci['status']}" + (f" · {ci['repository']}" if ci.get("repository") else "")
              + (f" · {ci['detail']}" if ci.get("detail") else "")]
    lines += [f"  {(job['conclusion'] or job['status']):<10}{job['name']}" for job in ci.get("jobs", [])]
    if ci.get("url"):
        lines.append(f"  {ci['url']}")
    gates, summary = data["gates"]["items"], data["gates"]["summary"]
    lines.append("")
    if summary is None:
        lines.append("Release gates: not readable (no database connection)")
    else:
        lines.append(f"Release gates (Admin → Verification): {summary['passed']} passed · "
                     f"{summary['failed']} failed · {summary['not_applicable']} not applicable · "
                     f"{summary['not_checked']} not checked (of {summary['total']}); only what an administrator "
                     "recorded counts")
        group = None
        for item in gates:
            if item["group"] != group:
                group = item["group"]
                lines.append(f"  {group}")
            who = f" · {item['recorded_by']} {item['recorded_at'][:16]}" if item["recorded_at"] else ""
            note = f" · {item['note']}" if item["note"] else ""
            lines.append(f"    {GATE_LEVEL[item['status']]:<7}{item['key']}{who}{note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.release_check",
        description="ReelForge release pre-flight and report (read-only; never prints a secret)")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("preflight", "check this server (exit status 1 when a check FAILs)"),
                       ("report", "pre-flight, recorded release gates, CI and the verdict "
                                  "(exit status 0 only when READY_FOR_TAG)")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--json", action="store_true", help="machine-readable output")
        command.add_argument("--expect-commit", help="FAIL unless HEAD is this commit (the release candidate)")
        command.add_argument("--expect-origin", help="FAIL unless the public origin is this one")
        if name == "report":
            command.add_argument("--no-network", action="store_true", help="do not ask GitHub for the CI result")
    args = parser.parse_args(argv)
    if args.command == "preflight":
        checks = preflight(expect_commit=args.expect_commit, expect_origin=args.expect_origin)
        if args.json:
            print(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "host": socket.gethostname(), "counts": counts(checks),
                              "result": FAIL if counts(checks)[FAIL] else PASS,
                              "checks": [asdict(check) for check in checks]}, indent=2, ensure_ascii=False))
        else:
            print(render_preflight(checks))
        return 1 if counts(checks)[FAIL] else 0
    data = report(network=not args.no_network, expect_commit=args.expect_commit, expect_origin=args.expect_origin)
    print(json.dumps(data, indent=2, ensure_ascii=False) if args.json else render_report(data))
    return 0 if data["verdict"] == READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
