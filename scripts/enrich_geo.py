#!/usr/bin/env python3
"""
enrich_geo.py - pull physical and cultural geography around the inscription
findspots and write assets/data/features.geojson.

Sources, each independently switchable:

  naturalearth  Natural Earth 10m: rivers, lake centrelines, lakes, and named
                physical regions (mountain ranges, peninsulas, capes, plains).
                Static files, no rate limit, public domain. This is the default
                hydrography because it is generalised for exactly this scale.
  pleiades      The Pleiades gazetteer of ancient places (CC-BY). Downloads the
                public places CSV dump once, then filters to the bounding box.
  wikidata      Wikidata SPARQL. Archaeological sites and ancient settlements.
  overpass      OpenStreetMap detail via Overpass. Off by default: a
                country-scale `out geom` request will time out on the public
                endpoints. The query is split by feature class and tiled, and
                every tile is cached, so an interrupted run resumes for free.

    python3 scripts/enrich_geo.py                          # naturalearth, pleiades, wikidata
    python3 scripts/enrich_geo.py --sources naturalearth   # hydrography only, ~15 s
    python3 scripts/enrich_geo.py --sources overpass       # peaks, volcanoes, passes
    python3 scripts/enrich_geo.py --sources overpass --osm-rivers --tile 1.0
    python3 scripts/enrich_geo.py --offline                # rebuild from cache alone
    python3 scripts/enrich_geo.py --refresh                # ignore the cache
    python3 scripts/enrich_geo.py --bbox 6 36 19 47        # minx miny maxx maxy

Everything is cached under .cache/ keyed by source and request.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
OUT = ROOT / "assets" / "data" / "features.geojson"

USER_AGENT = "epigraphic-atlas/1.1 (academic research; contact via project repository)"

NE_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
NE_LAYERS = [
    # file stem, kind, whether the layer is optional
    ("ne_10m_rivers_lake_centerlines", "river", False),
    ("ne_10m_rivers_europe", "river", True),
    ("ne_10m_lakes", "lake", False),
    ("ne_10m_lakes_europe", "lake", True),
    ("ne_10m_geography_regions_points", "landmark", True),
    ("ne_10m_geography_regions_polys", "range", True),
]

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
PLEIADES_CSV = "https://atlantides.org/downloads/pleiades/dumps/pleiades-places-latest.csv.gz"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

DEFAULT_BBOX = (6.0, 36.0, 19.0, 47.5)


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------

def log(*a):
    print(*a, file=sys.stderr, flush=True)


def cache_path(source: str, key: str, suffix: str) -> Path:
    CACHE.mkdir(exist_ok=True)
    return CACHE / f"{source}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}{suffix}"


def http(url: str, data: bytes | None = None, headers: dict | None = None,
         timeout: int = 180, retries: int = 3) -> bytes:
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):          # a bad request will not improve
                raise RuntimeError(f"{url}: HTTP {exc.code}")
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < retries:
            wait = 4 * attempt
            log(f"    {type(last).__name__}: {last}; retry {attempt}/{retries - 1} in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}: {last}")


def bbox_of(geom) -> tuple[float, float, float, float] | None:
    """Bounding box of any GeoJSON geometry, without a geometry library."""
    xs, ys = [], []

    def walk(node):
        if isinstance(node, (int, float)):
            return
        if (len(node) >= 2 and isinstance(node[0], (int, float))
                and isinstance(node[1], (int, float))):
            xs.append(node[0])
            ys.append(node[1])
            return
        for child in node:
            walk(child)

    coords = geom.get("coordinates")
    if not coords:
        return None
    walk(coords)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def intersects(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def prop(props: dict, *names):
    """Natural Earth mixes lower and upper case property names between layers."""
    lowered = {k.lower(): v for k, v in props.items()}
    for n in names:
        v = lowered.get(n.lower())
        if v not in (None, ""):
            return v
    return None


def corpus_bbox(quantile: float = 0.005) -> tuple[float, float, float, float]:
    """Robust bounding box of the located findspots.

    A handful of records sit in Africa, Pannonia and Germania Superior. Using
    the raw extremes would ask for half of Europe, so the box is trimmed to the
    central 99% of points and padded by half a degree. Override with --bbox.
    """
    import sqlite3
    db = ROOT / "db" / "atlas.sqlite"
    if not db.exists():
        return DEFAULT_BBOX
    con = sqlite3.connect(db)
    lats = [r[0] for r in con.execute(
        "SELECT latitude FROM inscriptions WHERE latitude IS NOT NULL ORDER BY latitude")]
    lons = [r[0] for r in con.execute(
        "SELECT longitude FROM inscriptions WHERE longitude IS NOT NULL ORDER BY longitude")]
    con.close()
    if len(lats) < 20:
        return DEFAULT_BBOX
    lo, hi = int(len(lats) * quantile), int(len(lats) * (1 - quantile)) - 1
    box = (lons[lo] - 0.5, lats[lo] - 0.5, lons[hi] + 0.5, lats[hi] + 0.5)
    return (max(box[0], -180), max(box[1], -90), min(box[2], 180), min(box[3], 90))


# --------------------------------------------------------------------------
# Natural Earth
# --------------------------------------------------------------------------

def fetch_naturalearth(bbox, opts):
    features = []
    for stem, kind, optional in NE_LAYERS:
        url = NE_BASE + stem + ".geojson"
        cp = cache_path("ne", stem, ".geojson")

        if cp.exists() and not opts.refresh:
            log(f"  {stem}: cache hit")
            blob = cp.read_bytes()
        elif opts.offline:
            log(f"  {stem}: no cache and --offline, skipping")
            continue
        else:
            log(f"  {stem}: downloading")
            try:
                blob = http(url, timeout=opts.timeout)
            except RuntimeError as exc:
                level = "optional layer unavailable" if optional else "FAILED"
                log(f"  {stem}: {level} ({exc})")
                continue
            cp.write_bytes(blob)

        try:
            payload = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            log(f"  {stem}: unreadable cache ({exc}); delete .cache and retry")
            continue

        kept = 0
        for f in payload.get("features", []):
            geom = f.get("geometry")
            if not geom:
                continue
            gb = bbox_of(geom)
            if not gb or not intersects(gb, bbox):
                continue
            p = f.get("properties", {}) or {}
            name = prop(p, "name_en", "name", "label")
            featurecla = (prop(p, "featurecla") or "").lower()

            this_kind = kind
            if kind == "range":
                # continent and island outlines are megabytes each and only
                # repeat what the base map already draws
                if featurecla in ("continent", "island", "islands", "island group"):
                    continue
                this_kind = "range" if "range" in featurecla or "mtn" in featurecla else "region"

            # unnamed fragments still draw useful hydrography, they just get no
            # label; only drop unnamed features whose whole point is the name
            if this_kind in ("landmark", "range", "region") and not name:
                continue

            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "kind": this_kind,
                    "name": name,
                    "class": featurecla or None,
                    "rank": prop(p, "scalerank"),
                    "source": "naturalearth",
                },
            })
            kept += 1
        log(f"  {stem}: {kept} features inside the box")
    return features


# --------------------------------------------------------------------------
# Overpass, split by feature class and tiled
# --------------------------------------------------------------------------

OSM_NODES = """
[out:json][timeout:{t}];
(
  node["natural"="peak"]["name"]({s},{w},{n},{e});
  node["natural"="volcano"]["name"]({s},{w},{n},{e});
  node["mountain_pass"="yes"]["name"]({s},{w},{n},{e});
);
out body qt;
"""

OSM_RIVERS = """
[out:json][timeout:{t}];
(
  way["waterway"="river"]["name"]({s},{w},{n},{e});
);
out geom qt;
"""


def tiles(bbox, step):
    w, s, e, n = bbox
    y = s
    while y < n:
        x = w
        while x < e:
            yield (x, y, min(x + step, e), min(y + step, n))
            x += step
        y += step


def overpass_tile(query, opts):
    cp = cache_path("overpass", query, ".json")
    if cp.exists() and not opts.refresh:
        return json.loads(cp.read_text(encoding="utf-8")), True
    if opts.offline:
        return None, False
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            raw = http(endpoint,
                       data=urllib.parse.urlencode({"data": query}).encode(),
                       headers={"Content-Type": "application/x-www-form-urlencoded"},
                       timeout=opts.timeout, retries=2)
            payload = json.loads(raw.decode("utf-8"))
            cp.write_text(json.dumps(payload), encoding="utf-8")
            return payload, False
        except (RuntimeError, json.JSONDecodeError) as exc:
            log(f"    {endpoint.split('/')[2]}: {exc}")
    return None, False


def fetch_overpass(bbox, opts):
    templates = [("peaks, volcanoes, passes", OSM_NODES)]
    if opts.osm_rivers:
        templates.append(("rivers", OSM_RIVERS))

    features, boxes = [], list(tiles(bbox, opts.tile))
    for label, tpl in templates:
        log(f"  {label}: {len(boxes)} tiles of {opts.tile} degrees")
        ok = 0
        for i, (w, s, e, n) in enumerate(boxes, 1):
            query = tpl.format(s=s, w=w, n=n, e=e, t=opts.timeout)
            payload, cached = overpass_tile(query, opts)
            if payload is None:
                log(f"    tile {i}/{len(boxes)}: no data")
                continue
            ok += 1
            for el in payload.get("elements", []):
                tags = el.get("tags", {}) or {}
                name = tags.get("name") or tags.get("name:en")
                if not name:
                    continue
                if el["type"] == "node":
                    geom = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
                    kind = ("volcano" if tags.get("natural") == "volcano"
                            else "pass" if tags.get("mountain_pass") else "peak")
                elif el.get("geometry"):
                    coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
                    if len(coords) < 2:
                        continue
                    geom = {"type": "LineString", "coordinates": coords}
                    kind = "river"
                else:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "kind": kind, "name": name, "source": "osm",
                        "osm_id": f"{el['type']}/{el['id']}",
                        "elevation": tags.get("ele"),
                    },
                })
            if not cached and not opts.offline:
                time.sleep(opts.pause)
        log(f"  {label}: {ok}/{len(boxes)} tiles retrieved")

    seen, unique = set(), []
    for f in features:
        key = f["properties"].get("osm_id")
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    log(f"  overpass: {len(unique)} features")
    return unique


# --------------------------------------------------------------------------
# Pleiades
# --------------------------------------------------------------------------

PLEIADES_KEEP = {
    "settlement", "settlement-modern", "fort", "temple", "sanctuary", "villa",
    "river", "mountain", "lake", "island", "cape", "port", "road", "pass",
    "valley", "plain", "bridge", "tumulus", "cemetery", "necropolis",
}


def fetch_pleiades(bbox, opts):
    w, s, e, n = bbox
    cp = cache_path("pleiades", PLEIADES_CSV, ".csv.gz")
    if cp.exists() and not opts.refresh:
        log("  pleiades: cache hit")
        blob = cp.read_bytes()
    elif opts.offline:
        log("  pleiades: no cache and --offline, skipping")
        return []
    else:
        log("  pleiades: downloading the places dump (~20 MB, once)")
        try:
            blob = http(PLEIADES_CSV, timeout=max(opts.timeout, 300))
        except RuntimeError as exc:
            log(f"  pleiades: {exc}")
            return []
        cp.write_bytes(blob)

    try:
        text = gzip.decompress(blob).decode("utf-8", "replace")
    except (OSError, gzip.BadGzipFile):
        text = blob.decode("utf-8", "replace")

    features = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            lat = float(row.get("reprLat") or row.get("representative_latitude") or "")
            lon = float(row.get("reprLong") or row.get("representative_longitude") or "")
        except ValueError:
            continue
        if not (s <= lat <= n and w <= lon <= e):
            continue
        types = {t.strip() for t in (row.get("featureTypes") or "").split(",") if t.strip()}
        if types and not (types & PLEIADES_KEEP):
            continue
        title = (row.get("title") or "").strip()
        if not title:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {
                "kind": "ancient-place", "name": title, "types": sorted(types)[:4],
                "source": "pleiades",
                "uri": (row.get("path") and f"https://pleiades.stoa.org{row['path']}")
                       or row.get("uri") or "",
                "start": row.get("minDate"), "end": row.get("maxDate"),
            },
        })
    log(f"  pleiades: {len(features)} features")
    return features


# --------------------------------------------------------------------------
# Wikidata
# --------------------------------------------------------------------------

WIKIDATA_QUERY = """
SELECT ?item ?itemLabel ?coord ?typeLabel WHERE {{
  VALUES ?type {{ wd:Q839954 wd:Q2221906 wd:Q1006733 wd:Q207934 }}
  ?item wdt:P31/wdt:P279* ?type ;
        wdt:P625 ?coord .
  SERVICE wikibase:box {{
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerWest  "Point({w} {s})"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerEast  "Point({e} {n})"^^geo:wktLiteral .
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,it,de,la". }}
}}
LIMIT 6000
"""


def fetch_wikidata(bbox, opts):
    w, s, e, n = bbox
    query = WIKIDATA_QUERY.format(w=w, s=s, e=e, n=n)
    cp = cache_path("wikidata", query, ".json")
    if cp.exists() and not opts.refresh:
        log("  wikidata: cache hit")
        payload = json.loads(cp.read_text(encoding="utf-8"))
    elif opts.offline:
        log("  wikidata: no cache and --offline, skipping")
        return []
    else:
        url = WIKIDATA_SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
        log("  wikidata: querying the SPARQL endpoint")
        try:
            payload = json.loads(http(url, timeout=opts.timeout,
                                      headers={"Accept": "application/sparql-results+json"}))
        except (RuntimeError, json.JSONDecodeError) as exc:
            log(f"  wikidata: {exc}")
            return []
        cp.write_text(json.dumps(payload), encoding="utf-8")

    features = []
    for row in payload.get("results", {}).get("bindings", []):
        wkt = row.get("coord", {}).get("value", "")
        if not wkt.startswith("Point("):
            continue
        try:
            lon, lat = (float(x) for x in wkt[6:-1].split())
        except ValueError:
            continue
        qid = row.get("item", {}).get("value", "").rsplit("/", 1)[-1]
        label = row.get("itemLabel", {}).get("value", "")
        if not label or label == qid:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {
                "kind": "archaeological-site", "name": label,
                "types": [row.get("typeLabel", {}).get("value", "")],
                "source": "wikidata",
                "uri": f"https://www.wikidata.org/wiki/{qid}",
            },
        })
    log(f"  wikidata: {len(features)} features")
    return features


# --------------------------------------------------------------------------

FETCHERS = {
    "naturalearth": fetch_naturalearth,
    "pleiades": fetch_pleiades,
    "wikidata": fetch_wikidata,
    "overpass": fetch_overpass,
}
DEFAULT_SOURCES = "naturalearth,pleiades,wikidata"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default=DEFAULT_SOURCES,
                    help="comma-separated subset of: " + ", ".join(FETCHERS))
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("MINX", "MINY", "MAXX", "MAXY"))
    ap.add_argument("--refresh", action="store_true", help="ignore cached responses")
    ap.add_argument("--offline", action="store_true", help="use only cached responses")
    ap.add_argument("--timeout", type=int, default=120, help="per-request timeout in seconds")
    ap.add_argument("--tile", type=float, default=2.0, help="Overpass tile size in degrees")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between Overpass tiles")
    ap.add_argument("--osm-rivers", action="store_true",
                    help="also pull named rivers from Overpass (slow; Natural Earth already "
                         "supplies generalised rivers)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    bbox = tuple(args.bbox) if args.bbox else corpus_bbox()
    log(f"bounding box: {bbox[0]:.3f} {bbox[1]:.3f} {bbox[2]:.3f} {bbox[3]:.3f}")

    wanted = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in FETCHERS]
    if unknown:
        sys.exit(f"unknown source(s): {', '.join(unknown)}")

    features = []
    for name in wanted:
        log(f"{name}:")
        try:
            features.extend(FETCHERS[name](bbox, args))
        except KeyboardInterrupt:
            log("  interrupted; keeping what has been collected so far")
            break
        except Exception as exc:
            log(f"  {name}: unexpected failure ({exc.__class__.__name__}: {exc}); skipped")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "type": "FeatureCollection",
        "metadata": {
            "sources": wanted,
            "bbox": list(bbox),
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "attribution": ("Natural Earth (public domain); "
                            "OpenStreetMap contributors (ODbL); "
                            "Pleiades, ISAW (CC-BY); Wikidata (CC0)"),
        },
        "features": features,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")

    counts = {}
    for f in features:
        k = f["properties"]["kind"]
        counts[k] = counts.get(k, 0) + 1
    log(f"wrote {args.out.relative_to(ROOT)}: {len(features)} features, "
        f"{args.out.stat().st_size / 1e6:.1f} MB")
    if counts:
        log("  " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    else:
        log("no features retrieved. The site renders fine without them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
