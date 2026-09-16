#!/usr/bin/env python3
"""
Materialise the Project MERCURY sources (https://projectmercury.eu/datasets/)
that can actually be extracted for Italy 700 BCE - 1 BCE, and build their
25-year aoristic time-weight tables for economy_field.py v0.4.

Every dataset on the MERCURY inventory was checked (see data_sources_v04.csv
for the full ledger). Four are materialised here:

  1. Coin Hoards of the Roman Republic (CHRR, numismatics.org/chrr; CC BY-NC).
     Nomisma RDF dump -> one row per hoard with closing date (nmo:hasClosingDate)
     and GeoNames findspot; coordinates joined from findspots.geojson.
     -> chrr_hoards_italy.csv, chrr_hoard_time_weights_25yr.csv
     Event-type evidence (a deposition). Closing date = latest coin; deposition
     window taken as uniform on [closing-10, closing+15] (Crawford type-date
     tolerance + deposition lag). Hoards without closing date are excluded.

  2. OXREP Olive oil and wine presses (oxrep.classics.ox.ac.uk).
     Session CSV export; Italian sites with structured date phases.
     -> presses_italy.csv, presses_time_weights_25yr.csv
     Presence-type evidence (installation active over its phase); weight =
     number of presses x fraction of the 25-yr bin covered by a dated phase.
     Sites with only -999/999 dates are excluded, never back-projected.

  3. Pleiades places (pleiades.stoa.org, CC BY; dump at atlantides.org).
     Places in the Italian grid box with precise/rough coordinates and
     Barrington-derived period attestations, classed into three sources:
       pleiades_settlement  settlement / fortified-settlement / urban / vicus / pagus
       pleiades_production  villa / estate / centuriation / production / quarry /
                            mine / salt-pan / fishpond
       pleiades_transport   bridge / road / station / canal / port / harbor /
                            anchorage / lighthouse / shipshed / causeway / milestone
     -> pleiades_places_italy.csv, pleiades_time_weights_25yr.csv
     Presence-type, with the same date-heaping logic v0.2 applied to Hanson's
     catalogue dates: attested periods (Pleiades vocabulary bounds, e.g.
     archaic 750-550, classical 550-330, hellenistic-republican 330-30, roman
     30 BCE-300 CE) are merged into contiguous blocks; a block's start is
     taken as uniformly distributed within its first attested period and its
     end within its last, so presence probability ramps linearly across those
     periods instead of stepping to 1 at the period boundary (a place first
     attested "hellenistic-republican" is not assumed to exist in 330 BCE).
     A single-period attestation gives P(active) = 2(t-a)(b-t)/(b-a)^2.
     Bin weight = mean presence probability over the 25-yr bin.

  4. OXREP Shipwrecks (refresh of the v0.1 extraction).
     Italy-filtered session table (one row per wreck with OXREP location id)
     + per-location popups, the only place OXREP exposes wreck coordinates.
     -> shipwrecks_oxrep_italy_v04.csv, shipwreck_time_weights_25yr_v04.csv
     Event-type: probability spread uniformly over [wreckage after, before].

Not materialisable / not usable (recorded in data_sources_v04.csv):
  AWMC shapefiles (aqueducts, bridges, canals, centuriation, roads, urban
  areas): listed URLs 404; the same features enter through Pleiades with
  period attestation. OXREP stone quarries: coordinates only, free-text
  dating -> undated, excluded. OXREP water technology: Egyptian papyri only.
  Orbis: imperial network without segment chronology (same objection as
  Itiner-e). CRRO/OCRE/CHRE: type catalogues or post-30 BCE. Trismegistos,
  FACEM, RTAR, Samian, kilns/pottery of Britain, Karanis, Levant ceramics:
  web-only, out of area, or out of period.

Raw downloads are cached in data/economy/raw_v04/ so the build is
reproducible offline.

    python scripts/economy_sources_v04.py
"""
from __future__ import annotations

import gzip
import html
import http.cookiejar
import io
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ECON = ROOT / "data" / "economy"
RAW = ECON / "raw_v04"

LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = 6.5, 19.0, 35.5, 47.5   # v0.2 grid box
BIN_STARTS = np.arange(-700, 0, 25)
BIN_W = 25

UA = {"User-Agent": "epigraphic-atlas economy build (research; contact via repo)"}


# --------------------------------------------------------------- fetching
def fetch(url, dest, opener=None, binary=True):
    """GET url into RAW/dest unless cached; return the path."""
    dest = RAW / dest
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    RAW.mkdir(parents=True, exist_ok=True)
    print(f"  fetching {url}", flush=True)
    req = urllib.request.Request(url, headers=UA)
    op = opener or urllib.request.build_opener()
    with op.open(req, timeout=300) as r:
        data = r.read()
    dest.write_bytes(data)
    return dest


def oxrep_session_export(db):
    """OXREP databases export the current session's table via csv.php;
    coordinates are only exposed in the map frame's JS `sites` array."""
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    base = "https://oxrep.classics.ox.ac.uk"
    csv_path = RAW / f"oxrep_{db}.csv"
    map_path = RAW / f"oxrep_{db}_mapframe.html"
    if not (csv_path.exists() and map_path.exists()):
        op.open(urllib.request.Request(f"{base}/databases/{db}/", headers=UA),
                timeout=120).read()
        fetch(f"{base}/oxrep/modules/databases/output/csv.php", csv_path.name, op)
        fetch(f"{base}/oxrep/modules/databases/map/v3/mapframe.php?fs=yes&mq=yes",
              map_path.name, op)
    df = pd.read_csv(csv_path, encoding="latin-1")
    txt = map_path.read_text(encoding="latin-1")
    arr = re.search(r"var sites\s*=\s*(\[.*?\]);\s*\n", txt, re.S)
    pat = re.compile(r"\[\s*'((?:[^'\\]|\\.)*)',\s*([\d.-]+),\s*([\d.-]+),"
                     r"\s*(\d+),\s*'([^']*)',\s*'([^']*)'\s*\]")
    rows = [(a.replace("\\'", "'"), float(b), float(c), int(d), e, f)
            for a, b, c, d, e, f in pat.findall(arr.group(1) if arr else "")]
    sites = pd.DataFrame(rows, columns=["sitename", "latitude", "longitude",
                                        "map_id", "kind", "url"])
    return df, sites


def overlap_weights(lo, hi, kind):
    """25-yr bin weights for an interval [lo, hi].
    kind='event': probabilities (sum to 1 over the interval);
    kind='presence': fraction of each bin covered."""
    lo, hi = float(lo), float(hi)
    if hi <= lo:
        hi = lo + 1.0
    b0 = BIN_STARTS.astype(float)
    ov = np.clip(np.minimum(b0 + BIN_W, hi) - np.maximum(b0, lo), 0, None)
    if kind == "event":
        return ov / (hi - lo)
    return ov / BIN_W


def explode(df, id_cols, lo_col, hi_col, kind, mag=None):
    """Long table of (bin, weight) per row of df."""
    out = []
    for _, r in df.iterrows():
        w = overlap_weights(r[lo_col], r[hi_col], kind)
        if mag is not None:
            w = w * float(r[mag])
        for bi in np.where(w > 1e-9)[0]:
            rec = {c: r[c] for c in id_cols}
            rec.update({"bin_start_year": int(BIN_STARTS[bi]),
                        "bin_end_year": int(BIN_STARTS[bi] + BIN_W),
                        "weight": round(float(w[bi]), 6)})
            out.append(rec)
    return pd.DataFrame(out)


# --------------------------------------------------------------- 1. CHRR
NS = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
      "nmo": "http://nomisma.org/ontology#",
      "skos": "http://www.w3.org/2004/02/skos/core#",
      "crm": "http://www.cidoc-crm.org/cidoc-crm/",
      "rdfs": "http://www.w3.org/2000/01/rdf-schema#"}


