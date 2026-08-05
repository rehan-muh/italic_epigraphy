#!/usr/bin/env python3
"""
build_db.py - turn database_preliminary.csv into a normalised SQLite database,
a plain-text SQL dump, and the JSON payloads the Jekyll site reads.

The CSV is the single source of truth. Everything under db/, assets/data/ and
_data/atlas.yml is generated and can be deleted at any time.

    python3 scripts/build_db.py                 # rebuild only if the CSV changed
    python3 scripts/build_db.py --force         # rebuild unconditionally
    python3 scripts/build_db.py --site-pages    # also write _sites/*.md gazetteer stubs
    python3 scripts/build_db.py --check         # exit 1 if a rebuild is needed

Requires: python 3.9 or newer and pyyaml. No other dependencies, no network access.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml is required: python -m pip install pyyaml")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import textfix  # noqa: E402  (same directory)

SCRIPT_VERSION = "2.0.0"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "database_preliminary.csv"
LINKS_CSV = ROOT / "data" / "links.csv"
DB_DIR = ROOT / "db"
DATA_DIR = ROOT / "assets" / "data"
JEKYLL_DATA = ROOT / "_data"
SITES_DIR = ROOT / "_sites"
STAMP = DB_DIR / ".build-stamp.json"

# CSV header -> SQLite column
COLUMN_MAP = {
    "ID_Internal": "id_internal",
    "Reference": "reference",
    "Region": "region",
    "Groundedness": "groundedness",
    "Specified.Location": "location",
    "Pleiades": "coord_flag",
    "Latitude": "latitude",
    "Longitude": "longitude",
    "Date.Start": "date_start",
    "Date.End": "date_end",
    "Date.Start.Orig": "date_start_orig",
    "Date.End.Orig": "date_end_orig",
    "Object.Type": "object_type",
    "Inscription.Use": "use",
    "Alphabet": "alphabet",
    "Alphabet.Direction": "direction",
    "Language": "language",
    "Notes": "notes",
    "Script": "script",
}

INT_COLS = ("id_internal", "groundedness", "coord_flag", "alphabet",
            "date_start", "date_end", "date_start_orig", "date_end_orig")
FLOAT_COLS = ("latitude", "longitude")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Table:
    """A column-major table of Python objects.

    Deliberately not a DataFrame. The whole job here is reading a CSV and
    handing typed columns to SQLite, and doing that with the standard library
    keeps the build free of compiled dependencies, which in turn keeps it
    working across Python versions without waiting for anyone's wheels.
    """

    __slots__ = ("cols", "order")

    def __init__(self, order):
        self.order = list(order)
        self.cols = {name: [] for name in self.order}

    def __getitem__(self, key):
        return self.cols[key]

    def __setitem__(self, key, values):
        if key not in self.cols:
            self.order.append(key)
        self.cols[key] = values

    def __len__(self):
        return len(self.cols[self.order[0]]) if self.order else 0

    def rows(self):
        for i in range(len(self)):
            yield {name: self.cols[name][i] for name in self.order}

    def keep(self, mask):
        """Retain the rows where mask[i] is true, in place."""
        for name in self.order:
            column = self.cols[name]
            self.cols[name] = [v for v, ok in zip(column, mask) if ok]


def read_csv_repaired(path: Path) -> tuple[list[str], list[list[str]], str, list[str]]:
    """Return (header, rows, encoding_note, warnings).

    The file mixes UTF-8, double-encoded UTF-8 and cp1252 within single rows, so
    there is no file-level encoding to choose. It is read as latin-1, which maps
    bytes to codepoints without loss, and each field is repaired individually by
    scripts/textfix.py. Rows wider than the header are truncated and narrower
    ones padded, so one malformed line cannot abort the build.
    """
    import io

    text = path.read_bytes().decode("latin-1")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None:
        raise SystemExit(f"{path} is empty")
    header = [textfix.fix(h).strip().lstrip("\ufeff") for h in header]
    width = len(header)

    rows: list[list[str]] = []
    repaired = 0
    broken: Counter = Counter()
    for record in reader:
        if len(record) < width:
            record = record + [""] * (width - len(record))
        elif len(record) > width:
            record = record[:width]
        out = []
        for value in record:
            fixed = textfix.fix(value)
            if fixed != value:
                repaired += 1
            if textfix.unrepaired(fixed):
                broken[fixed] += 1
            out.append(fixed)
        rows.append(out)

    warnings = []
    if repaired:
        warnings.append(f"repaired the character encoding of {repaired} field(s)")
    if broken:
        sample = "; ".join(repr(v) for v, _ in broken.most_common(5))
        warnings.append(
            f"{sum(broken.values())} field(s) in {len(broken)} distinct string(s) still "
            f"contain an unrecoverable character: {sample}. "
            f"Add them to scripts/text_repairs.yml")
    return header, rows, "mixed, repaired per field", warnings


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value))
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "unnamed"


def to_int(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return int(round(float(str(v).strip())))
    except (ValueError, TypeError):
        return None


def to_float(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


NULL_TOKENS = {"", "na", "n/a", "nan", "null", "none", "<na>", "-"}


def clean_str(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return None if s.lower() in NULL_TOKENS else s


def year_label(year: int | None) -> str:
    """-500 -> '500 BC', 26 -> 'AD 26'. Year 0 does not exist in this data."""
    if year is None:
        return "undated"
    return f"{abs(year)} BC" if year < 0 else f"AD {year}"


def quarter_bin(year: int | None) -> int | None:
    """Left edge of the quarter-century a year falls in.

    BC years bin away from zero so that a quarter is written descending
    (150-126 BC), matching the convention used elsewhere in the project.
    AD years bin the ordinary way (1-25, 26-50, ...).
    """
    if year is None:
        return None
    if year < 0:
        return -(((-year + 24) // 25) * 25)
    return ((year - 1) // 25) * 25 + 1


def quarter_label(edge: int | None) -> str:
    if edge is None:
        return "undated"
    if edge < 0:
        return f"{abs(edge)}\u2013{abs(edge) - 24} BC"
    return f"AD {edge}\u2013{edge + 24}"


def quarter_range(start: int | None, end: int | None) -> list[int]:
    """Every quarter-century edge an interval touches, earliest first.

    Dates run negative for BC, so the sequence always ascends numerically:
    (-400, -1) yields the sixteen edges from -400 to -25.
    """
    if start is None and end is None:
        return []
    a = quarter_bin(start if start is not None else end)
    b = quarter_bin(end if end is not None else start)
    if a is None or b is None:
        return []
    if a > b:
        a, b = b, a
    edges, cursor = [], a
    while cursor <= b and len(edges) < 400:
        edges.append(cursor)
        if cursor < 0:
            cursor += 25
            if cursor == 0:          # there is no year zero
                cursor = 1
        else:
            cursor += 25
    return edges


def aoristic_weights(start: int | None, end: int | None) -> list[tuple[int, float]]:
    """Spread one record uniformly across the quarters its interval covers.

    Binning on Date.Start alone credits an inscription dated 400 to 1 BC
    entirely to 400-376 BC, which is why the raw histogram spikes at the round
    century edges. Each record instead contributes 1/k to each of the k quarters
    it could belong to, which is the standard aoristic treatment of interval
    dating and sums to exactly one record either way.
    """
    edges = quarter_range(start, end)
    if not edges:
        return []
    w = 1.0 / len(edges)
    return [(e, w) for e in edges]


# --------------------------------------------------------------------------
# place names
# --------------------------------------------------------------------------

# Two facts are buried in the findspot string rather than in a column of their
# own: a trailing "(?)" marks an uncertain attribution, and "[found & written]"
# marks a findspot that is also the place of composition. Both are lifted out so
# that the place itself can be identified across the rows that carry them.
FLAG_UNCERTAIN = re.compile(r"\s*\(\s*\?\s*\)\s*$|\s*\?\s*$")
FLAG_FOUND_WRITTEN = re.compile(r"\s*\[\s*found\s*&\s*written\s*\]\s*", re.I)

TOMB_WORDS = re.compile(
    r"^(t\b|t\.|tomba|tomb\b|tumulo|sepolcr|grave\b|hypogeum)", re.I)
NECROPOLIS_WORDS = re.compile(r"necropol|cimitero|cemeter|sepolcret", re.I)


def split_place_flags(name: str) -> tuple[str, bool, bool]:
    found_written = bool(FLAG_FOUND_WRITTEN.search(name))
    clean = FLAG_FOUND_WRITTEN.sub(" ", name)
    uncertain = bool(FLAG_UNCERTAIN.search(clean))
    clean = FLAG_UNCERTAIN.sub("", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" ,-")
    return clean or name.strip(), uncertain, found_written


def name_variants(name: str) -> set[str]:
    """Normalised alternative names carried by one findspot string.

    "Palestrina / Praeneste" is the same place as "Palestrina"; "Ateste (Este)"
    is the same place as "Este". Slash-separated alternatives and parenthetical
    glosses are therefore both treated as names of the thing, which is what lets
    the two records merge.
    """
    parts = [name]
    for chunk in re.split(r"\s*/\s*", name):
        parts.append(chunk)
        for inner in re.findall(r"\(([^)]*)\)", chunk):
            parts.append(inner)
        parts.append(re.sub(r"\s*\([^)]*\)", "", chunk))
    out = set()
    for p in parts:
        key = slugify(p)
        if key and key != "unnamed" and len(key) > 2:
            out.add(key)
    return out


def place_level(name: str) -> str:
    """site, necropolis, tomb or locality, inferred from the findspot string."""
    tail = re.split(r"\s*[,-]\s*", name)[-1].strip()
    if TOMB_WORDS.match(tail) or TOMB_WORDS.match(name):
        return "tomb"
    if NECROPOLIS_WORDS.search(name):
        return "necropolis"
    if "," in name or re.search(r"\s-\s", name):
        return "locality"
    return "site"


def load_codebook() -> dict:
    path = Path(__file__).resolve().parent / "codebook.yml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def label_for(codebook: dict, field: str, code, fallback_prefix: str) -> str:
    table = codebook.get(field) or {}
    for key in (code, to_int(code), str(code)):
        if key in table and str(table[key]).strip():
            return str(table[key]).strip()
    return f"{fallback_prefix} {code}"


def hue_color(index: int, total: int) -> str:
    hue = (index * 137.508) % 360
    import colorsys
    r, g, b = colorsys.hls_to_rgb(hue / 360.0, 0.55, 0.48)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


# --------------------------------------------------------------------------
# load and clean
# --------------------------------------------------------------------------

def load_frame(csv_path: Path) -> tuple[Table, str, list[str]]:
    header, records, encoding, warnings = read_csv_repaired(csv_path)

    unknown = [c for c in header if c not in COLUMN_MAP]
    if unknown:
        warnings.append(f"columns present in the CSV but not mapped: {', '.join(unknown)}")
    missing = [c for c in COLUMN_MAP if c not in header]
    if missing:
        warnings.append(f"expected columns absent from the CSV: {', '.join(missing)}")

    position = {name: i for i, name in enumerate(header)}
    table = Table(COLUMN_MAP.values())
    for source, target in COLUMN_MAP.items():
        idx = position.get(source)
        table[target] = ([clean_str(r[idx]) for r in records] if idx is not None
                         else [None] * len(records))

    for col in INT_COLS:
        table[col] = [to_int(v) for v in table[col]]
    for col in FLOAT_COLS:
        table[col] = [to_float(v) for v in table[col]]

    # drop rows that are entirely empty
    before = len(table)
    table.keep([any(v is not None for v in row.values()) for row in table.rows()])
    blank = before - len(table)
    if blank:
        warnings.append(f"skipped {blank} entirely empty row(s)")

    # rows with no usable identifier at all are dropped and reported
    before = len(table)
    table.keep([not (i is None and r is None)
                for i, r in zip(table["id_internal"], table["reference"])])
    if len(table) < before:
        warnings.append(f"dropped {before - len(table)} row(s) with neither ID_Internal nor Reference")

    # synthesise ids for rows missing ID_Internal so the primary key is total
    known = [v for v in table["id_internal"] if v is not None]
    max_id = max(known) if known else 0
    synth = 0
    ids = []
    for v in table["id_internal"]:
        if v is None:
            max_id += 1
            synth += 1
            ids.append(max_id)
        else:
            ids.append(v)
    table["id_internal"] = ids
    if synth:
        warnings.append(f"assigned surrogate ids to {synth} row(s) with an empty ID_Internal")

    seen = Counter(table["id_internal"])
    dupe = sum(n - 1 for n in seen.values() if n > 1)
    if dupe:
        warnings.append(f"{dupe} duplicate ID_Internal value(s); kept all rows, "
                        f"the primary key is a surrogate")

    # coordinate sanity
    lat_col, lon_col = table["latitude"], table["longitude"]
    bad = 0
    for i in range(len(table)):
        la, lo = lat_col[i], lon_col[i]
        if la is None or lo is None:
            continue
        if abs(la) > 90 or abs(lo) > 180:
            lat_col[i] = lon_col[i] = None
            bad += 1
    if bad:
        warnings.append(f"{bad} row(s) have out-of-range coordinates and are treated as unlocated")

    # derived temporal columns
    table["date_mid"] = [
        None if (a is None and b is None)
        else int(round(((a if a is not None else b) + (b if b is not None else a)) / 2))
        for a, b in zip(table["date_start"], table["date_end"])
    ]
    table["quarter_start"] = [quarter_bin(y) for y in table["date_start"]]
    table["quarter_end"] = [quarter_bin(y) for y in table["date_end"]]
    table["n_quarters"] = [len(quarter_range(a, b))
                           for a, b in zip(table["date_start"], table["date_end"])]
    table["century"] = [None if y is None else (-((-y + 99) // 100) if y < 0 else ((y - 1) // 100) + 1)
                        for y in table["date_mid"]]

    # lift "(?)" and "[found & written]" out of the findspot string into columns
    clean, uncertain, found_written = [], [], []
    for raw in table["location"]:
        if raw is None:
            clean.append(None)
            uncertain.append(0)
            found_written.append(0)
            continue
        name, unc, fw = split_place_flags(raw)
        clean.append(name)
        uncertain.append(1 if unc else 0)
        found_written.append(1 if fw else 0)
    flagged = sum(uncertain) + sum(found_written)
    if flagged:
        warnings.append(
            f"lifted {sum(uncertain)} uncertain-attribution and {sum(found_written)} "
            f"'found & written' marker(s) out of the findspot names into columns")
    table["location"] = clean
    table["uncertain_place"] = uncertain
    table["found_written"] = found_written

    return table, encoding, warnings


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE regions (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE NOT NULL
);

CREATE TABLE languages (
    id    INTEGER PRIMARY KEY,
    name  TEXT UNIQUE NOT NULL,
    slug  TEXT UNIQUE NOT NULL,
    color TEXT
);

CREATE TABLE alphabets (
    id    INTEGER PRIMARY KEY,
    code  INTEGER UNIQUE,
    label TEXT NOT NULL
);

CREATE TABLE directions (
    id    INTEGER PRIMARY KEY,
    code  TEXT UNIQUE,
    label TEXT NOT NULL
);

CREATE TABLE uses (
    id     INTEGER PRIMARY KEY,
    code   TEXT UNIQUE,
    label  TEXT NOT NULL,
    scheme TEXT NOT NULL DEFAULT 'numeric'
);

CREATE TABLE object_types (
    id     INTEGER PRIMARY KEY,
    name   TEXT UNIQUE NOT NULL,
    label  TEXT,
    scheme TEXT NOT NULL DEFAULT 'numeric'
);

CREATE TABLE places (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    slug           TEXT UNIQUE NOT NULL,
    region_id      INTEGER REFERENCES regions(id),
    parent_id      INTEGER REFERENCES places(id),
    level          TEXT NOT NULL DEFAULT 'site',
    aliases        TEXT,
    coord_shared   INTEGER NOT NULL DEFAULT 1,
    latitude       REAL,
    longitude      REAL,
    n_inscriptions INTEGER NOT NULL DEFAULT 0,
    n_descendants  INTEGER NOT NULL DEFAULT 0,
    date_min       INTEGER,
    date_max       INTEGER,
    UNIQUE (name, region_id)
);

-- One row per external authority record matched to a findspot. Written by
-- scripts/link_authorities.py into data/links.csv and folded in here, so the
-- matching can be re-run and corrected without touching the corpus.
CREATE TABLE place_links (
    place_id   INTEGER NOT NULL REFERENCES places(id),
    authority  TEXT NOT NULL,
    identifier TEXT,
    uri        TEXT NOT NULL,
    label      TEXT,
    method     TEXT,
    score      REAL,
    distance_km REAL,
    PRIMARY KEY (place_id, authority, uri)
);

CREATE TABLE inscriptions (
    pk              INTEGER PRIMARY KEY AUTOINCREMENT,
    id_internal     INTEGER,
    reference       TEXT,
    region_id       INTEGER REFERENCES regions(id),
    place_id        INTEGER REFERENCES places(id),
    language_id     INTEGER REFERENCES languages(id),
    alphabet_id     INTEGER REFERENCES alphabets(id),
    direction_id    INTEGER REFERENCES directions(id),
    use_id          INTEGER REFERENCES uses(id),
    object_type_id  INTEGER REFERENCES object_types(id),
    groundedness    INTEGER,
    coord_flag      INTEGER,
    uncertain_place INTEGER NOT NULL DEFAULT 0,
    found_written   INTEGER NOT NULL DEFAULT 0,
    n_quarters      INTEGER,
    q_ord_start     INTEGER,
    q_ord_end       INTEGER,
    latitude        REAL,
    longitude       REAL,
    date_start      INTEGER,
    date_end        INTEGER,
    date_start_orig INTEGER,
    date_end_orig   INTEGER,
    date_mid        INTEGER,
    quarter_start   INTEGER,
    quarter_end     INTEGER,
    century         INTEGER,
    script          TEXT,
    notes           TEXT
);

-- The quarter-century axis, as a dimension table. Every inscription carries the
-- ordinal of the first and last quarter its date interval touches, which is
-- what makes the aoristic view below a plain range join rather than a
-- materialised table of 140,000 rows that nobody wants to download.
CREATE TABLE quarters (
    ord   INTEGER PRIMARY KEY,
    edge  INTEGER UNIQUE NOT NULL,
    label TEXT NOT NULL
);

CREATE INDEX idx_ins_place     ON inscriptions(place_id);
CREATE INDEX idx_ins_region    ON inscriptions(region_id);
CREATE INDEX idx_ins_language  ON inscriptions(language_id);
CREATE INDEX idx_ins_alphabet  ON inscriptions(alphabet_id);
CREATE INDEX idx_ins_quarter   ON inscriptions(quarter_start);
CREATE INDEX idx_ins_coords    ON inscriptions(latitude, longitude);

CREATE VIEW v_inscriptions AS
SELECT i.pk,
       i.id_internal,
       i.reference,
       r.name  AS region,
       p.name  AS place,
       p.slug  AS place_slug,
       l.name  AS language,
       a.label AS alphabet,
       d.label AS direction,
       u.label AS use,
       o.name  AS object_type,
       p.level AS place_level,
       i.latitude, i.longitude,
       i.date_start, i.date_end, i.date_mid,
       i.quarter_start, i.quarter_end, i.n_quarters, i.century,
       i.uncertain_place, i.found_written,
       i.script, i.notes
FROM inscriptions i
LEFT JOIN regions      r ON r.id = i.region_id
LEFT JOIN places       p ON p.id = i.place_id
LEFT JOIN languages    l ON l.id = i.language_id
LEFT JOIN alphabets    a ON a.id = i.alphabet_id
LEFT JOIN directions   d ON d.id = i.direction_id
LEFT JOIN uses         u ON u.id = i.use_id
LEFT JOIN object_types o ON o.id = i.object_type_id;

CREATE VIEW v_place_counts AS
SELECT p.id, p.name, p.slug, r.name AS region,
       p.latitude, p.longitude,
       COUNT(i.pk) AS n,
       MIN(i.date_start) AS date_min,
       MAX(i.date_end)   AS date_max
FROM places p
LEFT JOIN inscriptions i ON i.place_id = p.id
LEFT JOIN regions r ON r.id = p.region_id
GROUP BY p.id;

CREATE VIEW v_timeline AS
SELECT i.quarter_start AS quarter, l.name AS language, COUNT(*) AS n
FROM inscriptions i
LEFT JOIN languages l ON l.id = i.language_id
WHERE i.quarter_start IS NOT NULL
GROUP BY i.quarter_start, l.name;

-- One row per (inscription, quarter century it could belong to), weight 1/k.
-- Summing weight per quarter gives the interval-aware histogram; summing it
-- over everything returns the record count.
CREATE VIEW v_aoristic AS
SELECT i.pk, q.edge AS quarter, q.label AS quarter_label,
       1.0 / i.n_quarters AS weight
FROM inscriptions i
JOIN quarters q ON q.ord BETWEEN i.q_ord_start AND i.q_ord_end
WHERE i.n_quarters > 0;

CREATE VIEW v_timeline_aoristic AS
SELECT a.quarter, l.name AS language,
       SUM(a.weight) AS expected,
       COUNT(*)      AS n_possible
FROM v_aoristic a
JOIN inscriptions i ON i.pk = a.pk
LEFT JOIN languages l ON l.id = i.language_id
GROUP BY a.quarter, l.name;

CREATE VIEW v_place_tree AS
SELECT c.id, c.name, c.slug, c.level, c.n_inscriptions,
       p.id AS parent_id, p.name AS parent_name, p.slug AS parent_slug
FROM places c LEFT JOIN places p ON p.id = c.parent_id;

CREATE VIRTUAL TABLE inscriptions_fts USING fts5(
    reference, place, notes, script, reference_language,
    content='', tokenize='unicode61 remove_diacritics 2'
);
"""


