#!/usr/bin/env python3
"""
enrich_geo.py - fetch the external geography around the findspots and write one
file per layer under assets/data/layers/, plus a manifest the atlas reads.

Every layer is declared in scripts/geosources.yml, so adding a source is an edit
to that file rather than a code change. Nothing is bundled into a single large
GeoJSON any more: the browser downloads a layer only when the reader switches it
on, which is what keeps the map responsive once polygon sources are in play.

    python3 scripts/enrich_geo.py                      # every default layer
    python3 scripts/enrich_geo.py --all                # every declared layer
    python3 scripts/enrich_geo.py --only pleiades-polygons,awmc-roads
    python3 scripts/enrich_geo.py --group "Ancient geography"
    python3 scripts/enrich_geo.py --list               # what is declared
    python3 scripts/enrich_geo.py --offline            # rebuild from cache only
    python3 scripts/enrich_geo.py --refresh            # ignore the cache
    python3 scripts/enrich_geo.py --bbox 6 36 19 47    # minx miny maxx maxy

Responses are cached under .cache/, keyed by source and request, so an
interrupted run resumes for free and a rebuild costs nothing. Each source fails
on its own: a dead endpoint loses that layer and the site renders without it.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml is required: python -m pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
LAYER_DIR = ROOT / "assets" / "data" / "layers"
MANIFEST = ROOT / "assets" / "data" / "layers.json"
JEKYLL_MANIFEST = ROOT / "_data" / "layers.json"
CATALOGUE = Path(__file__).resolve().parent / "geosources.yml"
LEGACY = ROOT / "assets" / "data" / "features.geojson"

USER_AGENT = "epigraphic-atlas/2.0 (academic research; contact via project repository)"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
PLEIADES_GIS_ZIP = "https://atlantides.org/downloads/pleiades/gis/pleiades_gis_data.zip"
PLEIADES_PLACES_CSV = "https://atlantides.org/downloads/pleiades/dumps/pleiades-places-latest.csv.gz"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

DEFAULT_BBOX = (6.0, 36.0, 19.0, 47.5)
COORD_PRECISION = 5

PLEIADES_KEEP = {
    "settlement", "settlement-modern", "fort", "temple", "sanctuary", "villa",
    "river", "mountain", "lake", "island", "cape", "port", "road", "pass",
    "valley", "plain", "bridge", "tumulus", "cemetery", "necropolis",
    "theatre", "amphitheatre", "wall", "province", "people",
}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# The default TLS context is whatever the interpreter was built against. On
# Windows and behind an inspecting proxy that store is often missing an
# intermediate, which shows up as "self-signed certificate in certificate
# chain" on one host while every other host works. certifi's bundle fixes the
# common case; --ca-bundle points at a corporate root; --insecure is the last
# resort and says so out loud.
SSL_CONTEXT = None


def build_ssl_context(opts):
    global SSL_CONTEXT
    import ssl
    if getattr(opts, "insecure", False):
        log("! TLS verification disabled with --insecure; downloads are not authenticated")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        SSL_CONTEXT = ctx
        return
    if getattr(opts, "ca_bundle", None):
        SSL_CONTEXT = ssl.create_default_context(cafile=opts.ca_bundle)
        log(f"using the CA bundle at {opts.ca_bundle}")
        return
    try:
        import certifi
        SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        SSL_CONTEXT = None      # the interpreter default


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------

def cache_path(source: str, key: str, suffix: str) -> Path:
    CACHE.mkdir(exist_ok=True)
    return CACHE / f"{source}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}{suffix}"


class Throttled(RuntimeError):
    """The server asked us to slow down, and said for how long."""

    def __init__(self, seconds: float, message: str):
        super().__init__(message)
        self.seconds = seconds


def http(url: str, data: bytes | None = None, headers: dict | None = None,
         timeout: int = 180, retries: int = 3) -> bytes:
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):
                raise RuntimeError(f"{url}: HTTP {exc.code}")
            if exc.code in (429, 503, 504):
                # Overpass rate limits hard. Retrying four seconds later just
                # burns the next slot, so honour Retry-After when it is given
                # and wait properly when it is not.
                try:
                    wait = float(exc.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    wait = 0
                raise Throttled(max(wait, 30.0), f"HTTP {exc.code}")
            last = exc
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", exc))
            if "CERTIFICATE_VERIFY_FAILED" in reason:
                raise RuntimeError(
                    f"{url}: TLS verification failed ({reason.strip()}). "
                    f"Try: pip install certifi, or pass --ca-bundle with your "
                    f"organisation's root certificate, or --insecure to skip the check")
            last = exc
        except (TimeoutError, OSError) as exc:
            last = exc
        if attempt < retries:
            wait = 4 * attempt
            log(f"    {type(last).__name__}: {last}; retry {attempt}/{retries - 1} in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}: {last}")


def cached_bytes(source: str, url: str, opts, suffix: str = ".bin") -> bytes | None:
    cp = cache_path(source, url, suffix)
    if cp.exists() and not opts.refresh:
        log(f"    cache hit ({cp.stat().st_size / 1e6:.1f} MB)")
        return cp.read_bytes()
    if opts.offline:
        log("    no cache and --offline, skipping")
        return None
    log(f"    downloading {url.split('/')[-1] or url}")
    try:
        blob = http(url, timeout=opts.timeout)
    except RuntimeError as exc:
        log(f"    {exc}")
        return None
    cp.write_bytes(blob)
    return blob


# --------------------------------------------------------------------------
# geometry, without a geometry library
# --------------------------------------------------------------------------

def walk_coords(node, fn):
    if isinstance(node, (int, float)):
        return
    if (len(node) >= 2 and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))):
        fn(node)
        return
    for child in node:
        walk_coords(child, fn)


def bbox_of(geom) -> tuple[float, float, float, float] | None:
    xs, ys = [], []

    def take(pt):
        xs.append(pt[0])
        ys.append(pt[1])

    coords = geom.get("coordinates")
    if not coords:
        return None
    walk_coords(coords, take)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def intersects(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _dp(points: list, tol: float) -> list:
    """Douglas-Peucker on a single ring or line."""
    if len(points) < 3 or tol <= 0:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        ax, ay = points[lo][0], points[lo][1]
        bx, by = points[hi][0], points[hi][1]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best, best_i = -1.0, lo
        for i in range(lo + 1, hi):
            px, py = points[i][0], points[i][1]
            if norm < 1e-12:
                # a closed ring starts and ends on the same point, so the chord
                # is degenerate; measure from the point itself instead, or the
                # whole ring collapses to two vertices and disappears
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best, best_i = d, i
        if best > tol:
            keep[best_i] = True
            stack.append((lo, best_i))
            stack.append((best_i, hi))
    return [p for p, k in zip(points, keep) if k]


def simplify(geom: dict, tol: float) -> dict | None:
    """Thin a geometry and round its coordinates.

    Full-resolution OSM outlines and Natural Earth rings carry far more vertices
    than a map at this scale can draw. Thinning them at build time is the single
    largest thing that keeps the layer files small and the first paint fast.
    """
    def r(pt):
        return [round(pt[0], COORD_PRECISION), round(pt[1], COORD_PRECISION)]

    t = geom.get("type")
    c = geom.get("coordinates")
    if c is None:
        return None
    if t == "Point":
        return {"type": t, "coordinates": r(c)}
    if t == "MultiPoint":
        return {"type": t, "coordinates": [r(p) for p in c]}
    if t == "LineString":
        pts = [r(p) for p in _dp(c, tol)]
        return {"type": t, "coordinates": pts} if len(pts) > 1 else None
    if t == "MultiLineString":
        out = []
        for line in c:
            pts = [r(p) for p in _dp(line, tol)]
            if len(pts) > 1:
                out.append(pts)
        return {"type": t, "coordinates": out} if out else None
    if t == "Polygon":
        rings = []
        for ring in c:
            pts = [r(p) for p in _dp(ring, tol)]
            if len(pts) >= 4:
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                rings.append(pts)
        return {"type": t, "coordinates": rings} if rings else None
    if t == "MultiPolygon":
        polys = []
        for poly in c:
            rings = []
            for ring in poly:
                pts = [r(p) for p in _dp(ring, tol)]
                if len(pts) >= 4:
                    if pts[0] != pts[-1]:
                        pts.append(pts[0])
                    rings.append(pts)
            if rings:
                polys.append(rings)
        return {"type": t, "coordinates": polys} if polys else None
    return geom


def vertex_count(geom: dict) -> int:
    n = 0

    def bump(_pt):
        nonlocal n
        n += 1

    walk_coords(geom.get("coordinates") or [], bump)
    return n


def prop(props: dict, names) -> str | None:
    lowered = {str(k).lower(): v for k, v in (props or {}).items()}
    for n in names or ():
        v = lowered.get(str(n).lower())
        if v not in (None, ""):
            return str(v)
    return None


def corpus_bbox(quantile: float = 0.005) -> tuple[float, float, float, float]:
    """Bounding box of the central 99% of located findspots.

    A handful of records sit in Africa, Pannonia and Germania Superior; the raw
    extremes would ask every endpoint for half of Europe.
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
# adapters
# --------------------------------------------------------------------------