def build_chrr():
    print("CHRR coin hoards ...", flush=True)
    rdf = fetch("http://numismatics.org/chrr/nomisma.rdf", "chrr_nomisma.rdf")
    gj = fetch("http://numismatics.org/chrr/findspots.geojson", "chrr_findspots.geojson")
    root = ET.parse(rdf).getroot()
    contents = {}
    for c in root.iter():
        about = c.get(f"{{{NS['rdf']}}}about") or ""
        if about.endswith("#contents"):
            contents[about[:-9]] = len(c.findall("nmo:hasTypeSeriesItem", NS))
    rows = []
    for h in root.findall("nmo:Hoard", NS):
        uri = h.get(f"{{{NS['rdf']}}}about")
        place = h.find(".//crm:E53_Place", NS)
        geon = None
        plab = None
        if place is not None:
            plab = place.findtext("rdfs:label", namespaces=NS)
            fw = place.find("crm:P89_falls_within", NS)
            geon = fw.get(f"{{{NS['rdf']}}}resource") if fw is not None else None
        cd = h.findtext("nmo:hasClosingDate", namespaces=NS)
        rows.append({"hoard_id": uri.rsplit("/", 1)[-1],
                     "label": h.findtext("skos:prefLabel", namespaces=NS),
                     "findspot": plab, "geonames_uri": geon,
                     "closing_year": int(cd) if cd else np.nan,
                     "n_coin_types": contents.get(uri, 0)})
    hd = pd.DataFrame(rows)
    hd["country"] = hd.findspot.fillna("").str.rsplit(",", n=1).str[-1].str.strip()
    feats = json.loads(gj.read_text(encoding="utf-8"))["features"]
    xy = pd.DataFrame([{"geonames_uri": f["properties"].get("uri"),
                        "longitude": f["geometry"]["coordinates"][0],
                        "latitude": f["geometry"]["coordinates"][1],
                        "findspot_radius_km": f["properties"].get("radius")}
                       for f in feats if f.get("geometry")]).drop_duplicates("geonames_uri")
    hd = hd.merge(xy, on="geonames_uri", how="left")
    it = hd[hd.country.isin(["Italy", "Sicily", "Sardinia"])
            & hd.longitude.between(LON_MIN, LON_MAX) & hd.latitude.between(LAT_MIN, LAT_MAX)].copy()
    it["dated"] = it.closing_year.notna()
    it.to_csv(ECON / "chrr_hoards_italy.csv", index=False)
    d = it[it.dated].copy()
    d["dep_start"] = d.closing_year - 10
    d["dep_end"] = d.closing_year + 15
    tw = explode(d, ["hoard_id", "label", "longitude", "latitude", "closing_year"],
                 "dep_start", "dep_end", "event")
    tw.to_csv(ECON / "chrr_hoard_time_weights_25yr.csv", index=False)
    print(f"  {len(hd)} hoards total; {len(it)} in Italy/Sicily/Sardinia box; "
          f"{len(d)} with closing date (range {int(d.closing_year.min())}..{int(d.closing_year.max())}); "
          f"{len(tw)} hoard x bin rows, in-window mass {tw.weight.sum():.1f}")
    return {"records_total": len(hd), "records_italy": len(it), "records_used": len(d),
            "in_window_mass": round(float(tw.weight.sum()), 1)}


