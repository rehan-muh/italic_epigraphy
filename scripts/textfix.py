#!/usr/bin/env python3
"""
textfix.py - repair the mixed character encodings in database_preliminary.csv.

The CSV is not in one encoding. Four regimes occur in the same file, sometimes
in the same row:

    valid UTF-8            b"V\\xc3\\xa9n\\xc3\\xa8te"           -> Vénète
    double-encoded UTF-8   b"Magr\\xc3\\x83\\xc2\\xa8"           -> Magrè
    raw cp1252/latin-1     b"Magr\\xe8"                       -> Magrè
    destroyed bytes        b"Citt\\xef\\xbf\\xbd"                -> U+FFFD, unrecoverable

Deciding one encoding for the whole file, as a single call to open(), corrupts
whichever fields are not in it. So the file is read as latin-1, which is a
lossless byte-to-codepoint map, and every field is repaired individually:
re-encode to bytes, then try UTF-8 and fall back to cp1252, repeating while the
result still carries a mojibake signature.

The fourth case cannot be repaired by any decoder, because the original bytes
were replaced with U+FFFD before this file was ever written. Those strings are
listed in scripts/text_repairs.yml and substituted by hand. Anything containing
U+FFFD that is not in that file is reported as a warning by build_db.py.

    python3 scripts/textfix.py data/database_preliminary.csv    # audit only
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# "Ã©", "Â ", "â€™": a Latin-1 lead byte followed by a UTF-8 continuation byte.
# Real Italian, Latin or German text does not produce these sequences.
MOJIBAKE = re.compile(r"[\u00c2\u00c3\u00c5\u00ce\u00d0][\u0080-\u00bf]|\u00e2\u0080[\u0080-\u00bf]")

REPAIRS_FILE = Path(__file__).resolve().parent / "text_repairs.yml"


def _load_repairs() -> dict[str, str]:
    if not REPAIRS_FILE.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    with REPAIRS_FILE.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    table = raw.get("replace") or {}
    return {str(k): str(v) for k, v in table.items()}


REPAIRS = _load_repairs()


def fix(value: str) -> str:
    """Repair one field read from a latin-1 decode of the raw bytes."""
    if not value or value.isascii():
        return value

    out = value
    for _ in range(3):
        try:
            raw = out.encode("latin-1")
        except UnicodeEncodeError:
            break                      # already repaired past the byte range
        try:
            candidate = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                candidate = raw.decode("cp1252")
            except UnicodeDecodeError:
                break
            if candidate != out:
                out = candidate
            break
        if candidate == out:
            break
        out = candidate
        if not MOJIBAKE.search(out):
            break

    return REPAIRS.get(out, out)


def unrepaired(value: str) -> bool:
    """True if the string still holds a replacement character after fix()."""
    return "\ufffd" in value


def audit(path: Path) -> int:
    """Print every field that fix() cannot fully recover. Used by hand."""
    import collections
    import csv
    import io

    text = path.read_bytes().decode("latin-1")
    rows = csv.reader(io.StringIO(text, newline=""))
    header = next(rows)
    counts: collections.Counter = collections.Counter()
    touched = 0
    for record in rows:
        for column, value in zip(header, record):
            repaired = fix(value)
            if repaired != value:
                touched += 1
            if unrepaired(repaired):
                counts[(column, repaired)] += 1

    print(f"{touched} field(s) changed by repair")
    if not counts:
        print("no unrecoverable fields remain")
        return 0
    print(f"{len(counts)} distinct unrecoverable string(s):")
    for (column, value), n in counts.most_common():
        print(f"  {n:5d}  {column:20s}  {value!r}")
    print("\nAdd them to scripts/text_repairs.yml under 'replace:'.")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).resolve().parent.parent / "data" / "database_preliminary.csv")
    raise SystemExit(audit(target))