def adapt_geojson(spec, bbox, opts):
    """A static remote GeoJSON file, clipped to the box and field-mapped."""
    urls = [spec["url"]] + list(spec.get("extra_urls") or [])
    drop = {str(c).lower() for c in (spec.get("drop_classes") or ())}
    keep_fields = spec.get("keep_fields") or []
    out = []
    for url in urls:
        blob = cached_bytes(spec["id"], url, opts, ".geojson")
        if blob is None:
            continue
        try:
            payload = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            log(f"    unreadable payload ({exc}); delete .cache and retry")
            continue
        features = payload.get("features") if isinstance(payload, dict) else payload
        for f in features or []:
            geom = f.get("geometry")
            if not geom:
                continue
            gb = bbox_of(geom)
            if not gb or not intersects(gb, bbox):
                continue
            props = f.get("properties") or {}
            featurecla = (prop(props, ["featurecla", "type", "class"]) or "").lower()
            if featurecla in drop:
                continue
            name = prop(props, spec.get("name_fields") or ["name"])
            if spec.get("require_name") and not name:
                continue
            attrs = {"name": name, "class": featurecla or None}
            for field in keep_fields:
                # AWMC ships upper-case shapefile column names, Natural Earth
                # lower-case ones, so the lookup is case-insensitive
                value = prop(props, [field])
                if value not in (None, ""):
                    attrs[field.lower()] = value
            rank = prop(props, [spec.get("rank_field") or "scalerank"])
            out.append((geom, attrs, float(rank) if rank and rank.replace(".", "", 1).lstrip("-").isdigit() else 99.0))
    return out