# --------------------------------------------------------------- 2. presses
def build_presses():
    print("OXREP presses ...", flush=True)
    df, sites = oxrep_session_export("olive_oil_and_wine_presses_database")
    it = df[df.country == "Italy"].copy()
    it = it.rename(columns={"loclong": "longitude", "loclat": "latitude"})
    it["site_id"] = ["press_%03d" % i for i in range(len(it))]
    phases = []
    for _, r in it.iterrows():
        for k in (0, 1):
            lo, hi = r[f"post_{k}"], r[f"ante_{k}"]
            if pd.isna(lo) or pd.isna(hi) or lo <= -999 or hi >= 999:
                continue          # undated phase: not back-projected
            phases.append({"site_id": r.site_id, "sitename": r.sitename,
                           "locname": r.locname, "longitude": r.longitude,
                           "latitude": r.latitude, "presses": r.presses,
                           "phase": k, "phase_start": lo, "phase_end": hi})
    ph = pd.DataFrame(phases)
    it["n_dated_phases"] = it.site_id.map(ph.groupby("site_id").size()).fillna(0).astype(int)
    it.to_csv(ECON / "presses_italy.csv", index=False)
    tw = explode(ph, ["site_id", "sitename", "locname", "longitude", "latitude", "phase"],
                 "phase_start", "phase_end", "presence", mag="presses")
    # a site with two overlapping dated phases must not double count presence
    tw = (tw.groupby(["site_id", "sitename", "locname", "longitude", "latitude",
                      "bin_start_year", "bin_end_year"], as_index=False)
            .weight.max())
    tw.to_csv(ECON / "presses_time_weights_25yr.csv", index=False)
    used = tw.site_id.nunique()
    print(f"  {len(df)} sites total; {len(it)} Italy; {used} with a dated phase in window; "
          f"{len(tw)} site x bin rows, mass {tw.weight.sum():.1f} press-bins")
    return {"records_total": len(df), "records_italy": len(it), "records_used": used,
            "in_window_mass": round(float(tw.weight.sum()), 1)}


# --------------------------------------------------------------- 3. Pleiades
PLEIADES_CLASSES = {
    "pleiades_settlement": {"settlement", "fortified-settlement", "urban", "vicus", "pagus"},
    "pleiades_production": {"villa", "estate", "centuriation", "production", "quarry",
                            "mine", "mine-2", "salt-pan-salina", "fishpond"},
    "pleiades_transport": {"bridge", "road", "station", "canal", "port", "harbor",
                           "anchorage", "lighthouse", "shipshed", "causeway", "milestone"},
}
# fallback bounds for the Barrington periods if the vocabulary page changes
FALLBACK_PERIODS = {"archaic": (-750, -550), "classical": (-550, -330),
                    "hellenistic-republican": (-330, -30), "roman": (-30, 300),
                    "late-antique": (300, 640)}


def pleiades_periods():
    p = RAW / "pleiades_time_periods.json"
    if p.exists():
        return {k: tuple(v) for k, v in json.loads(p.read_text()).items()}
    page = fetch("https://pleiades.stoa.org/vocabularies/time-periods",
                 "pleiades_time_periods.html")
    txt = page.read_text(encoding="utf-8", errors="replace")
    out = {}
    link = re.compile(r'time-periods/([a-z0-9\-]+)"[^>]*>([^<]*)<')
    span = re.compile(r'\(\s*(AD\s*)?(\d+)\s*(BC)?\s*-\s*(AD\s*)?(\d+)\s*(BC)?\s*\)')
    for m in link.finditer(txt):
        key, label = m.group(1), html.unescape(m.group(2))
        dates = span.findall(label)
        if not dates:
            continue
        ad1, y1, bc1, ad2, y2, bc2 = dates[-1]          # last "(start - end)" in the link text
        a = -int(y1) if bc1 else int(y1)
        b = -int(y2) if bc2 else int(y2)
        if key not in out:
            out[key] = (min(a, b), max(a, b))
    for k, v in FALLBACK_PERIODS.items():
        out.setdefault(k, v)
    p.write_text(json.dumps(out, indent=0))
    return out


def period_presence(intervals):
    """Presence probability per 25-yr bin from a set of attested period
    intervals (see module docstring): start ~ U(first period of block),
    end ~ U(last period of block); averaged over each bin at yearly steps."""
    iv = sorted(set(intervals))
    blocks = []                                    # [a, b, first_end, last_start]
    for a, b in iv:
        if blocks and a <= blocks[-1][1]:
            blocks[-1][1] = max(blocks[-1][1], b)
            blocks[-1][3] = max(blocks[-1][3], a)
        else:
            blocks.append([a, b, b, a])
    t = (BIN_STARTS[:, None] + np.arange(0.5, BIN_W, 1.0)[None, :]).astype(float)
    w = np.zeros_like(t)
    for a, b, a1, b0 in blocks:
        if a1 == b and b0 == a:                    # single attested period
            pr = 2.0 * (t - a) * (b - t) / (b - a) ** 2
        else:
            pr = np.clip((t - a) / (a1 - a), 0, 1) * np.clip((b - t) / (b - b0), 0, 1)
        pr[(t < a) | (t > b)] = 0.0
        w = np.maximum(w, pr)
    return w.mean(axis=1)


