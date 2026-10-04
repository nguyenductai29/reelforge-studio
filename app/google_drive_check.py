"""Check the movie sources' Google Drive from the server: ``python -m app.google_drive_check``.

The same test as Admin → System settings → Movie sources → Test connection (app/google_drive.py): the stored
credentials get an access token, the root folder is a writable folder, a tiny text file is uploaded, read back and
deleted permanently. Nothing else in Drive is read or changed. Prints one line per check and the account Drive acts
as; never a client secret, a refresh token, a key or an access token. Exit status 0 when every check passed.

``--config`` only reports whether the settings are complete, without calling Google.
"""
import argparse
import json
import sys

from app import google_drive
from app.runtime_env import start_process


def main(argv: list[str] | None = None, out=print) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.google_drive_check",
                                     description="Test the movie sources' Google Drive connection")
    parser.add_argument("--config", action="store_true", help="Only check that the settings are complete")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON")
    args = parser.parse_args(argv)
    start_process("google_drive_check")
    settings = google_drive.config()
    if args.config:
        problem = settings.problem()
        result = {"status": "error" if problem else "ok",
                  "checks": [{"key": "configuration", "status": "error" if problem else "ok",
                              **({"code": problem} if problem else {})}]}
    else:
        result = google_drive.check_connection(settings)
    if args.json:
        out(json.dumps(result, indent=2))
    else:
        out(f"Google Drive ({settings.auth_mode}, delete mode {settings.delete_mode}):")
        for check in result["checks"]:
            extra = f" ({check['code']})" if check.get("code") else ""
            account = f" as {check['account']}" if check.get("account") else ""
            out(f"  {check['key']:<14} {check['status']}{extra}{account}")
        out("Drive ready." if result["status"] == "ok" else "Drive NOT ready.")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