def adapt_ndjson(spec, bbox, opts):
    blob = cached_bytes(spec["id"], spec["url"], opts, ".ndjson")
    if blob is None:
        return []
    out = []
    for line in blob.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            f = json.loads(line)
        except json.JSONDecodeError:
            continue
        geom = f.get("geometry") or (f if f.get("type") in
                                     ("LineString", "Point", "Polygon") else None)
        if not geom:
            continue
        gb = bbox_of(geom)
        if not gb or not intersects(gb, bbox):
            continue
        props = f.get("properties") or f
        out.append((geom, {"name": prop(props, spec.get("name_fields") or ["name"])}, 50.0))
    return out


class _LazyZipTables:
    """CSV members of the Pleiades bundle, decoded only when asked for.

    The bundle is 35 MB compressed and holds a dozen tables; reading them all
    into strings to use two of them wastes most of a gigabyte.
    """

    def __init__(self, blob: bytes):
        self.zf = zipfile.ZipFile(io.BytesIO(blob))
        self.index = {Path(n).name.lower(): n for n in self.zf.namelist()
                      if n.lower().endswith(".csv")}

    def names(self):
        return sorted(self.index)

    def get(self, name: str):
        member = self.index.get(name.lower())
        if member is None:
            return None
        return self.zf.read(member).decode("utf-8", "replace")

    def find(self, *fragments):
        """The first member whose name contains all of the fragments."""
        for name in sorted(self.index):
            if all(f in name for f in fragments):
                return name
        return None


def _pleiades_tables(opts):
    """The Pleiades GIS bundle, or the legacy places dump as a fallback.

    The bundle is the one worth having: it carries location_polygons.csv, which
    is where the site footprints and territories live. The legacy dump has
    representative points only.
    """
    blob = cached_bytes("pleiades", PLEIADES_GIS_ZIP, opts, ".zip")
    if blob:
        try:
            return _LazyZipTables(blob)
        except zipfile.BadZipFile:
            log("    the GIS bundle did not unzip; falling back to the places dump")
    blob = cached_bytes("pleiades", PLEIADES_PLACES_CSV, opts, ".csv.gz")
    if not blob:
        return None
    try:
        text = gzip.decompress(blob).decode("utf-8", "replace")
    except (OSError, gzip.BadGzipFile):
        text = blob.decode("utf-8", "replace")

    class _Single:
        def names(self):
            return ["places.csv"]

        def get(self, name):
            return text if name.lower() == "places.csv" else None

        def find(self, *fragments):
            return "places.csv" if all(f in "places.csv" for f in fragments) else None

    return _Single()


def _wkt_column(header) -> str | None:
    """Whichever column holds the geometry, whatever this release calls it."""
    for candidate in ("geometry_wkt", "geometry", "wkt", "reprpoint_wkt", "shape_wkt"):
        for field in header:
            if field.strip().lower() == candidate:
                return field
    for field in header:
        low = field.strip().lower()
        if "wkt" in low or low.endswith("geom") or low == "the_geom":
            return field
    return None