def build_pleiades():
    print("Pleiades places ...", flush=True)
    gz = fetch("https://atlantides.org/downloads/pleiades/dumps/pleiades-places-latest.csv.gz",
               "pleiades-places-latest.csv.gz")
    with gzip.open(gz, "rb") as f:
        p = pd.read_csv(io.BytesIO(f.read()), low_memory=False)
    periods = pleiades_periods()
    box = p[p.reprLong.between(LON_MIN, LON_MAX) & p.reprLat.between(LAT_MIN, LAT_MAX)
            & p.locationPrecision.isin(["precise", "rough"])].copy()
    box = box[box.featureTypes.notna() & box.timePeriodsKeys.notna()]
    box["feature_set"] = box.featureTypes.apply(lambda s: {t.strip() for t in s.split(",")})
    for src, cls in PLEIADES_CLASSES.items():
        box[src] = box.feature_set.apply(lambda fs: bool(fs & cls))
    keep = box[box[list(PLEIADES_CLASSES)].any(axis=1)].copy()

    def intervals(keys):
        iv = []
        for k in keys.split(","):
            k = k.strip()
            if k in periods:
                iv.append(periods[k])
        return iv

    keep["intervals"] = keep.timePeriodsKeys.apply(intervals)
    unknown = sorted({k.strip() for s in keep.timePeriodsKeys for k in s.split(",")
                      if k.strip() not in periods})
    if unknown:
        print(f"  WARNING unmapped period keys ignored: {unknown}")
    rows, place_rows = [], []
    for _, r in keep.iterrows():
        w = period_presence(r.intervals) if r.intervals else np.zeros(len(BIN_STARTS))
        srcs = [s for s in PLEIADES_CLASSES if r[s]]
        place_rows.append({"pleiades_id": r.id, "title": r.title,
                           "longitude": r.reprLong, "latitude": r.reprLat,
                           "location_precision": r.locationPrecision,
                           "feature_types": r.featureTypes,
                           "time_periods": r.timePeriodsKeys,
                           "sources": ";".join(srcs),
                           "in_window_bins": round(float(w.sum()), 3)})
        if w.sum() <= 0:
            continue
        for s in srcs:
            for bi in np.where(w > 1e-9)[0]:
                rows.append({"source_type": s, "pleiades_id": r.id, "title": r.title,
                             "longitude": r.reprLong, "latitude": r.reprLat,
                             "bin_start_year": int(BIN_STARTS[bi]),
                             "bin_end_year": int(BIN_STARTS[bi] + BIN_W),
                             "weight": round(float(w[bi]), 6)})
    pl = pd.DataFrame(place_rows)
    pl.to_csv(ECON / "pleiades_places_italy.csv", index=False)
    tw = pd.DataFrame(rows)
    tw.to_csv(ECON / "pleiades_time_weights_25yr.csv", index=False)
    stats = {}
    for s in PLEIADES_CLASSES:
        sub = tw[tw.source_type == s]
        n = sub.pleiades_id.nunique()
        early = sub[sub.bin_start_year < -400].pleiades_id.nunique()
        print(f"  {s}: {int(keep[s].sum())} classed places, {n} with mass in window "
              f"({early} before 400 BCE), mass {sub.weight.sum():.1f} place-bins")
        stats[s] = {"records_total": int(keep[s].sum()), "records_used": n,
                    "records_pre400": early, "in_window_mass": round(float(sub.weight.sum()), 1)}
    print(f"  {len(p)} places in dump; {len(box)} in box (precise/rough); "
          f"{len(keep)} in an economic class")
    return stats


