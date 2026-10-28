"""Consistent online backup of the SQLite database.

    python -m app.backup /data/home.db /backups [--keep-days 30] [--prefix home]
                         [--skip-if-missing]

Uses SQLite's backup API (safe while the app is writing), verifies the copy
with an integrity check, then prunes old backups.
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


def backup(db_path: Path, out_dir: Path, keep_days: int, prefix: str = "home") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{prefix}-{datetime.now():%Y%m%dT%H%M}.db"
    tmp = target.with_suffix(".db.tmp")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    src.close()
    result = dst.execute("PRAGMA integrity_check").fetchone()[0]
    dst.close()
    if result != "ok":
        tmp.unlink()
        raise RuntimeError(f"integrity check failed: {result}")
    tmp.rename(target)

    cutoff = time.time() - keep_days * 86400
    for old in out_dir.glob(f"{prefix}-*.db"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
    return target


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("db", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--keep-days", type=int, default=30)
    p.add_argument("--prefix", default="home", help="backup file name prefix")
    # The pre-deploy initContainer runs before the app has ever created the database.
    p.add_argument("--skip-if-missing", action="store_true", help="exit 0 if the database doesn't exist yet")
    args = p.parse_args(argv)
    if args.skip_if_missing and not args.db.exists():
        print(f"no database at {args.db}, nothing to back up")
        return 0
    target = backup(args.db, args.out_dir, args.keep_days, args.prefix)
    print(f"backup written: {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