# --------------------------------------------------------------------------
# database construction
# --------------------------------------------------------------------------

def load_place_links(cur, slugs: dict[int, str], names: dict[int, str]) -> int:
    """Fold data/links.csv, written by scripts/link_authorities.py, into the DB.

    Kept as a separate file on purpose: the reconciliation is fuzzy, it needs
    the network, and it should be reviewable and correctable by hand without
    rebuilding anything. Rows are matched on the place slug, falling back to the
    display name so that a corrected slug does not silently drop every link.
    """
    if not LINKS_CSV.exists():
        return 0
    by_slug = {slug: pid for pid, slug in slugs.items()}
    by_name = {name: pid for pid, name in names.items()}

    inserted = 0
    with LINKS_CSV.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            pid = by_slug.get((row.get("place_slug") or "").strip())
            if pid is None:
                pid = by_name.get((row.get("place_name") or "").strip())
            uri = (row.get("uri") or "").strip()
            if pid is None or not uri:
                continue
            cur.execute(
                "INSERT OR REPLACE INTO place_links "
                "(place_id, authority, identifier, uri, label, method, score, distance_km) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (pid, (row.get("authority") or "").strip(),
                 (row.get("identifier") or "").strip() or None, uri,
                 (row.get("label") or "").strip() or None,
                 (row.get("method") or "").strip() or None,
                 to_float(row.get("score")), to_float(row.get("distance_km"))))
            inserted += 1
    return inserted