# --------------------------------------------------------------- 4. wrecks
def oxrep_wreck_table():
    """Italy-filtered shipwreck table (one row per wreck, with OXREP location
    ids) and per-location popups, which are the only place OXREP exposes
    per-wreck coordinates."""
    base = "https://oxrep.classics.ox.ac.uk"
    tab = RAW / "oxrep_shipwrecks_italy_table.html"
    if not tab.exists():
        # urllib trips over OXREP's post-filter redirect loop; curl does not
        import subprocess
        cj = str(RAW / "oxrep_cookies.txt")
        if Path(cj).exists():
            Path(cj).unlink()
        def curl(url, *extra):
            r = subprocess.run(["curl", "-sS", "-L", "--max-time", "180", "-c", cj, "-b", cj,
                                "-A", UA["User-Agent"], *extra, url],
                               capture_output=True, check=True)
            return r.stdout
        curl(f"{base}/databases/shipwrecks_database/")
        menu = curl(f"{base}/oxrep/modules/databases/menu/dbfiltermenu.php?type=fld_sitecountry"
                    ).decode("latin-1")
        m = re.search(r"name='(\d+)'></td><td>Italy</td>", menu)
        if not m:
            raise RuntimeError("OXREP country filter: Italy not found")
        curl(f"{base}/oxrep/modules/databases/dbengine.php", "-X", "POST", "--data",
             f"type=filter&field=3_sitecountry&formtype=list&{m.group(1)}=on")
        page = curl(f"{base}/databases/shipwrecks_database/", "-X", "POST", "--data",
                    "querylimit=400&queryoffset=0")
        if b"of 367" not in page and b"Showing records 1 - " not in page:
            raise RuntimeError("OXREP shipwreck table export failed")
        tab.write_bytes(page)
    txt = tab.read_text(encoding="latin-1")
    cell = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", txt, re.S):
        cells = cell.findall(tr)
        if len(cells) < 9 or "popup.php?loc=" not in tr:
            continue
        def txt_of(c):
            return html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
        loc = re.search(r"popup\.php\?loc=(\d+)", cells[4])
        ste = re.search(r"popup\.php\?ste=(\d+)", cells[3])
        def num(c):
            v = txt_of(c)
            try:
                return float(v)
            except ValueError:
                return np.nan
        rows.append({"loc_id": int(loc.group(1)), "site_id": int(ste.group(1)) if ste else -1,
                     "country": txt_of(cells[1]), "sea_area": txt_of(cells[2]),
                     "sitename": txt_of(cells[3]), "locname": txt_of(cells[4]),
                     "depth_m": num(cells[5]), "wreckage_after": num(cells[7]),
                     "wreckage_before": num(cells[8])})
    return pd.DataFrame(rows)


