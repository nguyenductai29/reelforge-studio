"""Turn the failures of a ``python -m unittest -v`` run into GitHub Actions annotations.

    python -m unittest discover -s tests -v 2>&1 | tee unittest.log
    python tests/ci_annotate.py unittest.log        # in a step that runs only on failure

A job log can only be read with rights on the repository; annotations appear on the run page and through the
public checks API. Each FAIL or ERROR becomes one ``::error`` annotation (at most ten) with the test's name and the
end of its traceback, so a failure on CI's Linux machines is visible to whoever looks at the run.
"""
from pathlib import Path
import re
import sys

SEPARATOR = "=" * 70
BLOCK = re.compile(rf"^{SEPARATOR}\n(FAIL|ERROR): (\S+) \(([^)\n]+)\)\n-{{70}}\n(.*?)(?=^{SEPARATOR}\n|^-{{70}}\nRan )",
                   re.M | re.S)
LIMIT = 10


def _message(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _property(text: str) -> str:
    return _message(text).replace(":", "%3A").replace(",", "%2C")


def annotations(log: str) -> list[str]:
    found = []
    for kind, test, where, body in BLOCK.findall(log.replace("\r\n", "\n")):
        lines = [line for line in body.strip().splitlines() if line.strip()]
        excerpt = "\n".join(lines[-30:])[-3000:]
        found.append(f"::error title={_property(f'{kind} {where}')}::{_message(excerpt)}")
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: python tests/ci_annotate.py unittest.log", file=sys.stderr)
        return 2
    path = Path(args[0])
    log = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    found = annotations(log)
    for line in found[:LIMIT]:
        print(line)
    if not found:
        summary = next((line for line in reversed(log.splitlines()) if line.startswith(("FAILED", "OK", "Ran "))), "")
        print(f"::error title=unittest::No FAIL/ERROR block found ({_message(summary or 'no summary line')})")
    print(f"{len(found)} failing test(s); {min(len(found), LIMIT)} annotated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