def build_sqlite(df: Table, codebook: dict, db_path: Path,
                 csv_path: Path, csv_hash: str, encoding: str) -> sqlite3.Connection:
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    cur = con.cursor()

    def lookup(table, values, extra=None):
        """Insert distinct values and return {value: id}."""
        mapping = {}
        for i, v in enumerate(values, start=1):
            if extra is None:
                cur.execute(f"INSERT INTO {table} (id, name, slug) VALUES (?,?,?)",
                            (i, v, slugify(v)))
            else:
                cur.execute(f"INSERT INTO {table} (id, code, label) VALUES (?,?,?)",
                            (i, v, extra(v)))
            mapping[v] = i
        return mapping

    regions = sorted({v for v in df["region"] if v})
    region_ids = lookup("regions", regions)

    languages = sorted({v for v in df["language"] if v})
    lang_colors = (codebook.get("language_colors") or {})
    lang_ids = {}
    for i, name in enumerate(languages, start=1):
        color = lang_colors.get(name) or hue_color(i, len(languages))
        cur.execute("INSERT INTO languages (id, name, slug, color) VALUES (?,?,?,?)",
                    (i, name, slugify(name), color))
        lang_ids[name] = i

    alphabets = sorted({v for v in df["alphabet"] if v is not None})
    alpha_ids = lookup("alphabets", alphabets,
                       extra=lambda c: label_for(codebook, "alphabet", c, "Alphabet"))

    directions = sorted({v for v in df["direction"] if v}, key=str)
    dir_ids = lookup("directions", directions,
                     extra=lambda c: label_for(codebook, "direction", c, "Direction"))

    # Inscription.Use and Object.Type each hold two coding schemes at once: bare
    # integers from one annotation pass and English strings from another. They
    # are kept apart by a scheme column rather than being presented as if code
    # 28 and "cippus" belonged to a single vocabulary.
    def scheme_of(code) -> str:
        return "numeric" if re.fullmatch(r"-?\d+", str(code)) else "descriptive"

    uses = sorted({v for v in df["use"] if v}, key=str)
    use_ids = {}
    for i, code in enumerate(uses, start=1):
        scheme = scheme_of(code)
        label = (label_for(codebook, "use", to_int(code), "Use")
                 if scheme == "numeric" else str(code))
        cur.execute("INSERT INTO uses (id, code, label, scheme) VALUES (?,?,?,?)",
                    (i, code, label, scheme))
        use_ids[code] = i

    obj_types = sorted({v for v in df["object_type"] if v}, key=str)
    obj_ids = {}
    for i, name in enumerate(obj_types, start=1):
        scheme = scheme_of(name)
        label = (label_for(codebook, "object_type", to_int(name), "Object type")
                 if scheme == "numeric" else str(name))
        cur.execute("INSERT INTO object_types (id, name, label, scheme) VALUES (?,?,?,?)",
                    (i, name, label, scheme))
        obj_ids[name] = i

    # ---- places ----------------------------------------------------------
    # A place starts as (Specified.Location, Region); rows without a location
    # fall back to a per-region bucket so nothing is silently lost. Two further
    # passes then fix the two problems that flat keying leaves behind.
    place_key_to_id: dict[tuple, int] = {}
    place_name: dict[int, str] = {}
    place_region: dict[int, str | None] = {}
    place_coords: dict[int, list] = defaultdict(list)
    place_ids_by_row: list[int] = []
    next_place = 1

    for loc, reg, lat, lon in zip(df["location"], df["region"],
                                  df["latitude"], df["longitude"]):
        name = loc or (f"{reg} (unspecified findspot)" if reg else "Findspot not recorded")
        key = (name, reg)
        pid = place_key_to_id.get(key)
        if pid is None:
            pid = next_place
            place_key_to_id[key] = pid
            place_name[pid] = name
            place_region[pid] = reg
            next_place += 1
        place_ids_by_row.append(pid)
        if lat is not None and lon is not None:
            place_coords[pid].append((lat, lon))

    def centroid(pid):
        coords = place_coords.get(pid) or []
        if not coords:
            return None, None
        return (round(sum(c[0] for c in coords) / len(coords), 6),
                round(sum(c[1] for c in coords) / len(coords), 6))

    # Pass 1: merge records that name the same thing at the same coordinates.
    # "Palestrina" and "Palestrina / Praeneste" sit on the same point and share
    # a name; so do "Este", "Ateste (Este)" and "Este, Italy". Merging is only
    # allowed when the coordinates are identical to four decimals (about 11 m)
    # and the normalised name sets intersect, which never merges two genuinely
    # different findspots that happen to share a centroid.
    parent_of: dict[int, int] = {}

    def find(x):
        while parent_of.get(x, x) != x:
            parent_of[x] = parent_of.get(parent_of[x], parent_of[x])
            x = parent_of[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent_of[max(ra, rb)] = min(ra, rb)

    coord_groups: dict[tuple, list[int]] = defaultdict(list)
    for pid in place_name:
        lat, lon = centroid(pid)
        if lat is None:
            continue
        coord_groups[(round(lat, 4), round(lon, 4))].append(pid)

    variants = {pid: name_variants(place_name[pid]) for pid in place_name}
    merged = 0
    for group in coord_groups.values():
        for a_i in range(len(group)):
            for b_i in range(a_i + 1, len(group)):
                a, b = group[a_i], group[b_i]
                if variants[a] & variants[b]:
                    union(a, b)
                    merged += 1

    canonical: dict[int, int] = {}
    alias_names: dict[int, set] = defaultdict(set)
    members: dict[int, list[int]] = defaultdict(list)
    for pid in place_name:
        members[find(pid)].append(pid)
    for root, group in members.items():
        # the fullest name wins, since "Palestrina / Praeneste" carries more
        # information than "Palestrina"
        keeper = max(group, key=lambda p: (len(place_name[p]), -p))
        for pid in group:
            canonical[pid] = keeper
            if pid != keeper:
                alias_names[keeper].add(place_name[pid])
                place_coords[keeper].extend(place_coords.get(pid, []))
    place_ids_by_row = [canonical[p] for p in place_ids_by_row]
    live = sorted({canonical[p] for p in place_name})
    if merged:
        warnings_extra = f"merged {len(place_name) - len(live)} duplicate findspot record(s)"
    else:
        warnings_extra = None

    counts = Counter(place_ids_by_row)

    # Pass 2: hierarchy. Coincident places are attached to the largest
    # site-level place on the same point. This is a statement about shared
    # coordinates, not about topographic containment: the 42 Monterozzi tombs
    # really do carry the Tarquinia centroid, and the map can now roll them up
    # instead of drawing 42 dots on one pixel.
    levels = {pid: place_level(place_name[pid]) for pid in live}
    parents: dict[int, int | None] = {pid: None for pid in live}
    shared: dict[int, int] = {pid: 1 for pid in live}

    live_groups: dict[tuple, list[int]] = defaultdict(list)
    for pid in live:
        lat, lon = centroid(pid)
        if lat is None:
            continue
        live_groups[(round(lat, 4), round(lon, 4))].append(pid)

    virtual: list[int] = []
    virtual_by_key: dict[tuple, int] = {}
    existing_names = {(place_name[p], place_region[p]) for p in live}
    for coords, group in live_groups.items():
        if len(group) < 2:
            continue
        sites = [p for p in group if levels[p] == "site"]
        head = None
        if not sites:
            # every member is a sub-findspot: "Monterozzi, Calvario - T 1718",
            # "Monterozzi, Cimitero - T d Orco" and forty more. They all name
            # the same necropolis, which has no record of its own, so the
            # necropolis is created as an empty parent rather than promoting an
            # arbitrary tomb to stand for the whole cemetery.
            stems = {re.split(r"\s*[,]\s*", place_name[p])[0].strip() for p in group}
            if len(stems) == 1:
                stem = stems.pop()
                region = place_region[group[0]]
                if stem and (stem, region) in existing_names:
                    stem = ""
                if stem and (stem, region) in virtual_by_key:
                    head = virtual_by_key[(stem, region)]
                    group = group + [head]
                elif stem:
                    head = next_place
                    next_place += 1
                    place_name[head] = stem
                    place_region[head] = region
                    place_coords[head] = [(coords[0], coords[1])]
                    levels[head] = "necropolis" if NECROPOLIS_WORDS.search(stem) else "site"
                    parents[head] = None
                    shared[head] = len(group) + 1
                    virtual.append(head)
                    virtual_by_key[(stem, region)] = head
                    group = group + [head]
        if head is None:
            pool = sites or group
            head = max(pool, key=lambda p: (counts.get(p, 0), len(place_name[p])))
        for pid in group:
            shared[pid] = len(group)
            if pid != head:
                parents[pid] = head
                if levels[pid] == "site":
                    levels[pid] = "locality"

    if virtual:
        live = sorted(set(live) | set(virtual))

    # name-based fallback for children that were given their own coordinates
    by_slug = {slugify(place_name[p]): p for p in live}
    for pid in live:
        if parents[pid] is not None:
            continue
        head_name = re.split(r"\s*[,]\s*", place_name[pid])[0]
        if head_name == place_name[pid]:
            continue
        candidate = by_slug.get(slugify(head_name))
        if candidate is not None and candidate != pid:
            parents[pid] = candidate

    # deterministic slugs: the same CSV content always produces the same URLs,
    # whatever order the rows arrive in
    slugs: dict[int, str] = {}
    taken: set[str] = set()
    for pid in sorted(live, key=lambda p: (place_name[p], place_region[p] or "")):
        base = slugify(place_name[pid])
        slug = base
        if slug in taken and place_region[pid]:
            slug = f"{base}-{slugify(place_region[pid])}"
        if slug in taken:
            digest = hashlib.sha1(
                f"{place_name[pid]}|{place_region[pid]}".encode("utf-8")).hexdigest()[:6]
            slug = f"{base}-{digest}"
        taken.add(slug)
        slugs[pid] = slug

    for pid in live:
        lat, lon = centroid(pid)
        aliases = sorted(alias_names.get(pid, ()))
        cur.execute(
            "INSERT INTO places (id, name, slug, region_id, level, aliases, "
            "coord_shared, latitude, longitude) VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, place_name[pid], slugs[pid], region_ids.get(place_region[pid]),
             levels[pid],
             json.dumps(aliases, ensure_ascii=False) if aliases else None,
             shared[pid], lat, lon))
    # parents are set afterwards: the table references itself, so every row has
    # to exist before any of them can point at another
    cur.executemany("UPDATE places SET parent_id = ? WHERE id = ?",
                    [(parent, pid) for pid, parent in parents.items() if parent is not None])

    # ---- the quarter-century axis ---------------------------------------
    # Built from the data rather than fixed, then trimmed: one record dated
    # "200 BC to AD 799" would otherwise stretch the axis by forty empty bins,
    # each holding a fortieth of a single inscription. A quarter is kept if any
    # record starts in it, or if the aoristic weight it accumulates reaches half
    # an inscription. The kept range is then filled in so the axis has no holes.
    weight_by_edge: dict[int, float] = defaultdict(float)
    start_bins: set[int] = set()
    for start, end in zip(df["date_start"], df["date_end"]):
        qs = quarter_bin(start)
        if qs is not None:
            start_bins.add(qs)
        for edge, w in aoristic_weights(start, end):
            weight_by_edge[edge] += w

    kept = {e for e, w in weight_by_edge.items() if w >= 0.5} | start_bins
    if kept:
        lo, hi = min(kept), max(kept)
        axis = [e for e in sorted(weight_by_edge) if lo <= e <= hi]
        for e in sorted(start_bins):
            if lo <= e <= hi and e not in axis:
                axis.append(e)
        axis = sorted(set(axis))
    else:
        axis = []
    q_ord = {edge: i for i, edge in enumerate(axis)}
    cur.executemany("INSERT INTO quarters (ord, edge, label) VALUES (?,?,?)",
                    [(i, e, quarter_label(e)) for i, e in enumerate(axis)])

    ord_start, ord_end, clamped = [], [], 0
    for start, end in zip(df["date_start"], df["date_end"]):
        edges = quarter_range(start, end)
        if not edges or not axis:
            ord_start.append(None)
            ord_end.append(None)
            continue
        inside = [e for e in edges if e in q_ord]
        if not inside:
            ord_start.append(None)
            ord_end.append(None)
            clamped += 1
            continue
        if len(inside) != len(edges):
            clamped += 1
        ord_start.append(q_ord[inside[0]])
        ord_end.append(q_ord[inside[-1]])
    df["q_ord_start"] = ord_start
    df["q_ord_end"] = ord_end
    if clamped:
        cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('clamped_dates', ?)",
                    (str(clamped),))

    # ---- inscriptions ----------------------------------------------------
    rows = []
    for r, pid in zip(df.rows(), place_ids_by_row):
        rows.append((
            r["id_internal"], r["reference"], region_ids.get(r["region"]), pid,
            lang_ids.get(r["language"]), alpha_ids.get(r["alphabet"]),
            dir_ids.get(r["direction"]), use_ids.get(r["use"]),
            obj_ids.get(r["object_type"]),
            r["groundedness"], r["coord_flag"],
            r["uncertain_place"], r["found_written"], r["n_quarters"],
            r["q_ord_start"], r["q_ord_end"],
            r["latitude"], r["longitude"],
            r["date_start"], r["date_end"], r["date_start_orig"], r["date_end_orig"],
            r["date_mid"], r["quarter_start"], r["quarter_end"], r["century"],
            r["script"], r["notes"],
        ))
    cur.executemany("""
        INSERT INTO inscriptions
            (id_internal, reference, region_id, place_id, language_id, alphabet_id,
             direction_id, use_id, object_type_id, groundedness, coord_flag,
             uncertain_place, found_written, n_quarters, q_ord_start, q_ord_end,
             latitude, longitude, date_start, date_end, date_start_orig,
             date_end_orig, date_mid, quarter_start, quarter_end, century,
             script, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)

    cur.execute("""
        UPDATE places SET
            n_inscriptions = (SELECT COUNT(*) FROM inscriptions i WHERE i.place_id = places.id),
            date_min = (SELECT MIN(date_start) FROM inscriptions i WHERE i.place_id = places.id),
            date_max = (SELECT MAX(date_end)   FROM inscriptions i WHERE i.place_id = places.id)
    """)
    cur.execute("""
        UPDATE places SET
            n_descendants = COALESCE((SELECT SUM(c.n_inscriptions) FROM places c
                                      WHERE c.parent_id = places.id), 0)
    """)

    load_place_links(cur, slugs, place_name)
    if warnings_extra:
        cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('merge_note', ?)",
                    (warnings_extra,))

    cur.execute("""
        INSERT INTO inscriptions_fts (rowid, reference, place, notes, script, reference_language)
        SELECT i.pk,
               COALESCE(i.reference, ''),
               COALESCE(p.name, ''),
               COALESCE(i.notes, ''),
               COALESCE(i.script, ''),
               COALESCE(l.name, '')
        FROM inscriptions i
        LEFT JOIN places p    ON p.id = i.place_id
        LEFT JOIN languages l ON l.id = i.language_id
    """)

    meta = {
        "source_csv": csv_path.name,
        "source_sha256": csv_hash,
        "source_encoding": encoding,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "script_version": SCRIPT_VERSION,
        "row_count": str(len(df)),
        "place_count": str(len(live)),
        "sqlite_version": sqlite3.sqlite_version,
    }
    cur.executemany("INSERT INTO meta (key, value) VALUES (?,?)", list(meta.items()))

    con.commit()
    cur.execute("ANALYZE")
    con.commit()
    return con


def write_sql_text(con: sqlite3.Connection, db_dir: Path) -> None:
    ddl = [r[0] for r in con.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY "
        "CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name")]
    header = ("-- Generated by scripts/build_db.py. Do not edit; edit the CSV instead.\n"
              f"-- {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n")
    (db_dir / "schema.sql").write_text(header + ";\n\n".join(ddl) + ";\n", encoding="utf-8")

    with (db_dir / "atlas.dump.sql").open("w", encoding="utf-8") as fh:
        fh.write(header)
        for line in con.iterdump():
            fh.write(line + "\n")


# --------------------------------------------------------------------------
# exports for the web front end
# --------------------------------------------------------------------------

def export_web(con: sqlite3.Connection, codebook: dict, warnings: list[str]) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JEKYLL_DATA.mkdir(parents=True, exist_ok=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    languages = [dict(r) for r in cur.execute(
        "SELECT id, name, slug, color FROM languages ORDER BY name")]
    alphabets = [dict(r) for r in cur.execute(
        "SELECT id, code, label FROM alphabets ORDER BY code")]
    regions = [dict(r) for r in cur.execute(
        "SELECT id, name, slug FROM regions ORDER BY name")]
    directions = [dict(r) for r in cur.execute(
        "SELECT id, code, label FROM directions ORDER BY code")]
    uses = [dict(r) for r in cur.execute(
        "SELECT id, code, label, scheme FROM uses ORDER BY scheme, label")]
    obj_types = [dict(r) for r in cur.execute(
        "SELECT id, name, label, scheme FROM object_types ORDER BY scheme, name")]

    lang_count = {r["id"]: 0 for r in languages}
    for row in cur.execute("SELECT language_id, COUNT(*) n FROM inscriptions GROUP BY language_id"):
        if row["language_id"] is not None:
            lang_count[row["language_id"]] = row["n"]
    for lang in languages:
        lang["count"] = lang_count.get(lang["id"], 0)

    alpha_count = {}
    for row in cur.execute("SELECT alphabet_id, COUNT(*) n FROM inscriptions GROUP BY alphabet_id"):
        alpha_count[row["alphabet_id"]] = row["n"]
    for a in alphabets:
        a["count"] = alpha_count.get(a["id"], 0)

    region_count = {}
    for row in cur.execute("SELECT region_id, COUNT(*) n FROM inscriptions GROUP BY region_id"):
        region_count[row["region_id"]] = row["n"]
    for r in regions:
        r["count"] = region_count.get(r["id"], 0)

    # ---- places.geojson -------------------------------------------------
    per_place_lang = defaultdict(dict)
    for row in cur.execute("""
        SELECT place_id, language_id, COUNT(*) n FROM inscriptions
        WHERE language_id IS NOT NULL GROUP BY place_id, language_id"""):
        per_place_lang[row["place_id"]][row["language_id"]] = row["n"]

    per_place_alpha = defaultdict(dict)
    for row in cur.execute("""
        SELECT place_id, alphabet_id, COUNT(*) n FROM inscriptions
        WHERE alphabet_id IS NOT NULL GROUP BY place_id, alphabet_id"""):
        per_place_alpha[row["place_id"]][row["alphabet_id"]] = row["n"]

    link_count = {r["place_id"]: r["n"] for r in cur.execute(
        "SELECT place_id, COUNT(*) n FROM place_links GROUP BY place_id")}

    features = []
    unlocated = 0
    for row in cur.execute("""
        SELECT p.id, p.name, p.slug, p.latitude, p.longitude, p.n_inscriptions,
               p.date_min, p.date_max, p.parent_id, p.level, p.coord_shared,
               r.name AS region
        FROM places p LEFT JOIN regions r ON r.id = p.region_id
        ORDER BY p.n_inscriptions DESC"""):
        if row["latitude"] is None or row["longitude"] is None:
            unlocated += row["n_inscriptions"]
            continue
        langs = per_place_lang.get(row["id"], {})
        dominant = max(langs, key=langs.get) if langs else None
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["longitude"], row["latitude"]]},
            "properties": {
                "id": row["id"],
                "name": row["name"],
                "slug": row["slug"],
                "region": row["region"],
                "n": row["n_inscriptions"],
                "date_min": row["date_min"],
                "date_max": row["date_max"],
                "lang": dominant,
                "langs": langs,
                "alphabets": per_place_alpha.get(row["id"], {}),
                "parent": row["parent_id"],
                "level": row["level"],
                "shared": row["coord_shared"],
                "links": link_count.get(row["id"], 0),
            },
        })
    geojson = {"type": "FeatureCollection", "features": features}
    (DATA_DIR / "places.geojson").write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- corpus.json (columnar, dictionary-encoded) ----------------------
    # notes and script repeat heavily: one 200-character note occurs 2,000
    # times. Storing an index into a dictionary instead of the string itself
    # takes roughly a megabyte off the payload the browser has to parse before
    # the first paint.
    cols = defaultdict(list)
    dicts: dict[str, list[str]] = {"notes": [""], "script": [""]}
    dict_index: dict[str, dict[str, int]] = {"notes": {"": 0}, "script": {"": 0}}

    def encode(field: str, value: str | None) -> int:
        v = value or ""
        table = dict_index[field]
        idx = table.get(v)
        if idx is None:
            idx = len(dicts[field])
            dicts[field].append(v)
            table[v] = idx
        return idx

    for row in cur.execute("""
        SELECT pk, id_internal, reference, place_id, region_id, language_id,
               alphabet_id, direction_id, use_id, object_type_id,
               date_start, date_end, quarter_start,
               quarter_end, n_quarters, groundedness, uncertain_place,
               found_written, script, notes
        FROM inscriptions ORDER BY pk"""):
        cols["id"].append(row["id_internal"])
        cols["ref"].append(row["reference"] or "")
        cols["place"].append(row["place_id"])
        cols["region"].append(row["region_id"])
        cols["lang"].append(row["language_id"])
        cols["alpha"].append(row["alphabet_id"])
        cols["dir"].append(row["direction_id"])
        cols["use"].append(row["use_id"])
        cols["obj"].append(row["object_type_id"])
        cols["ds"].append(row["date_start"])
        cols["de"].append(row["date_end"])
        cols["q"].append(row["quarter_start"])
        cols["qe"].append(row["quarter_end"])
        # the true width of the dating interval, which is what the aoristic
        # weight divides by. It can exceed the span the timeline axis shows,
        # for the handful of records whose end date is out of range, and the
        # browser has to use the same denominator as the build or the two
        # histograms disagree.
        cols["nq"].append(row["n_quarters"])
        cols["grnd"].append(row["groundedness"])
        cols["unc"].append(row["uncertain_place"])
        cols["fw"].append(row["found_written"])
        cols["script"].append(encode("script", row["script"]))
        cols["notes"].append(encode("notes", row["notes"]))

    place_rows = list(cur.execute(
        "SELECT id, name, slug, level, parent_id, coord_shared FROM places"))
    corpus = {
        "n": len(cols["id"]),
        "columns": dict(cols),
        "dicts": dicts,
        "lookups": {
            "languages": {l["id"]: l["name"] for l in languages},
            "language_colors": {l["id"]: l["color"] for l in languages},
            "alphabets": {a["id"]: a["label"] for a in alphabets},
            "regions": {r["id"]: r["name"] for r in regions},
            "directions": {d["id"]: d["label"] for d in directions},
            "uses": {u["id"]: u["label"] for u in uses},
            "object_types": {o["id"]: o["label"] or o["name"] for o in obj_types},
            "places": {r["id"]: r["name"] for r in place_rows},
            "place_slugs": {r["id"]: r["slug"] for r in place_rows},
            "place_levels": {r["id"]: r["level"] for r in place_rows},
            "place_parents": {r["id"]: r["parent_id"] for r in place_rows
                              if r["parent_id"] is not None},
        },
    }
    (DATA_DIR / "corpus.json").write_text(
        json.dumps(corpus, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- timeline.json --------------------------------------------------
    # Two histograms are published side by side. "start" is the old one, which
    # credits a record to the quarter its Date.Start falls in: it is what most
    # epigraphic databases show and it is badly spiked, because a third of the
    # corpus is dated to intervals of a century or more that all begin on a
    # round century edge. "aoristic" spreads each record evenly over the
    # quarters it could occupy. The band is the analytic standard deviation of
    # the Poisson binomial implied by those weights, so the reader can see how
    # much of the shape survives the dating uncertainty without waiting for a
    # simulation to run.
    quarters = [r["edge"] for r in cur.execute("SELECT edge FROM quarters ORDER BY ord")]
    q_index = {q: i for i, q in enumerate(quarters)}

    matrix = {l["id"]: [0] * len(quarters) for l in languages}
    totals = [0] * len(quarters)
    for row in cur.execute("""
        SELECT quarter_start q, language_id l, COUNT(*) n FROM inscriptions
        WHERE quarter_start IS NOT NULL GROUP BY quarter_start, language_id"""):
        if row["q"] not in q_index:
            continue
        i = q_index[row["q"]]
        totals[i] += row["n"]
        if row["l"] in matrix:
            matrix[row["l"]][i] = row["n"]

    a_matrix = {l["id"]: [0.0] * len(quarters) for l in languages}
    a_totals = [0.0] * len(quarters)
    a_var = [0.0] * len(quarters)
    for row in cur.execute("""
        SELECT a.quarter q, i.language_id l,
               SUM(a.weight) w, SUM(a.weight * (1 - a.weight)) v
        FROM v_aoristic a JOIN inscriptions i ON i.pk = a.pk
        GROUP BY a.quarter, i.language_id"""):
        if row["q"] not in q_index:
            continue
        i = q_index[row["q"]]
        a_totals[i] += row["w"]
        a_var[i] += row["v"]
        if row["l"] in a_matrix:
            a_matrix[row["l"]][i] = round(row["w"], 3)

    timeline = {
        "quarters": quarters,
        "labels": [quarter_label(q) for q in quarters],
        "totals": totals,
        "by_language": matrix,
        "aoristic": {
            "totals": [round(v, 3) for v in a_totals],
            "sd": [round(math.sqrt(v), 3) for v in a_var],
            "by_language": a_matrix,
        },
    }
    (DATA_DIR / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- _data/atlas.yml -------------------------------------------------
    meta = {r["key"]: r["value"] for r in cur.execute("SELECT key, value FROM meta")}
    dated = cur.execute("SELECT COUNT(*) c FROM inscriptions WHERE date_start IS NOT NULL").fetchone()["c"]
    located = cur.execute("SELECT COUNT(*) c FROM inscriptions WHERE latitude IS NOT NULL").fetchone()["c"]
    bounds = cur.execute("""SELECT MIN(latitude) miny, MIN(longitude) minx,
                                   MAX(latitude) maxy, MAX(longitude) maxx
                            FROM inscriptions WHERE latitude IS NOT NULL""").fetchone()

    unlabelled = [a["label"] for a in alphabets if a["label"].startswith("Alphabet ")]
    if unlabelled:
        warnings.append(
            f"{len(unlabelled)} alphabet code(s) have no label in scripts/codebook.yml: "
            + ", ".join(unlabelled))
    for field, table, prefix in (("use", uses, "Use "), ("object_type", obj_types, "Object type ")):
        blank = [t["label"] for t in table if str(t["label"]).startswith(prefix)]
        if blank:
            warnings.append(f"{len(blank)} {field} code(s) have no label in scripts/codebook.yml")

    # a bounding box on the central 99% of points, matching the one
    # enrich_geo.py queries with, rather than the raw extremes: a dozen records
    # from Africa and Pannonia otherwise stretch the box across half of Europe
    lats = [r[0] for r in cur.execute(
        "SELECT latitude FROM inscriptions WHERE latitude IS NOT NULL ORDER BY latitude")]
    lons = [r[0] for r in cur.execute(
        "SELECT longitude FROM inscriptions WHERE longitude IS NOT NULL ORDER BY longitude")]
    if len(lats) > 200:
        lo, hi = int(len(lats) * 0.005), int(len(lats) * 0.995) - 1
        core = {"minx": lons[lo], "miny": lats[lo], "maxx": lons[hi], "maxy": lats[hi]}
    else:
        core = {"minx": bounds["minx"], "miny": bounds["miny"],
                "maxx": bounds["maxx"], "maxy": bounds["maxy"]}

    hierarchy = cur.execute(
        "SELECT SUM(parent_id IS NULL) roots, SUM(parent_id IS NOT NULL) children, "
        "SUM(coord_shared > 1) shared FROM places").fetchone()
    links_by_authority = {r["authority"]: r["n"] for r in cur.execute(
        "SELECT authority, COUNT(*) n FROM place_links GROUP BY authority ORDER BY authority")}
    aor_rows = cur.execute("SELECT COUNT(*) c FROM v_aoristic").fetchone()["c"]

    atlas = {
        "build": meta,
        "stats": {
            "inscriptions": int(meta["row_count"]),
            "places": int(meta["place_count"]),
            "located": located,
            "unlocated": int(meta["row_count"]) - located,
            "dated": dated,
            "regions": len(regions),
            "languages": len(languages),
            "alphabets": len(alphabets),
            "earliest": year_label(min((q for q in quarters), default=None)),
            "latest": quarter_label(max(quarters)) if quarters else "n/a",
        },
        "bounds": {"minx": bounds["minx"], "miny": bounds["miny"],
                   "maxx": bounds["maxx"], "maxy": bounds["maxy"]},
        "bounds_core": core,
        "hierarchy": {
            "roots": hierarchy["roots"] or 0,
            "children": hierarchy["children"] or 0,
            "coordinate_sharing": hierarchy["shared"] or 0,
        },
        "links": links_by_authority,
        "aoristic_rows": aor_rows,
        "languages": languages,
        "alphabets": alphabets,
        "regions": regions,
        "directions": directions,
        "uses": uses,
        "object_types": obj_types,
        "quarters": [{"edge": q, "label": quarter_label(q), "n": totals[i]}
                     for i, q in enumerate(quarters)],
        "top_places": [dict(r) for r in cur.execute(
            "SELECT name, slug, region, n, date_min, date_max FROM v_place_counts "
            "WHERE latitude IS NOT NULL ORDER BY n DESC LIMIT 40")],
        "warnings": warnings,
    }
    with (JEKYLL_DATA / "atlas.yml").open("w", encoding="utf-8") as fh:
        yaml.safe_dump(atlas, fh, allow_unicode=True, sort_keys=False, width=120)

    return atlas["stats"]


def write_site_pages(con: sqlite3.Connection) -> int:
    """One markdown stub per located place, consumed by the _sites collection."""
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    for old in SITES_DIR.glob("*.md"):
        old.unlink()
    con.row_factory = sqlite3.Row
    links = defaultdict(list)
    for row in con.execute(
            "SELECT place_id, authority, identifier, uri, label, method, score "
            "FROM place_links ORDER BY authority"):
        links[row["place_id"]].append({
            "authority": row["authority"], "identifier": row["identifier"],
            "uri": row["uri"], "label": row["label"],
            "method": row["method"], "score": row["score"]})

    children = defaultdict(list)
    for row in con.execute(
            "SELECT id, name, slug, level, n_inscriptions, parent_id FROM places "
            "WHERE parent_id IS NOT NULL ORDER BY n_inscriptions DESC"):
        children[row["parent_id"]].append({
            "name": row["name"], "slug": row["slug"],
            "level": row["level"], "n": row["n_inscriptions"]})

    n = 0
    for row in con.execute("""
        SELECT p.id, p.name, p.slug, p.latitude, p.longitude, p.n_inscriptions,
               p.date_min, p.date_max, p.level, p.aliases, p.coord_shared,
               p.n_descendants, r.name AS region,
               q.name AS parent_name, q.slug AS parent_slug
        FROM places p
        LEFT JOIN regions r ON r.id = p.region_id
        LEFT JOIN places  q ON q.id = p.parent_id
        WHERE p.latitude IS NOT NULL
          AND (p.n_inscriptions > 0 OR p.n_descendants > 0)
        ORDER BY p.n_inscriptions DESC, p.n_descendants DESC"""):
        front = {
            "layout": "site",
            "title": row["name"],
            "slug": row["slug"],
            "place_id": row["id"],
            "region": row["region"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "n_inscriptions": row["n_inscriptions"],
            "date_min": row["date_min"],
            "date_max": row["date_max"],
            "level": row["level"],
            "coord_shared": row["coord_shared"],
            "n_descendants": row["n_descendants"],
        }
        if row["aliases"]:
            front["aliases"] = json.loads(row["aliases"])
        if row["parent_slug"]:
            front["parent_name"] = row["parent_name"]
            front["parent_slug"] = row["parent_slug"]
        if children.get(row["id"]):
            front["children"] = children[row["id"]][:60]
        if links.get(row["id"]):
            front["links"] = links[row["id"]]
        body = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False) + "---\n"
        (SITES_DIR / f"{row['slug']}.md").write_text(body, encoding="utf-8")
        n += 1
    return n


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def needs_rebuild(csv_path: Path, csv_hash: str, db_path: Path) -> bool:
    if not db_path.exists() or not STAMP.exists():
        return True
    try:
        stamp = json.loads(STAMP.read_text())
    except (json.JSONDecodeError, OSError):
        return True
    if stamp.get("sha256") != csv_hash:
        return True
    if stamp.get("script_version") != SCRIPT_VERSION:
        return True
    codebook = Path(__file__).resolve().parent / "codebook.yml"
    if codebook.exists() and stamp.get("codebook_sha256") != sha256_of(codebook):
        return True
    for artefact in (DATA_DIR / "places.geojson", DATA_DIR / "corpus.json",
                     JEKYLL_DATA / "atlas.yml", DB_DIR / "schema.sql"):
        if not artefact.exists():
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--db", type=Path, default=DB_DIR / "atlas.sqlite")
    ap.add_argument("--force", action="store_true", help="rebuild even if the CSV is unchanged")
    ap.add_argument("--check", action="store_true", help="exit 1 if a rebuild is needed, then stop")
    ap.add_argument("--site-pages", action="store_true", help="also write _sites/*.md")
    ap.add_argument("--no-web-copy", action="store_true",
                    help="skip copying the SQLite file into assets/data/")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    def say(*a):
        if not args.quiet:
            print(*a, file=sys.stderr)

    if not args.csv.exists():
        sys.exit(f"CSV not found: {args.csv}")

    DB_DIR.mkdir(parents=True, exist_ok=True)
    csv_hash = sha256_of(args.csv)

    stale = needs_rebuild(args.csv, csv_hash, args.db)
    if args.check:
        say("rebuild needed" if stale else "up to date")
        return 1 if stale else 0
    if not stale and not args.force:
        say(f"up to date ({args.csv.name} unchanged, sha256 {csv_hash[:12]}) - nothing to do")
        return 0

    say(f"reading {args.csv}")
    df, encoding, warnings = load_frame(args.csv)
    say(f"  {len(df)} rows, decoded as {encoding}")
    for w in warnings:
        say(f"  ! {w}")

    codebook = load_codebook()
    say(f"writing {args.db}")
    con = build_sqlite(df, codebook, args.db, args.csv, csv_hash, encoding)

    say("writing db/schema.sql and db/atlas.dump.sql")
    write_sql_text(con, DB_DIR)

    say("writing assets/data/*.json and _data/atlas.yml")
    stats = export_web(con, codebook, warnings)

    if args.site_pages:
        n = write_site_pages(con)
        say(f"writing _sites/*.md ({n} pages)")

    if not args.no_web_copy:
        target = DATA_DIR / "atlas.sqlite"
        target.write_bytes(args.db.read_bytes())
        say(f"copied database to {target.relative_to(ROOT)} "
            f"({target.stat().st_size / 1e6:.1f} MB, queried in-browser by sql.js)")

    con.close()

    codebook_path = Path(__file__).resolve().parent / "codebook.yml"
    STAMP.write_text(json.dumps({
        "sha256": csv_hash,
        "codebook_sha256": sha256_of(codebook_path) if codebook_path.exists() else None,
        "script_version": SCRIPT_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": stats,
    }, indent=2), encoding="utf-8")

    say("done: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
