"""A checksum manifest of the media worth keeping (Phase 25): ``python -m app.media_manifest``.

Media can be large; the database dump does not contain it, and copying every
intermediate file to the same disk protects against nothing. The manifest lists the
files that matter, by default final renders and uploaded sources, with their size and
SHA-256, so that a copy kept elsewhere (an external disk, a NAS) can be verified, and a
restore can show exactly what is missing.

    python -m app.media_manifest create --output /srv/data/backups/reelforge/media-manifest.jsonl
    python -m app.media_manifest verify --manifest media-manifest.jsonl [--root /mnt/nas/reelforge-media]

One JSON object per line: asset id, workspace id, kind, bytes, sha256 and the path
relative to the media root (``<workspace>/<asset>``). No titles or user text.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from sqlalchemy import select

DEFAULT_KINDS = ("final_render", "source")
CHUNK = 1 << 20


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def create(db, output: Path, *, kinds: tuple[str, ...] = DEFAULT_KINDS) -> dict:
    from app.models import Asset
    from app.storage import media_root

    root = media_root(db)
    query = select(Asset).where(Asset.bytes > 0)
    if kinds:
        query = query.where(Asset.kind.in_(kinds))
    written = missing = total = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for asset in db.scalars(query.order_by(Asset.created_at)):
            relative = f"{asset.workspace_id}/{asset.id}"
            path = root / relative
            if not path.is_file():
                missing += 1
                continue
            handle.write(json.dumps({"asset_id": asset.id, "workspace_id": asset.workspace_id, "kind": asset.kind,
                                     "bytes": path.stat().st_size, "sha256": sha256(path), "path": relative}) + "\n")
            written += 1
            total += path.stat().st_size
    return {"root": str(root), "files": written, "bytes": total, "missing_on_disk": missing, "output": str(output)}


def verify(manifest: Path, root: Path) -> dict:
    checked = ok = 0
    problems = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        checked += 1
        path = root / item["path"]
        if not path.is_file():
            problems.append({"asset_id": item["asset_id"], "problem": "missing"})
        elif path.stat().st_size != item["bytes"]:
            problems.append({"asset_id": item["asset_id"], "problem": "size"})
        elif sha256(path) != item["sha256"]:
            problems.append({"asset_id": item["asset_id"], "problem": "checksum"})
        else:
            ok += 1
    return {"root": str(root), "checked": checked, "ok": ok, "problems": problems[:200],
            "problem_count": len(problems)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checksum manifest of ReelForge media worth keeping")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("create")
    make.add_argument("--output", required=True)
    make.add_argument("--kinds", default=",".join(DEFAULT_KINDS),
                      help="Asset kinds, comma separated (empty: every stored file)")
    check = commands.add_parser("verify")
    check.add_argument("--manifest", required=True)
    check.add_argument("--root", help="Where the copy lives (default: the media root)")
    args = parser.parse_args(argv)

    from app.runtime_env import start_process

    start_process("media_manifest")
    from app.db import Session
    from app.storage import media_root

    with Session() as db:
        if args.command == "create":
            kinds = tuple(kind.strip() for kind in args.kinds.split(",") if kind.strip())
            result = create(db, Path(args.output), kinds=kinds)
        else:
            result = verify(Path(args.manifest), Path(args.root) if args.root else media_root(db))
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0 if args.command == "create" or not result["problem_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