def oxrep_popup_coords(loc_id):
    d = RAW / "oxrep_wreck_popups"
    d.mkdir(exist_ok=True)
    f = d / f"loc_{loc_id}.html"
    if not f.exists():
        req = urllib.request.Request(f"https://oxrep.classics.ox.ac.uk/popup.php?loc={loc_id}",
                                     headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            f.write_bytes(r.read())
    t = f.read_text(encoding="latin-1")
    m = re.search(r"([\d.]+)&deg;([NS])\s+([\d.]+)&deg;([EW])", t)
    if not m:
        return np.nan, np.nan
    lat = float(m.group(1)) * (1 if m.group(2) == "N" else -1)
    lon = float(m.group(3)) * (1 if m.group(4) == "E" else -1)
    return lat, lon


def build_wrecks():
    print("OXREP shipwrecks (refresh) ...", flush=True)
    it = oxrep_wreck_table()
    it["dated"] = (it.wreckage_after > -999) & (it.wreckage_before < 999)         & it.wreckage_after.notna() & it.wreckage_before.notna()
    it["in_window"] = it.dated & (it.wreckage_after <= -1) & (it.wreckage_before >= -700)
    lat, lon = [], []
    for i, lid in enumerate(it.loc_id):
        if it.in_window.iloc[i]:
            a, b = oxrep_popup_coords(lid)
        else:
            a, b = np.nan, np.nan          # popups only fetched where needed
        lat.append(a)
        lon.append(b)
    it["latitude"], it["longitude"] = lat, lon
    it["coord_source"] = np.where(it.latitude.notna(), "oxrep_popup", "")
    # OXREP gives "Coordinates: None given" for many Italian wrecks; the v0.1
    # build had geocoded some of these by name — reuse those rather than lose
    # evidence that v0.3 already used (flagged so it can be dropped)
    old = pd.read_csv(ECON / "source_points_all.csv")
    old = old[old.source_type == "maritime_shipwreck"].drop_duplicates("name")              .set_index("name")[["latitude", "longitude"]]
    for i in np.where(it.latitude.isna() & it.in_window)[0]:
        for key in (it.locname.iloc[i], it.sitename.iloc[i]):
            if key in old.index:
                it.iloc[i, it.columns.get_loc("latitude")] = old.at[key, "latitude"]
                it.iloc[i, it.columns.get_loc("longitude")] = old.at[key, "longitude"]
                it.iloc[i, it.columns.get_loc("coord_source")] = "v01_geocoded"
                break
    it["has_coords"] = it.latitude.notna()
    it.to_csv(ECON / "shipwrecks_oxrep_italy_v04.csv", index=False)
    d = it[it.in_window & it.has_coords].copy()
    d["wreck_id"] = "oxrep_loc_" + d.loc_id.astype(str)
    tw = explode(d, ["wreck_id", "sitename", "locname", "longitude", "latitude",
                     "wreckage_after", "wreckage_before"],
                 "wreckage_after", "wreckage_before", "event")
    tw.to_csv(ECON / "shipwreck_time_weights_25yr_v04.csv", index=False)
    print(f"  {len(it)} Italian wrecks in OXREP; {int(it.in_window.sum())} dated in window; "
          f"{len(d)} of those with coordinates "
          f"({int((d.coord_source == 'oxrep_popup').sum())} OXREP, "
          f"{int((d.coord_source == 'v01_geocoded').sum())} v0.1 geocoded); "
          f"in-window mass {tw.weight.sum():.1f}")
    return {"records_total": 1784, "records_italy": len(it), "records_used": len(d),
            "in_window_mass": round(float(tw.weight.sum()), 1)}


# --------------------------------------------------------------- ledger
def write_ledger(chrr, presses, ple, wrecks):
    rows = [
        # retained v0.2/v0.3 components
        ("Hanson / OXREP Cities", "urban/settlement", "integrated (v0.1)", 372,
         "aoristic activation from heaped catalogue start dates", "open research database", ""),
        ("AIDA Italian radiocarbon archive", "archaeological occupation", "integrated (v0.1)", 185,
         "IntCal20 calibrated probability, site x bin capped at 1", "open archive", ""),
        ("OXREP Shipwrecks (2026 export)", "maritime exchange", "integrated (refreshed v0.4)",
         wrecks["records_used"], "uniform over wreckage-after/before interval",
         "open research database; coordinates from location popups (+ v0.1 geocoding where OXREP gives none)",
         f"{wrecks['records_italy']} Italian wrecks exported; replaces the 98-wreck v0.1 extract"),
        ("OXREP Mines", "production", "integrated_secondary (v0.1)", 4,
         "dated overlap", "open research database",
         "2026 export: 8 Italian mines, none with structured dates -> no change"),
        ("Ancient Ports (de Graauw)", "coastal connectivity", "integrated (v0.2)", 531,
         "aoristic from foundation date", "XLS catalogue", ""),
        ("CEIPAC amphora stamps (Rubio et al. 2018)", "material exchange", "integrated (v0.2)", 5785,
         "uniform over conservative type window", "GPL", ""),
        # new in v0.4
        ("Coin Hoards of the Roman Republic (CHRR)", "monetization", "integrated (v0.4)",
         chrr["records_used"], "uniform deposition window [closing-10, closing+15]",
         "CC BY-NC 3.0; Nomisma RDF dump + findspots.geojson",
         f"{chrr['records_total']} hoards; {chrr['records_italy']} in Italy/Sicily/Sardinia; "
         "hoards without closing date excluded; likelihood window from 200 BCE"),
        ("OXREP Olive oil and wine presses", "production", "integrated (v0.4)",
         presses["records_used"], "presence over dated phase, weight = n presses",
         "open research database", f"{presses['records_italy']} Italian sites; undated phases excluded"),
        ("Pleiades places - settlement class", "settlement", "integrated (v0.4)",
         ple["pleiades_settlement"]["records_used"], "presence ramped within first/last attested period (start/end uniform in period)",
         "CC BY; atlantides.org dump", f"{ple['pleiades_settlement']['records_pre400']} places with mass before 400 BCE"),
        ("Pleiades places - rural production class", "production", "integrated (v0.4)",
         ple["pleiades_production"]["records_used"], "presence ramped within first/last attested period (start/end uniform in period)",
         "CC BY; atlantides.org dump", "villa/estate/centuriation/production/quarry/mine/salt-pan/fishpond"),
        ("Pleiades places - transport class", "connectivity", "integrated (v0.4)",
         ple["pleiades_transport"]["records_used"], "presence ramped within first/last attested period (start/end uniform in period)",
         "CC BY; atlantides.org dump", "bridge/road/station/canal/port/harbor/anchorage/lighthouse/shipshed/causeway/milestone"),
        # examined, not usable
        ("OXREP Stone quarries", "production", "excluded: undated", 0, "none",
         "open research database", "792 records; coordinates only, dating is free text (mostly imperial)"),
        ("OXREP Water technology", "infrastructure", "excluded: out of area", 0, "none",
         "open research database", "622 records, all Egyptian papyri"),
        ("AWMC shapefiles (aqueducts, bridges, canals, centuriation, roads, urban areas, coastlines)",
         "infrastructure", "excluded: URLs 404 / undated", 0, "none", "CC BY-NC 3.0",
         "same Barrington features enter via Pleiades with period attestation"),
        ("Orbis Roman transport network", "connectivity", "excluded: undated imperial network", 0,
         "none", "CC BY 3.0", "no segment chronology; same objection as Itiner-e"),
        ("CRRO Roman Republican coin types", "monetization", "excluded: not spatial", 0, "none",
         "ODbL", "type catalogue by mint (Rome-dominated), no findspots"),
        ("OCRE / Coin Hoards of the Roman Empire", "monetization", "excluded: post-30 BCE", 0,
         "none", "ODbL / n.s.", ""),
        ("Trismegistos, FACEM, RTAR, Samian/Terra Sigillata, Gallo-Belgic, Roman Britain kilns, "
         "Karanis, Levantine ceramics, archdata/cawd R packages, Dutch limes, athletic festivals, Strabo",
         "various", "excluded: web-only / out of area / out of period", 0, "none", "various", ""),
        ("Palmisano, Bevan & Shennan settlement archive", "settlement", "not on MERCURY list; still not materialised",
         0, "none", "CC0", "remains the main missing survey-based source"),
        ("HYDE 3.3", "population baseline", "not on MERCURY list; not materialised", 0, "none", "open", ""),
    ]
    led = pd.DataFrame(rows, columns=["dataset", "role", "status", "records_used",
                                      "temporal_treatment", "license_or_access", "notes"])
    led.to_csv(ECON / "data_sources_v04.csv", index=False)


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    chrr = build_chrr()
    presses = build_presses()
    ple = build_pleiades()
    wrecks = build_wrecks()
    write_ledger(chrr, presses, ple, wrecks)
    (RAW / "build_stats_v04.json").write_text(json.dumps(
        {"chrr": chrr, "presses": presses, "pleiades": ple, "wrecks": wrecks}, indent=1))
    print("wrote data_sources_v04.csv")


if __name__ == "__main__":
    main()
