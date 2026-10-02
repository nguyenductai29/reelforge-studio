"""tests/test_youtube_worker_retry.py, run where it is safe: a disposable copy of the app with its own SQLite database.

Importing ``app.youtube_worker`` imports ``app.db`` and ``app.main``, which open the configured database while they
are imported. In place, that database is CI's missing one (every backend job failed on that import) or a developer's
real one. The retry tests skip themselves outside the copy; this test builds the copy, migrates it and runs them.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = "test_youtube_worker_retry"


def copy_of_the_app(target: Path, *, database: bool) -> dict:
    """The app, its migrations and the retry tests in ``target``; with ``database``, a SQLite database of its own."""
    for folder in ("app", "migrations"):
        shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
    (target / "tests").mkdir()
    shutil.copy(ROOT / "tests" / f"{MODULE}.py", target / "tests" / f"{MODULE}.py")
    (target / "instance").mkdir()
    if database:
        (target / "instance" / "bootstrap.json").write_text(
            json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
    # Nothing from this machine's configuration reaches the copy: the tests set what they need themselves.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("REELFORGE_", "GOOGLE_"))}
    return {**env, "PYTHONPATH": str(target), "PYTHONIOENCODING": "utf-8"}


class YouTubeWorkerRetryInACopy(unittest.TestCase):
    def test_the_retry_tests_pass_in_a_disposable_copy(self):
        expected = len(re.findall(r"(?m)^    def test_", (ROOT / "tests" / f"{MODULE}.py").read_text(encoding="utf-8")))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            env = {**copy_of_the_app(target, database=True), "REELFORGE_ISOLATED_COPY": "1"}
            migrated = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=target, env=env,
                                      capture_output=True, text=True, encoding="utf-8", timeout=300)
            self.assertEqual(migrated.returncode, 0, migrated.stderr[-4000:])
            result = subprocess.run([sys.executable, "-m", "unittest", f"tests.{MODULE}", "-v"], cwd=target, env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=600)
        report = result.stdout[-3000:] + result.stderr[-6000:]
        self.assertEqual(result.returncode, 0, report)
        self.assertIn(f"Ran {expected} tests", result.stderr, report)
        self.assertNotIn("skipped", result.stderr.splitlines()[-1], report)  # every test ran, none skipped

    def test_outside_the_copy_the_module_skips_instead_of_importing_the_app(self):
        """What CI does: no instance/bootstrap.json, no database URL. The module must load, and skip."""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            env = copy_of_the_app(target, database=False)
            results = [subprocess.run([sys.executable, "-m", "unittest", *arguments], cwd=target, env=env,
                                      capture_output=True, text=True, encoding="utf-8", timeout=300)
                       for arguments in ((f"tests.{MODULE}", "-v"), ("discover", "-s", "tests", "-p", f"{MODULE}.py"))]
        for result in results:
            report = result.stdout[-2000:] + result.stderr[-4000:]
            self.assertEqual(result.returncode, 0, report)
            self.assertRegex(result.stderr.strip().splitlines()[-1], r"^OK \(skipped=\d+\)$", report)
            self.assertNotIn("bootstrap.json", result.stderr, report)  # app.db was never imported


if __name__ == "__main__":
    unittest.main()
