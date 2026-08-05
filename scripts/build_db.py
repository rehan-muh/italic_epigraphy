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

SCRIPT_VERSION = "1.2.0"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "database_preliminary.csv"
DB_DIR = ROOT / "db"
DATA_DIR = ROOT / "assets" / "data"
JEKYLL_DATA = ROOT / "_data"
SITES_DIR = ROOT / "_sites"
STAMP = DB_DIR / ".build-stamp.json"
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

# CSV header -> SQLite column
COLUMN_MAP = {
    "ID_Internal": "id_internal",
    "Reference": "reference",
    "Region": "region",
    "Groundedness": "groundedness",
    "Specified.Location": "location",
    "Pleiades": "pleiades",
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

INT_COLS = ("id_internal", "groundedness", "pleiades", "alphabet",
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


def read_csv_any_encoding(path: Path) -> tuple[list[str], list[list[str]], str]:
    """Return (header, rows, encoding).

    The file is tried against each candidate encoding in turn. Rows with more
    fields than the header are truncated and rows with fewer are padded, so a
    single malformed line cannot abort the build.
    """
    last = None
    for enc in ENCODINGS:
        try:
            with path.open("r", encoding=enc, newline="") as fh:
                reader = csv.reader(fh)
                header = next(reader, None)
                if header is None:
                    raise SystemExit(f"{path} is empty")
                width = len(header)
                rows = []
                for record in reader:
                    if len(record) < width:
                        record = record + [""] * (width - len(record))
                    elif len(record) > width:
                        record = record[:width]
                    rows.append(record)
            return [h.strip().lstrip("\ufeff") for h in header], rows, enc
        except UnicodeDecodeError as exc:
            last = exc
    raise SystemExit(f"could not decode {path} with any of {ENCODINGS}: {last}")


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
    header, records, encoding = read_csv_any_encoding(csv_path)
    warnings: list[str] = []

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
    table["century"] = [None if y is None else (-((-y + 99) // 100) if y < 0 else ((y - 1) // 100) + 1)
                        for y in table["date_mid"]]

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
    id    INTEGER PRIMARY KEY,
    code  TEXT UNIQUE,
    label TEXT NOT NULL
);

CREATE TABLE object_types (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE places (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    slug           TEXT UNIQUE NOT NULL,
    region_id      INTEGER REFERENCES regions(id),
    latitude       REAL,
    longitude      REAL,
    n_inscriptions INTEGER NOT NULL DEFAULT 0,
    date_min       INTEGER,
    date_max       INTEGER,
    UNIQUE (name, region_id)
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
    pleiades        INTEGER,
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
       i.latitude, i.longitude,
       i.date_start, i.date_end, i.date_mid,
       i.quarter_start, i.century,
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

CREATE VIRTUAL TABLE inscriptions_fts USING fts5(
    reference, place, notes, script, reference_language,
    content='', tokenize='unicode61 remove_diacritics 2'
);
"""


# --------------------------------------------------------------------------
# database construction
# --------------------------------------------------------------------------

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

    uses = sorted({v for v in df["use"] if v}, key=str)

    def use_label(code):
        if re.fullmatch(r"-?\d+", str(code)):
            return label_for(codebook, "use", to_int(code), "Use")
        return str(code)

    use_ids = lookup("uses", uses, extra=use_label)

    obj_types = sorted({v for v in df["object_type"] if v})
    obj_ids = {}
    for i, name in enumerate(obj_types, start=1):
        cur.execute("INSERT INTO object_types (id, name) VALUES (?,?)", (i, name))
        obj_ids[name] = i

    # places: a place is (Specified.Location, Region); rows without a location
    # fall back to a per-region "unlocated" bucket so nothing is silently lost.
    place_key_to_id: dict[tuple, int] = {}
    place_rows = []
    place_coords: dict[int, list] = defaultdict(list)
    next_place = 1
    for loc, reg in zip(df["location"], df["region"]):
        name = loc or (f"{reg} (unspecified findspot)" if reg else "Findspot not recorded")
        key = (name, reg)
        if key not in place_key_to_id:
            place_key_to_id[key] = next_place
            place_rows.append((next_place, name, reg))
            next_place += 1

    place_ids_by_row = []
    for loc, reg, lat, lon in zip(df["location"], df["region"], df["latitude"], df["longitude"]):
        name = loc or (f"{reg} (unspecified findspot)" if reg else "Findspot not recorded")
        pid = place_key_to_id[(name, reg)]
        place_ids_by_row.append(pid)
        if lat is not None and lon is not None:
            place_coords[pid].append((lat, lon))

    slug_seen = Counter()
    for pid, name, reg in place_rows:
        base = slugify(name)
        slug_seen[base] += 1
        slug = base if slug_seen[base] == 1 else f"{base}-{slug_seen[base]}"
        coords = place_coords.get(pid) or []
        lat = round(sum(c[0] for c in coords) / len(coords), 6) if coords else None
        lon = round(sum(c[1] for c in coords) / len(coords), 6) if coords else None
        cur.execute(
            "INSERT INTO places (id, name, slug, region_id, latitude, longitude) VALUES (?,?,?,?,?,?)",
            (pid, name, slug, region_ids.get(reg), lat, lon))

    rows = []
    for r, pid in zip(df.rows(), place_ids_by_row):
        rows.append((
            r["id_internal"], r["reference"], region_ids.get(r["region"]), pid,
            lang_ids.get(r["language"]), alpha_ids.get(r["alphabet"]),
            dir_ids.get(r["direction"]), use_ids.get(r["use"]),
            obj_ids.get(r["object_type"]),
            r["groundedness"], r["pleiades"], r["latitude"], r["longitude"],
            r["date_start"], r["date_end"], r["date_start_orig"], r["date_end_orig"],
            r["date_mid"], r["quarter_start"], r["quarter_end"], r["century"],
            r["script"], r["notes"],
        ))
    cur.executemany("""
        INSERT INTO inscriptions
            (id_internal, reference, region_id, place_id, language_id, alphabet_id,
             direction_id, use_id, object_type_id, groundedness, pleiades,
             latitude, longitude, date_start, date_end, date_start_orig,
             date_end_orig, date_mid, quarter_start, quarter_end, century,
             script, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)

    cur.execute("""
        UPDATE places SET
            n_inscriptions = (SELECT COUNT(*) FROM inscriptions i WHERE i.place_id = places.id),
            date_min = (SELECT MIN(date_start) FROM inscriptions i WHERE i.place_id = places.id),
            date_max = (SELECT MAX(date_end)   FROM inscriptions i WHERE i.place_id = places.id)
    """)

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
        "place_count": str(len(place_rows)),
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
    uses = [dict(r) for r in cur.execute("SELECT id, code, label FROM uses ORDER BY label")]
    obj_types = [dict(r) for r in cur.execute("SELECT id, name FROM object_types ORDER BY name")]

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

    features = []
    unlocated = 0
    for row in cur.execute("""
        SELECT p.id, p.name, p.slug, p.latitude, p.longitude, p.n_inscriptions,
               p.date_min, p.date_max, r.name AS region
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
            },
        })
    geojson = {"type": "FeatureCollection", "features": features}
    (DATA_DIR / "places.geojson").write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- corpus.json (columnar, dictionary-encoded) ----------------------
    cols = defaultdict(list)
    for row in cur.execute("""
        SELECT pk, id_internal, reference, place_id, region_id, language_id,
               alphabet_id, direction_id, use_id, object_type_id,
               latitude, longitude, date_start, date_end, quarter_start,
               groundedness, script, notes
        FROM inscriptions ORDER BY pk"""):
        cols["pk"].append(row["pk"])
        cols["id"].append(row["id_internal"])
        cols["ref"].append(row["reference"] or "")
        cols["place"].append(row["place_id"])
        cols["region"].append(row["region_id"])
        cols["lang"].append(row["language_id"])
        cols["alpha"].append(row["alphabet_id"])
        cols["dir"].append(row["direction_id"])
        cols["use"].append(row["use_id"])
        cols["obj"].append(row["object_type_id"])
        cols["lat"].append(None if row["latitude"] is None else round(row["latitude"], 5))
        cols["lon"].append(None if row["longitude"] is None else round(row["longitude"], 5))
        cols["ds"].append(row["date_start"])
        cols["de"].append(row["date_end"])
        cols["q"].append(row["quarter_start"])
        cols["grnd"].append(row["groundedness"])
        cols["script"].append(row["script"] or "")
        cols["notes"].append(row["notes"] or "")

    corpus = {
        "n": len(cols["pk"]),
        "columns": dict(cols),
        "lookups": {
            "languages": {l["id"]: l["name"] for l in languages},
            "language_colors": {l["id"]: l["color"] for l in languages},
            "alphabets": {a["id"]: a["label"] for a in alphabets},
            "regions": {r["id"]: r["name"] for r in regions},
            "directions": {d["id"]: d["label"] for d in directions},
            "uses": {u["id"]: u["label"] for u in uses},
            "object_types": {o["id"]: o["name"] for o in obj_types},
            "places": {r["id"]: r["name"] for r in cur.execute("SELECT id, name FROM places")},
            "place_slugs": {r["id"]: r["slug"] for r in cur.execute("SELECT id, slug FROM places")},
        },
    }
    (DATA_DIR / "corpus.json").write_text(
        json.dumps(corpus, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- timeline.json --------------------------------------------------
    quarters = sorted({r["quarter"] for r in cur.execute(
        "SELECT DISTINCT quarter_start AS quarter FROM inscriptions WHERE quarter_start IS NOT NULL")})
    matrix = {l["id"]: [0] * len(quarters) for l in languages}
    q_index = {q: i for i, q in enumerate(quarters)}
    totals = [0] * len(quarters)
    for row in cur.execute("""
        SELECT quarter_start q, language_id l, COUNT(*) n FROM inscriptions
        WHERE quarter_start IS NOT NULL GROUP BY quarter_start, language_id"""):
        i = q_index[row["q"]]
        totals[i] += row["n"]
        if row["l"] in matrix:
            matrix[row["l"]][i] = row["n"]
    timeline = {
        "quarters": quarters,
        "labels": [quarter_label(q) for q in quarters],
        "totals": totals,
        "by_language": matrix,
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
    n = 0
    for row in con.execute("""
        SELECT p.id, p.name, p.slug, p.latitude, p.longitude, p.n_inscriptions,
               p.date_min, p.date_max, r.name AS region
        FROM places p LEFT JOIN regions r ON r.id = p.region_id
        WHERE p.latitude IS NOT NULL AND p.n_inscriptions > 0
        ORDER BY p.n_inscriptions DESC"""):
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
        }
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