def adapt_pleiades(spec, bbox, opts):
    tables = _pleiades_tables(opts)
    if tables is None:
        return []
    w, s, e, n = bbox
    out = []

    if spec.get("part") == "polygons":
        member = (tables.find("location", "polygon") or tables.find("polygon")
                  or tables.find("location"))
        if not member:
            log("    no polygon table in the bundle. Members present: "
                + ", ".join(tables.names()))
            return []
        text = tables.get(member)
        reader = csv.DictReader(io.StringIO(text))
        column = _wkt_column(reader.fieldnames or [])
        if not column:
            log(f"    {member} has no geometry column. Columns: "
                + ", ".join(reader.fieldnames or []))
            return []
        log(f"    reading {member}, geometry in {column!r}")

        rows = unparsed = 0
        for row in reader:
            rows += 1
            geom = wkt_to_geojson((row.get(column) or "").strip())
            if not geom:
                unparsed += 1
                continue
            gb = bbox_of(geom)
            if not gb or not intersects(gb, bbox):
                continue
            pid = str(row.get("pid") or row.get("place_id") or row.get("id") or "").strip()
            pid = pid.rstrip("/").split("/")[-1]
            out.append((geom, {
                "name": (row.get("title") or row.get("name") or "").strip() or None,
                "uri": f"https://pleiades.stoa.org/places/{pid}" if pid else None,
                "pleiades": pid or None,
                "precision": (row.get("location_precision") or "").strip() or None,
            }, 40.0))
        log(f"    {rows} rows, {len(out)} inside the box"
            + (f", {unparsed} with unreadable geometry" if unparsed else ""))
        return out

    text = tables.get("places.csv")
    if not text:
        log("    no places table in the bundle. Members present: "
            + ", ".join(tables.names()))
        return []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            lat = float(row.get("representative_latitude") or row.get("reprLat") or "")
            lon = float(row.get("representative_longitude") or row.get("reprLong") or "")
        except ValueError:
            continue
        if not (s <= lat <= n and w <= lon <= e):
            continue
        types = {t.strip() for t in
                 (row.get("feature_types") or row.get("featureTypes") or "").replace(";", ",").split(",")
                 if t.strip()}
        if types and not (types & PLEIADES_KEEP):
            continue
        title = (row.get("title") or "").strip()
        if not title:
            continue
        pid = (row.get("id") or row.get("path") or "").strip().strip("/").split("/")[-1]
        out.append(({"type": "Point", "coordinates": [lon, lat]}, {
            "name": title,
            "types": sorted(types)[:4],
            "uri": f"https://pleiades.stoa.org/places/{pid}" if pid else None,
            "pleiades": pid or None,
            "start": row.get("min_date") or row.get("minDate"),
            "end": row.get("max_date") or row.get("maxDate"),
        }, 40.0))
    return out


def _top_level_groups(text: str) -> list[str]:
    """The contents of each outermost parenthesised group in text."""
    out, depth, begin = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            if depth == 0:
                begin = i + 1
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                out.append(text[begin:i])
            elif depth < 0:
                return out
    return out


def _wkt_ring(text: str) -> list:
    pts = []
    for pair in text.split(","):
        bits = pair.strip().split()
        if len(bits) < 2:
            return []
        try:
            pts.append([float(bits[0]), float(bits[1])])
        except ValueError:
            return []
    return pts


def wkt_to_geojson(wkt: str) -> dict | None:
    """Enough WKT for the geometries the Pleiades bundle emits.

    Parsed by scanning parenthesis depth rather than by splitting on "),(",
    which quietly fails on rings with holes and on multipolygons.
    """
    if not wkt:
        return None
    wkt = wkt.strip()
    kind = wkt.split("(", 1)[0].strip().upper()
    groups = _top_level_groups(wkt)
    if not groups:
        return None
    body = groups[0]

    if kind == "POINT":
        pts = _wkt_ring(body)
        return {"type": "Point", "coordinates": pts[0]} if pts else None

    if kind == "LINESTRING":
        pts = _wkt_ring(body)
        return {"type": "LineString", "coordinates": pts} if len(pts) > 1 else None

    if kind == "MULTILINESTRING":
        lines = [_wkt_ring(g) for g in _top_level_groups(body)]
        lines = [l for l in lines if len(l) > 1]
        return {"type": "MultiLineString", "coordinates": lines} if lines else None

    if kind == "POLYGON":
        rings = [_wkt_ring(g) for g in _top_level_groups(body)]
        rings = [r for r in rings if len(r) >= 4]
        return {"type": "Polygon", "coordinates": rings} if rings else None

    if kind == "MULTIPOLYGON":
        polys = []
        for poly in _top_level_groups(body):
            rings = [_wkt_ring(g) for g in _top_level_groups(poly)]
            rings = [r for r in rings if len(r) >= 4]
            if rings:
                polys.append(rings)
        return {"type": "MultiPolygon", "coordinates": polys} if polys else None

    if kind == "MULTIPOINT":
        pts = _wkt_ring(body.replace("(", "").replace(")", ""))
        return {"type": "MultiPoint", "coordinates": pts} if pts else None

    return None


def tiles(bbox, step):
    w, s, e, n = bbox
    y = s
    while y < n:
        x = w
        while x < e:
            yield (x, y, min(x + step, e), min(y + step, n))
            x += step
        y += step


