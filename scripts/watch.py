#!/usr/bin/env python3
"""
watch.py - rebuild the database whenever the CSV or the codebook changes.

Run it in a second terminal beside `bundle exec jekyll serve`. Jekyll's own
watcher picks up the regenerated files in _data/, _sites/ and assets/data/ and
reloads the page.

    python3 scripts/watch.py
    python3 scripts/watch.py --interval 1 --jekyll     # also start jekyll serve

Uses watchdog if it is installed, otherwise polls file hashes. Polling two small
files once a second costs nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHED = [ROOT / "data" / "database_preliminary.csv",
           ROOT / "scripts" / "codebook.yml"]


def digest(paths) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.name.encode())
        if p.exists():
            h.update(str(p.stat().st_mtime_ns).encode())
            h.update(str(p.stat().st_size).encode())
        else:
            h.update(b"missing")
    return h.hexdigest()


def rebuild() -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] change detected, rebuilding", flush=True)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_db.py"), "--force", "--site-pages"],
        cwd=ROOT)
    if result.returncode == 0:
        print(f"[{time.strftime('%H:%M:%S')}] rebuild complete", flush=True)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] rebuild FAILED, keeping the previous data",
              flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=1.0, help="poll interval in seconds")
    ap.add_argument("--jekyll", action="store_true", help="also start `jekyll serve`")
    ap.add_argument("--no-initial", action="store_true", help="do not build once at startup")
    args = ap.parse_args()

    if not args.no_initial:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_db.py"),
                        "--site-pages"], cwd=ROOT)

    jekyll = None
    if args.jekyll:
        cmd = (["bundle", "exec", "jekyll", "serve", "--livereload"]
               if shutil.which("bundle") else ["jekyll", "serve", "--livereload"])
        print("starting: " + " ".join(cmd), flush=True)
        jekyll = subprocess.Popen(cmd, cwd=ROOT)

    def shutdown(*_):
        if jekyll and jekyll.poll() is None:
            jekyll.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"watching {', '.join(p.name for p in WATCHED)} (ctrl-c to stop)", flush=True)
    last = digest(WATCHED)
    try:
        while True:
            time.sleep(args.interval)
            current = digest(WATCHED)
            if current != last:
                # let a large save finish before reading the file
                time.sleep(0.3)
                current = digest(WATCHED)
                rebuild()
                last = current
            if jekyll and jekyll.poll() is not None:
                print("jekyll exited; stopping the watcher", flush=True)
                return jekyll.returncode
    finally:
        if jekyll and jekyll.poll() is None:
            jekyll.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
