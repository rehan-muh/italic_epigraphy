#!/usr/bin/env python3
"""
link_authorities.py - reconcile the findspots against external gazetteers and
write data/links.csv, which build_db.py folds into the place_links table.

The corpus carries no stable place identifiers: the column called "Pleiades" in
the CSV holds a 0/1/2 flag, not a Pleiades ID. Once a findspot has a Pleiades
URI, most of the rest of the linked-data world is a join rather than a project,
so Pleiades is matched first and the other authorities are matched beside it.

Matching is fuzzy and deliberately reviewable: every row records the method, the
name similarity and the distance in kilometres, and nothing is written back into
the corpus. Correct a row by editing data/links.csv, or drop it; the next
build_db.py run picks the file up as it stands.

    python3 scripts/link_authorities.py                    # every authority
    python3 scripts/link_authorities.py --only pleiades,wikidata
    python3 scripts/link_authorities.py --min-score 0.75   # stricter
    python3 scripts/link_authorities.py --offline          # cached responses only
    python3 scripts/link_authorities.py --periods          # PeriodO, into _data/
    python3 scripts/link_authorities.py --report           # what matched, no write

Requires the database, so run build_db.py first.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import math
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrich_geo as geo  # noqa: E402  (shared cache, HTTP and Pleiades readers)

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "atlas.sqlite"
OUT = ROOT / "data" / "links.csv"
PERIODS_OUT = ROOT / "_data" / "periods.yml"

IDAI_SEARCH = "https://gazetteer.dainst.org/search.json"
PERIODO_DUMP = "https://data.perio.do/d.json"

FIELDS = ["place_slug", "place_name", "authority", "identifier", "uri",
          "label", "method", "score", "distance_km"]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# name and distance comparison
# --------------------------------------------------------------------------

def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\(.*?\)", " ", text.lower())
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Modern administrative prefixes and generic words that carry no identifying
# force: "Castiglione in Teverina / Volsinii" should still match "Volsinii".
STOPWORDS = {"san", "santa", "sant", "santo", "di", "del", "della", "dei", "delle",
             "in", "al", "alla", "sul", "sulla", "e", "the", "of", "a", "il", "la",
             "area", "necropoli", "necropolis", "tomba", "tomb", "localita", "loc"}


def name_forms(name: str, aliases: list[str]) -> list[str]:
    """Every string worth trying for one findspot, longest first.

    "Palestrina / Praeneste" should match the Pleiades record for Praeneste even
    though the modern name comes first, so slash alternatives and parenthetical
    glosses are each tried on their own.
    """
    seen, out = set(), []
    for raw in [name] + list(aliases or []):
        chunks = [raw]
        chunks += re.split(r"\s*/\s*", raw)
        chunks += re.findall(r"\(([^)]*)\)", raw)
        chunks += [re.sub(r"\s*\([^)]*\)", "", raw)]
        chunks += [re.split(r"\s*,\s*", raw)[0]]
        for chunk in chunks:
            key = fold(chunk)
            words = [w for w in key.split() if w not in STOPWORDS]
            for form in (key, " ".join(words)):
                if form and len(form) > 2 and form not in seen:
                    seen.add(form)
                    out.append(form)
    return sorted(out, key=len, reverse=True)


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    # a containment match ("praeneste" inside "praeneste colonia") is worth more
    # than the raw ratio suggests
    if a in b or b in a:
        ratio = max(ratio, 0.9)
    return ratio


def haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Grid:
    """A coarse spatial index, so 1,700 findspots do not scan 40,000 candidates."""

    CELL = 0.25

    def __init__(self):
        self.cells: dict[tuple, list] = {}

    def add(self, lat, lon, payload):
        key = (int(lat / self.CELL), int(lon / self.CELL))
        self.cells.setdefault(key, []).append((lat, lon, payload))

    def near(self, lat, lon, radius_km):
        span = int(radius_km / 111.0 / self.CELL) + 1
        base = (int(lat / self.CELL), int(lon / self.CELL))
        for dy in range(-span, span + 1):
            for dx in range(-span, span + 1):
                for item in self.cells.get((base[0] + dy, base[1] + dx), ()):
                    yield item


def best_match(place, grid: Grid, opts, max_km: float):
    """Highest scoring candidate within max_km, or None."""
    forms = place["forms"]
    best = None
    for lat, lon, cand in grid.near(place["lat"], place["lon"], max_km):
        d = haversine(place["lat"], place["lon"], lat, lon)
        if d > max_km:
            continue
        sim = max((similarity(f, c) for f in forms for c in cand["forms"]), default=0.0)
        if sim < opts.min_name:
            continue
        score = 0.65 * sim + 0.35 * (1 - d / max_km)
        if best is None or score > best[0]:
            best = (score, sim, d, cand)
    if best is None or best[0] < opts.min_score:
        return None
    return best


# --------------------------------------------------------------------------
# authorities
# --------------------------------------------------------------------------

def candidates_pleiades(bbox, opts):
    tables = geo._pleiades_tables(opts)
    text = tables.get("places.csv") if tables else None
    if not text:
        log("  pleiades: no places table available")
        return []
    w, s, e, n = bbox
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            lat = float(row.get("representative_latitude") or row.get("reprLat") or "")
            lon = float(row.get("representative_longitude") or row.get("reprLong") or "")
        except ValueError:
            continue
        if not (s <= lat <= n and w <= lon <= e):
            continue
        title = (row.get("title") or "").strip()
        if not title:
            continue
        pid = (row.get("id") or row.get("path") or "").strip().strip("/").split("/")[-1]
        names = [title]
        extra = row.get("names") or row.get("nameAttested") or ""
        names += [p.strip() for p in re.split(r"[,;]", extra) if p.strip()]
        out.append((lat, lon, {
            "identifier": pid,
            "uri": f"https://pleiades.stoa.org/places/{pid}",
            "label": title,
            "forms": [fold(x) for x in names if fold(x)],
        }))
    log(f"  pleiades: {len(out)} candidates in the box")
    return out


def candidates_wikidata(bbox, opts):
    spec = {"id": "wikidata-sites", "kind": "archaeological-site"}
    features = geo.adapt_wikidata(spec, bbox, opts)
    out = []
    for geom, attrs, _rank in features:
        lon, lat = geom["coordinates"]
        out.append((lat, lon, {
            "identifier": attrs.get("qid"),
            "uri": attrs.get("uri"),
            "label": attrs.get("name"),
            "pleiades": attrs.get("pleiades"),
            "forms": [fold(attrs.get("name") or "")],
        }))
    log(f"  wikidata: {len(out)} candidates in the box")
    return out


def match_idai(places, opts):
    """iDAI.gazetteer has no bulk dump worth pulling, so it is queried per name.

    One cached request per distinct findspot name, and only for findspots that
    have coordinates to check the answer against.
    """
    rows = []
    for i, place in enumerate(places, 1):
        query = place["name"].split(" / ")[0].split(",")[0].strip()
        if len(query) < 3:
            continue
        url = f"{IDAI_SEARCH}?" + urllib.parse.urlencode({"q": query, "limit": 5})
        cp = geo.cache_path("idai", url, ".json")
        if cp.exists() and not opts.refresh:
            payload = json.loads(cp.read_text(encoding="utf-8"))
        elif opts.offline:
            continue
        else:
            try:
                payload = json.loads(geo.http(url, timeout=opts.timeout, retries=2))
            except Exception as exc:
                log(f"    {query}: {exc}")
                continue
            cp.write_text(json.dumps(payload), encoding="utf-8")
            time.sleep(opts.pause)
        if i % 200 == 0:
            log(f"    {i}/{len(places)} findspots queried")

        best = None
        for hit in (payload.get("result") or payload.get("results") or []):
            coords = ((hit.get("prefLocation") or {}).get("coordinates")
                      or (hit.get("prefLocation") or {}).get("shape"))
            if not coords:
                continue
            try:
                lon, lat = float(coords[0]), float(coords[1])
            except (TypeError, ValueError, IndexError):
                continue
            title = ((hit.get("prefName") or {}).get("title")
                     if isinstance(hit.get("prefName"), dict) else hit.get("prefName"))
            if not title:
                continue
            d = haversine(place["lat"], place["lon"], lat, lon)
            if d > opts.max_km:
                continue
            sim = max((similarity(f, fold(title)) for f in place["forms"]), default=0.0)
            if sim < opts.min_name:
                continue
            score = 0.65 * sim + 0.35 * (1 - d / opts.max_km)
            gid = str(hit.get("gazId") or hit.get("@id") or "").rstrip("/").split("/")[-1]
            if best is None or score > best[0]:
                best = (score, sim, d, {
                    "identifier": gid,
                    "uri": f"https://gazetteer.dainst.org/place/{gid}",
                    "label": title})
        if best and best[0] >= opts.min_score:
            rows.append(row_for(place, "idai", best, "name+distance"))
    return rows


def row_for(place, authority, best, method) -> dict:
    score, _sim, dist, cand = best
    return {
        "place_slug": place["slug"],
        "place_name": place["name"],
        "authority": authority,
        "identifier": cand.get("identifier") or "",
        "uri": cand.get("uri") or "",
        "label": cand.get("label") or "",
        "method": method,
        "score": round(score, 3),
        "distance_km": round(dist, 2),
    }


def trismegistos_rows(places, links_by_place) -> list[dict]:
    """Trismegistos publishes no reconciliation endpoint and no place dump.

    Rather than invent an identifier, each findspot gets a deterministic search
    link, marked as a search so nobody mistakes it for a resolved record. Where
    Pleiades matched, the Pleiades id goes into the query, which is what TM's
    GeoRef records are keyed on.
    """
    rows = []
    for place in places:
        pleiades = links_by_place.get((place["slug"], "pleiades"))
        term = pleiades["identifier"] if pleiades else place["name"].split(" / ")[0]
        rows.append({
            "place_slug": place["slug"],
            "place_name": place["name"],
            "authority": "trismegistos",
            "identifier": pleiades["identifier"] if pleiades else "",
            "uri": "https://www.trismegistos.org/geo/search?" +
                   urllib.parse.urlencode({"q": term}),
            "label": place["name"],
            "method": "search",
            "score": "",
            "distance_km": "",
        })
    return rows


# --------------------------------------------------------------------------
# PeriodO
# --------------------------------------------------------------------------

def fetch_periods(opts) -> int:
    """Attach canonical period URIs to the quarter-century axis.

    The bins here are a local convention. PeriodO gives published period
    definitions stable identifiers, so "Recent Etruscan" or "Republican" stops
    being a label this project made up and becomes something citable.
    """
    cp = geo.cache_path("periodo", PERIODO_DUMP, ".json")
    if cp.exists() and not opts.refresh:
        payload = json.loads(cp.read_text(encoding="utf-8"))
    elif opts.offline:
        log("periodo: no cache and --offline")
        return 0
    else:
        try:
            payload = json.loads(geo.http(PERIODO_DUMP, timeout=opts.timeout))
        except Exception as exc:
            log(f"periodo: {exc}")
            return 0
        cp.write_text(json.dumps(payload), encoding="utf-8")

    wanted = ("italy", "etruria", "roman", "italian peninsula", "sicily", "mediterranean")
    periods = []
    collections = (payload.get("authorities") or payload.get("periodCollections") or {})
    for authority in collections.values():
        for period in (authority.get("periods") or {}).values():
            spatial = " ".join(
                (c.get("label") or "") for c in (period.get("spatialCoverage") or []))
            if not any(w in spatial.lower() for w in wanted):
                continue
            start = ((period.get("start") or {}).get("in") or {}).get("year")
            stop = ((period.get("stop") or {}).get("in") or {}).get("year")
            try:
                start, stop = int(start), int(stop)
            except (TypeError, ValueError):
                continue
            if stop < -900 or start > 400:
                continue
            periods.append({
                "uri": period.get("id"),
                "label": period.get("label"),
                "start": start,
                "stop": stop,
                "coverage": spatial[:120],
                "source": (authority.get("source") or {}).get("citation", "")[:160],
            })

    periods.sort(key=lambda p: (p["start"], p["stop"]))
    PERIODS_OUT.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    with PERIODS_OUT.open("w", encoding="utf-8") as fh:
        yaml.safe_dump({"periods": periods}, fh, allow_unicode=True, sort_keys=False)
    log(f"periodo: {len(periods)} period definitions covering Italy written to "
        f"{PERIODS_OUT.relative_to(ROOT)}")
    return len(periods)


# --------------------------------------------------------------------------

def load_places() -> list[dict]:
    if not DB.exists():
        sys.exit("db/atlas.sqlite is missing. Run scripts/build_db.py first.")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    out = []
    for row in con.execute(
            "SELECT slug, name, aliases, latitude, longitude, n_inscriptions "
            "FROM places WHERE latitude IS NOT NULL ORDER BY n_inscriptions DESC"):
        aliases = json.loads(row["aliases"]) if row["aliases"] else []
        out.append({
            "slug": row["slug"], "name": row["name"],
            "lat": row["latitude"], "lon": row["longitude"],
            "n": row["n_inscriptions"],
            "forms": name_forms(row["name"], aliases),
        })
    con.close()
    return out


AUTHORITIES = {
    "pleiades": candidates_pleiades,
    "wikidata": candidates_wikidata,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated: pleiades, wikidata, idai, trismegistos")
    ap.add_argument("--max-km", type=float, default=8.0,
                    help="how far a candidate may sit from the findspot")
    ap.add_argument("--min-name", type=float, default=0.72,
                    help="minimum name similarity, 0 to 1")
    ap.add_argument("--min-score", type=float, default=0.68,
                    help="minimum combined score, 0 to 1")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--pause", type=float, default=0.3)
    ap.add_argument("--tile", type=float, default=2.0)
    ap.add_argument("--endpoint")
    ap.add_argument("--max-backoff", type=float, default=180.0)
    ap.add_argument("--ca-bundle",
                    help="PEM file of trusted roots, for networks that inspect TLS")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification entirely")
    ap.add_argument("--periods", action="store_true", help="also refresh PeriodO")
    ap.add_argument("--report", action="store_true", help="print results, write nothing")
    opts = ap.parse_args()

    wanted = ({s.strip() for s in opts.only.split(",")} if opts.only
              else {"pleiades", "wikidata", "idai", "trismegistos"})

    geo.build_ssl_context(opts)

    places = load_places()
    log(f"{len(places)} located findspots to reconcile")
    bbox = geo.corpus_bbox()

    rows: list[dict] = []
    by_place: dict[tuple, dict] = {}

    for authority in ("pleiades", "wikidata"):
        if authority not in wanted:
            continue
        log(f"{authority}:")
        grid = Grid()
        for lat, lon, cand in AUTHORITIES[authority](bbox, opts):
            grid.add(lat, lon, cand)
        hits = 0
        for place in places:
            best = best_match(place, grid, opts, opts.max_km)
            if not best:
                continue
            row = row_for(place, authority, best, "name+distance")
            rows.append(row)
            by_place[(place["slug"], authority)] = row
            hits += 1
        log(f"  matched {hits} of {len(places)} findspots "
            f"({100 * hits / max(len(places), 1):.0f}%)")

    if "idai" in wanted:
        log("idai:")
        found = match_idai(places, opts)
        rows.extend(found)
        log(f"  matched {len(found)} of {len(places)} findspots")

    if "trismegistos" in wanted:
        rows.extend(trismegistos_rows(places, by_place))
        log("trismegistos: search links written for every findspot")

    if opts.periods:
        fetch_periods(opts)

    if opts.report:
        for row in rows[:40]:
            log(f"  {row['authority']:<13} {row['place_name'][:34]:<34} -> "
                f"{row['label'][:28]:<28} {row['score']} {row['distance_km']}")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    log(f"\nwrote {OUT.relative_to(ROOT)}: {len(rows)} link(s). "
        f"Review it, then re-run: python3 scripts/build_db.py --force --site-pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