def overpass_tile(query, layer_id, opts):
    """One tile, tried against each endpoint in turn.

    The public instances rate limit aggressively and a country-scale run asks
    for dozens of tiles, so a 429 rotates to the next endpoint rather than
    sleeping on the one that just refused. Every tile is cached on its own, so
    re-running the same command fills whatever the last run could not get and
    costs nothing for what it already has.
    """
    cp = cache_path(layer_id, query, ".json")
    if cp.exists() and not opts.refresh:
        try:
            return json.loads(cp.read_text(encoding="utf-8")), True
        except json.JSONDecodeError:
            cp.unlink()
    if opts.offline:
        return None, False

    endpoints = list(OVERPASS_ENDPOINTS)
    if opts.endpoint:
        endpoints = [opts.endpoint] + [e for e in endpoints if e != opts.endpoint]

    deferred = []
    for endpoint in endpoints:
        host = endpoint.split("/")[2]
        try:
            raw = http(endpoint,
                       data=urllib.parse.urlencode({"data": query}).encode(),
                       headers={"Content-Type": "application/x-www-form-urlencoded"},
                       timeout=opts.timeout, retries=1)
            payload = json.loads(raw.decode("utf-8"))
            cp.write_text(json.dumps(payload), encoding="utf-8")
            return payload, False
        except Throttled as exc:
            deferred.append((endpoint, host, exc.seconds, str(exc)))
        except (RuntimeError, json.JSONDecodeError) as exc:
            log(f"      {host}: {exc}")

    # every endpoint was busy rather than broken: wait out the shortest of the
    # cool-off periods they asked for and try that one again, once
    if deferred:
        endpoint, host, wait, why = min(deferred, key=lambda d: d[2])
        wait = min(max(wait, 30.0), opts.max_backoff)
        log(f"      all endpoints busy ({why}); waiting {wait:.0f}s for {host}")
        time.sleep(wait)
        try:
            raw = http(endpoint,
                       data=urllib.parse.urlencode({"data": query}).encode(),
                       headers={"Content-Type": "application/x-www-form-urlencoded"},
                       timeout=opts.timeout, retries=1)
            payload = json.loads(raw.decode("utf-8"))
            cp.write_text(json.dumps(payload), encoding="utf-8")
            return payload, False
        except (RuntimeError, Throttled, json.JSONDecodeError) as exc:
            log(f"      {host}: {exc}")
    return None, False


def adapt_overpass(spec, bbox, opts):
    """Tiled Overpass, taking ways and relations as well as nodes."""
    step = float(spec.get("tile") or opts.tile)
    boxes = list(tiles(bbox, step))
    log(f"    {len(boxes)} tiles of {step} degrees")
    out, seen = [], set()
    got = missed = 0
    for i, (w, s, e, n) in enumerate(boxes, 1):
        query = spec["query"].format(s=s, w=w, n=n, e=e, t=opts.timeout)
        payload, cached = overpass_tile(query, spec["id"], opts)
        if payload is None:
            missed += 1
            log(f"      tile {i}/{len(boxes)}: no data")
            continue
        got += 1
        for el in payload.get("elements", []):
            key = f"{el.get('type')}/{el.get('id')}"
            if key in seen:
                continue
            seen.add(key)
            tags = el.get("tags") or {}
            geom = element_geometry(el)
            if not geom:
                continue
            out.append((geom, {
                "name": tags.get("name") or tags.get("name:en"),
                "osm_id": key,
                "site_type": tags.get("site_type") or tags.get("historic"),
                "wikidata": tags.get("wikidata"),
                "elevation": tags.get("ele"),
            }, 30.0))
        if not cached and not opts.offline:
            time.sleep(opts.pause)
    log(f"    {got}/{len(boxes)} tiles retrieved" +
        (f", {missed} still missing: re-run the same command to fill them, "
         f"the cached tiles cost nothing" if missed else ""))
    return out


def element_geometry(el) -> dict | None:
    if el.get("type") == "node" and "lat" in el:
        return {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
    geom = el.get("geometry")
    if geom:
        coords = [[p["lon"], p["lat"]] for p in geom if "lon" in p]
        if len(coords) < 2:
            return None
        if len(coords) >= 4 and coords[0] == coords[-1]:
            return {"type": "Polygon", "coordinates": [coords]}
        return {"type": "LineString", "coordinates": coords}
    members = el.get("members")
    if members:
        rings = []
        for m in members:
            mg = m.get("geometry")
            if not mg:
                continue
            coords = [[p["lon"], p["lat"]] for p in mg if "lon" in p]
            if len(coords) >= 4 and coords[0] == coords[-1]:
                rings.append([coords])
        if rings:
            return {"type": "MultiPolygon", "coordinates": rings}
    return None


def adapt_wfs(spec, bbox, opts):
    """A WFS layer, fetched as tiles and cached one file per tile.

    A single request for a whole country makes GeoServer serialise the entire
    response in memory, and the connection is reset before any of it arrives.
    The Carta Litologica at 1:100,000 runs to about 47 MB per square degree, so
    even a 2.7 degree tile is a third of a gigabyte. Tiling keeps each request
    to something the server will actually finish, and because every tile is
    cached on its own an interrupted run resumes for free.

    WFS 1.0.0 by default: 2.0.0 with EPSG:4326 flips the axis order, and a
    bbox given lon/lat then returns an empty result rather than an error.
    """
    step = float(spec.get("tile") or 0.5)
    version = str(spec.get("wfs_version") or "1.0.0")
    typename = spec["typename"]
    endpoint = spec["wfs_url"]
    fields = spec.get("properties")

    boxes = list(tiles(bbox, step))
    log(f"    {len(boxes)} tiles of {step} degrees")

    keep_fields = spec.get("keep_fields") or []
    drop = {str(c).lower() for c in (spec.get("drop_classes") or ())}
    out, seen = [], set()
    got = missed = empty = 0
    total_bytes = 0

    for i, (w, s_, e, n) in enumerate(boxes, 1):
        params = {
            "service": "WFS", "version": version, "request": "GetFeature",
            "typeName": typename, "outputFormat": "application/json",
            "srsName": "EPSG:4326", "bbox": f"{w},{s_},{e},{n}",
        }
        if fields:
            params["propertyName"] = ",".join(fields)
        url = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode(params)

        cp = cache_path(spec["id"], url, ".geojson")
        if cp.exists() and not opts.refresh:
            blob = cp.read_bytes()
        elif opts.offline:
            missed += 1
            continue
        else:
            try:
                blob = http(url, timeout=opts.timeout, retries=2)
            except (RuntimeError, Throttled) as exc:
                missed += 1
                log(f"      tile {i}/{len(boxes)}: {exc}")
                continue
            cp.write_bytes(blob)
            time.sleep(opts.pause)

        total_bytes += len(blob)
        try:
            payload = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            missed += 1
            cp.unlink(missing_ok=True)
            log(f"      tile {i}/{len(boxes)}: unreadable response, discarded")
            continue

        features = payload.get("features") or []
        got += 1
        if not features:
            empty += 1
            continue

        for f in features:
            # a polygon straddling a tile edge comes back in both, and the
            # server repeats its id, so dedupe on that
            fid = f.get("id") or f.get("properties", {}).get("OBJECTID")
            if fid is not None:
                if fid in seen:
                    continue
                seen.add(fid)
            geom = f.get("geometry")
            if not geom:
                continue
            gb = bbox_of(geom)
            if not gb or not intersects(gb, bbox):
                continue
            props = f.get("properties") or {}
            featurecla = (prop(props, ["featurecla", "type", "class"]) or "").lower()
            if featurecla in drop:
                continue
            attrs = {"name": prop(props, spec.get("name_fields") or ["name"])}
            for field in keep_fields:
                value = prop(props, [field])
                if value not in (None, ""):
                    attrs[field.lower()] = value
            out.append((geom, attrs, 50.0))

        if i % 25 == 0 or i == len(boxes):
            log(f"      {i}/{len(boxes)} tiles, {len(out)} features so far, "
                f"{total_bytes / 1e6:.0f} MB fetched")

    log(f"    {got}/{len(boxes)} tiles retrieved ({empty} empty)" +
        (f", {missed} still missing: re-run to fill them, cached tiles cost nothing"
         if missed else ""))
    return out


WIKIDATA_QUERY = """
SELECT ?item ?itemLabel ?coord ?typeLabel ?pleiades WHERE {{
  VALUES ?type {{ wd:Q839954 wd:Q2221906 wd:Q1006733 wd:Q207934 }}
  ?item wdt:P31/wdt:P279* ?type ;
        wdt:P625 ?coord .
  OPTIONAL {{ ?item wdt:P1584 ?pleiades . }}
  SERVICE wikibase:box {{
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerWest  "Point({w} {s})"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerEast  "Point({e} {n})"^^geo:wktLiteral .
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,it,de,la". }}
}}
LIMIT 8000
"""


def adapt_wikidata(spec, bbox, opts):
    w, s, e, n = bbox
    query = WIKIDATA_QUERY.format(w=w, s=s, e=e, n=n)
    cp = cache_path("wikidata", query, ".json")
    if cp.exists() and not opts.refresh:
        payload = json.loads(cp.read_text(encoding="utf-8"))
    elif opts.offline:
        return []
    else:
        url = WIKIDATA_SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
        try:
            payload = json.loads(http(url, timeout=opts.timeout,
                                      headers={"Accept": "application/sparql-results+json"}))
        except (RuntimeError, json.JSONDecodeError) as exc:
            log(f"    {exc}")
            return []
        cp.write_text(json.dumps(payload), encoding="utf-8")

    out = []
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
        out.append(({"type": "Point", "coordinates": [lon, lat]}, {
            "name": label,
            "types": [row.get("typeLabel", {}).get("value", "")],
            "uri": f"https://www.wikidata.org/wiki/{qid}",
            "qid": qid,
            "pleiades": row.get("pleiades", {}).get("value"),
        }, 45.0))
    return out


def adapt_local(spec, bbox, opts):
    """A file the publisher will not serve at a stable URL, dropped into .cache."""
    for suffix in (".geojson", ".json"):
        path = CACHE / f"{spec['id']}{suffix}"
        if path.exists():
            break
    else:
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"    {path.name}: {exc}")
        return []
    keep_fields = spec.get("keep_fields") or []
    out = []
    for f in payload.get("features") or []:
        geom = f.get("geometry")
        if not geom:
            continue
        gb = bbox_of(geom)
        if not gb or not intersects(gb, bbox):
            continue
        props = f.get("properties") or {}
        # a converted shapefile carries its attribute columns, and for
        # HydroBASINS those columns are the point: HYBAS_ID is the join key and
        # PFAF_ID is what lets you walk the basin hierarchy
        attrs = {"name": prop(props, spec.get("name_fields") or ["name"])}
        for field in keep_fields or props.keys():
            value = prop(props, [field])
            if value not in (None, ""):
                attrs[field.lower()] = value
        out.append((geom, attrs, 50.0))
    return out


ADAPTERS = {
    "geojson": adapt_geojson,
    "ndjson": adapt_ndjson,
    "pleiades": adapt_pleiades,
    "overpass": adapt_overpass,
    "wikidata": adapt_wikidata,
    "wfs": adapt_wfs,
    "local": adapt_local,
}


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def load_catalogue() -> list[dict]:
    with CATALOGUE.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    sources = list(raw.get("sources") or [])
    for spec in raw.get("local_sources") or []:
        spec = dict(spec)
        spec["adapter"] = "local"
        spec.setdefault("default", False)
        sources.append(spec)
    return sources


def build_layer(spec, bbox, opts) -> dict | None:
    adapter = ADAPTERS.get(spec.get("adapter", "geojson"))
    if adapter is None:
        log(f"  {spec['id']}: unknown adapter {spec.get('adapter')!r}")
        return None

    log(f"  {spec['id']}: {spec.get('name')}")
    try:
        raw = adapter(spec, bbox, opts)
    except KeyboardInterrupt:
        raise
    except Exception as exc:                       # one dead source, one lost layer
        log(f"    unexpected failure ({exc.__class__.__name__}: {exc}); skipped")
        return None
    if not raw:
        log("    nothing retrieved")
        return None

    cap = int(spec.get("max_features") or 0)
    if cap and len(raw) > cap:
        raw.sort(key=lambda item: (item[2], -vertex_count(item[0])))
        raw = raw[:cap]

    tol = float(spec.get("simplify") or 0.0)
    kind = spec.get("kind") or "feature"
    features, before, after = [], 0, 0
    for geom, attrs, _rank in raw:
        before += vertex_count(geom)
        thinned = simplify(geom, tol)
        if not thinned:
            continue
        after += vertex_count(thinned)
        props = {k: v for k, v in attrs.items() if v not in (None, "", [])}
        props["kind"] = kind
        props["source"] = spec["id"]
        features.append({"type": "Feature", "geometry": thinned, "properties": props})

    if not features:
        log("    every feature fell outside the box")
        return None

    LAYER_DIR.mkdir(parents=True, exist_ok=True)
    path = LAYER_DIR / f"{spec['id']}.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                               ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    size = path.stat().st_size
    thinning = f", {100 * (1 - after / before):.0f}% of vertices dropped" if before else ""
    log(f"    {len(features)} features, {size / 1e6:.2f} MB{thinning}")

    return {
        "id": spec["id"],
        "group": spec.get("group") or "Other",
        "name": spec.get("name") or spec["id"],
        "kind": kind,
        "file": f"layers/{spec['id']}.geojson",
        "features": len(features),
        "bytes": size,
        "default": bool(spec.get("default")),
        "licence": spec.get("licence"),
        "attribution": spec.get("attribution"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated layer ids")
    ap.add_argument("--group", action="append", help="build every layer in this group")
    ap.add_argument("--all", action="store_true", help="build every declared layer")
    ap.add_argument("--list", action="store_true", help="print the catalogue and stop")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("MINX", "MINY", "MAXX", "MAXY"))
    ap.add_argument("--refresh", action="store_true", help="ignore cached responses")
    ap.add_argument("--offline", action="store_true", help="use only cached responses")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--tile", type=float, default=2.0, help="default Overpass tile size")
    ap.add_argument("--pause", type=float, default=2.0,
                    help="seconds between Overpass tiles; raise it if you keep meeting 429")
    ap.add_argument("--endpoint", help="try this Overpass endpoint first")
    ap.add_argument("--max-backoff", type=float, default=180.0,
                    help="longest wait when every Overpass endpoint is rate limiting")
    ap.add_argument("--ca-bundle",
                    help="PEM file of trusted roots, for networks that inspect TLS")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification entirely; downloads are not authenticated")
    ap.add_argument("--keep-legacy", action="store_true",
                    help="also write the old combined assets/data/features.geojson")
    opts = ap.parse_args()

    build_ssl_context(opts)

    catalogue = load_catalogue()
    if opts.list:
        width = max(len(s["id"]) for s in catalogue)
        for spec in catalogue:
            mark = "*" if spec.get("default") else " "
            print(f" {mark} {spec['id']:<{width}}  {spec.get('group', ''):<22} "
                  f"{spec.get('name', '')}  [{spec.get('licence', '?')}]")
        print("\n* built by default. --all builds everything, --only picks ids.")
        return 0

    wanted = catalogue
    if opts.only:
        ids = {s.strip() for s in opts.only.split(",") if s.strip()}
        unknown = ids - {s["id"] for s in catalogue}
        if unknown:
            sys.exit(f"unknown layer(s): {', '.join(sorted(unknown))}")
        wanted = [s for s in catalogue if s["id"] in ids]
    elif opts.group:
        groups = {g.lower() for g in opts.group}
        wanted = [s for s in catalogue if (s.get("group") or "").lower() in groups]
    elif not opts.all:
        wanted = [s for s in catalogue if s.get("default")]

    bbox = tuple(opts.bbox) if opts.bbox else corpus_bbox()
    log(f"bounding box: {bbox[0]:.3f} {bbox[1]:.3f} {bbox[2]:.3f} {bbox[3]:.3f}")
    log(f"building {len(wanted)} layer(s) of {len(catalogue)} declared")

    # a rebuild of two layers must not delete the other twenty
    existing = {}
    if MANIFEST.exists():
        try:
            existing = {l["id"]: l for l in json.loads(MANIFEST.read_text())["layers"]}
        except (json.JSONDecodeError, KeyError, OSError):
            existing = {}

    # Adopt any layer file on disk that the manifest does not mention. The
    # manifest is a cache of the directory, not the authority for it: replacing
    # the file, as an unzip over the project does, would otherwise hide a layer
    # that is sitting right there and make it look like the build had failed.
    by_id = {spec["id"]: spec for spec in catalogue}
    adopted = []
    if LAYER_DIR.exists():
        for path in sorted(LAYER_DIR.glob("*.geojson")):
            layer_id = path.stem
            if layer_id in existing or layer_id not in by_id:
                continue
            spec = by_id[layer_id]
            try:
                count = len(json.loads(path.read_text(encoding="utf-8"))["features"])
            except (json.JSONDecodeError, KeyError, OSError):
                continue
            existing[layer_id] = {
                "id": layer_id,
                "group": spec.get("group") or "Other",
                "name": spec.get("name") or layer_id,
                "kind": spec.get("kind") or "feature",
                "file": f"layers/{layer_id}.geojson",
                "features": count,
                "bytes": path.stat().st_size,
                "default": bool(spec.get("default")),
                "licence": spec.get("licence"),
                "attribution": spec.get("attribution"),
            }
            adopted.append(layer_id)
    if adopted:
        log(f"adopted {len(adopted)} layer file(s) missing from the manifest: "
            + ", ".join(adopted))

    built = 0
    for spec in wanted:
        try:
            entry = build_layer(spec, bbox, opts)
        except KeyboardInterrupt:
            log("interrupted; keeping the layers already written")
            break
        if entry:
            existing[entry["id"]] = entry
            built += 1

    order = {s["id"]: i for i, s in enumerate(catalogue)}
    layers = sorted((l for l in existing.values() if (LAYER_DIR / f"{l['id']}.geojson").exists()),
                    key=lambda l: order.get(l["id"], 999))

    manifest = json.dumps({
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bbox": list(bbox),
        "layers": layers,
    }, ensure_ascii=False, indent=1)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(manifest, encoding="utf-8")
    # the same file under _data/, so the About page can list the layers and
    # their licences without fetching anything
    JEKYLL_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    JEKYLL_MANIFEST.write_text(manifest, encoding="utf-8")

    if opts.keep_legacy:
        merged = []
        for entry in layers:
            path = LAYER_DIR / f"{entry['id']}.geojson"
            merged.extend(json.loads(path.read_text())["features"])
        LEGACY.write_text(json.dumps({"type": "FeatureCollection", "features": merged},
                                     ensure_ascii=False, separators=(",", ":")),
                          encoding="utf-8")

    total = sum(l["bytes"] for l in layers)
    on_load = sum(l["bytes"] for l in layers if l["default"])
    log(f"\n{built} layer(s) rebuilt, {len(layers)} available, "
        f"{total / 1e6:.1f} MB on disk, {on_load / 1e6:.1f} MB loaded on first paint")
    for entry in layers:
        mark = "*" if entry["default"] else " "
        log(f" {mark} {entry['id']:<22} {entry['features']:>7} features  "
            f"{entry['bytes'] / 1e6:>6.2f} MB  {entry['licence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
