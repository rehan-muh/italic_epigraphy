#!/usr/bin/env python3
"""
Package the latent economic field E(s,t) for the website's /economy/ page and
build the standalone single-file version of the same animation.

Reads data/economy/field_<version>/economic_field_25yr_<version>.csv (the
posterior grid), db/economy_field_<version>_basis.npz (for the Italy-wide
trajectory with its Laplace 95% interval) and the AWMC shoreline layer, and
writes

    assets/data/economy_field_<version>.json
        the payload the site fetches lazily on /economy/ (cells of the Italian
        support, per-layer values x100 as integers, national trajectory,
        shoreline, named places); ~0.7 MB, gzips well
    data/economy/field_<version>/economic_field_<version>_animation.html
        the same page as one self-contained file: the site tokens from
        assets/css/atlas.scss, assets/css/economy.scss, the markup in
        _includes/economy-app.html, assets/js/economy.js and the data, all
        inlined. This is what is published as the shareable artifact; it is
        not part of the site and is git-ignored.

The animation logic lives only in assets/js/economy.js and the styles only in
assets/css/economy.scss, so the site page and the standalone file cannot
drift.

    python scripts/economy_field_animation.py            # v04
    python scripts/economy_field_animation.py v03
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from economy_field_eval import EconomyField  # noqa: E402

VERSION = sys.argv[1] if len(sys.argv) > 1 else "v04"
ECON = ROOT / "data" / "economy"
FIELD = ECON / f"field_{VERSION}"
LAYERS = ROOT / "assets" / "data" / "layers"
JSON_OUT = ROOT / "assets" / "data" / f"economy_field_{VERSION}.json"
HTML_OUT = FIELD / f"economic_field_{VERSION}_animation.html"

LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = 6.5, 19.0, 35.5, 47.5
FIELDS = ["E_mean", "E_sd", "settlement_field", "exchange_field",
          "connectivity_field", "production_field", "monetization_field"]
PLACES = [("Rome", 12.48, 41.89), ("Veii", 12.39, 42.02), ("Tarquinia", 11.76, 42.25),
          ("Capua", 14.25, 41.10), ("Cumae", 14.05, 40.85), ("Taranto", 17.24, 40.47),
          ("Syracuse", 15.29, 37.07), ("Bologna", 11.34, 44.49), ("Adria", 12.06, 45.05),
          ("Cagliari", 9.11, 39.22), ("Genoa", 8.93, 44.41), ("Aquileia", 13.37, 45.77)]


def load_field():
    df = pd.read_csv(FIELD / f"economic_field_25yr_{VERSION}.csv")
    bins = sorted(df.bin_start_year.unique())
    one = df[df.bin_start_year == bins[0]].reset_index(drop=True)
    sel = (one.is_land | (one.in_support & (one.dist_coast_km <= 20.0))).values
    cells = one.loc[sel, ["longitude", "latitude"]].reset_index(drop=True)
    key = pd.MultiIndex.from_frame(cells)
    layers = {}
    for c in FIELDS:
        if c not in df.columns or df[c].isna().all():
            continue
        wide = df.pivot_table(index=["longitude", "latitude"], columns="bin_start_year",
                              values=c).reindex(key)
        layers[c] = np.round(wide.values * 100).astype(int).tolist()   # cells x bins
    return cells, bins, layers, df


def national(cells_land, bins, version):
    f = EconomyField(version=version)
    lon, lat = cells_land.longitude.values, cells_land.latitude.values
    mids = np.asarray(bins, float) + 12.5
    mean, sd = [], []
    for t in mids:
        Phi = f.basis(lon, lat, np.full(len(lon), t))
        v = Phi.mean(axis=0)
        mean.append(float(v @ f.beta - f.offset))
        sd.append(float(math.sqrt(max(((v @ f.L) ** 2).sum(), 0.0))))
    return {"mean": [round(m, 3) for m in mean], "sd": [round(s, 3) for s in sd]}


def shoreline():
    g = json.loads((LAYERS / "awmc-shoreline.geojson").read_text(encoding="utf-8"))
    lines = []
    for feat in g["features"]:
        geom = feat["geometry"]
        parts = geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]
        for part in parts:
            a = np.asarray(part, float)[:, :2]
            inside = ((a[:, 0] >= LON_MIN - 0.3) & (a[:, 0] <= LON_MAX + 0.3)
                      & (a[:, 1] >= LAT_MIN - 0.3) & (a[:, 1] <= LAT_MAX + 0.3))
            run = []                      # split at gaps: clipped pieces must not join
            for ok, (x, y) in zip(inside, a):
                if ok:
                    run.append([round(float(x), 3), round(float(y), 3)])
                elif run:
                    if len(run) > 1:
                        lines.append(run)
                    run = []
            if len(run) > 1:
                lines.append(run)
    return lines


def build_payload():
    cells, bins, layers, df = load_field()
    land = df[(df.bin_start_year == bins[0]) & df.is_land][["longitude", "latitude"]]
    summary = json.loads((ROOT / "db" / f"economy_field_{VERSION}.json").read_text())
    return {
        "version": VERSION,
        "bins": [int(b) for b in bins],
        "lon": cells.longitude.round(3).tolist(),
        "lat": cells.latitude.round(3).tolist(),
        "layers": layers,
        "national": national(land, bins, VERSION),
        "shore": shoreline(),
        "places": [{"name": n, "lon": lo, "lat": la} for n, lo, la in PLACES],
        "loadings": summary["loadings_rho"],
        "length_scales": summary["selected_length_scales"],
    }


# --------------------------------------------------------------- standalone
def strip_front_matter(text):
    return re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S)


def site_tokens():
    """The token blocks (light, OS-dark, toggled-dark) from atlas.scss."""
    scss = (ROOT / "assets" / "css" / "atlas.scss").read_text(encoding="utf-8")
    m = re.search(r"/\* -+\n   Tokens\n   -+ \*/(.*?)/\* -+\n   Base\n", scss, re.S)
    if not m:
        raise RuntimeError("atlas.scss: Tokens/Base section markers not found")
    return m.group(1).strip()


STANDALONE = """<meta charset="utf-8">
<title>Italy Economic Field</title>
<meta name="description" content="Animated map of the latent economic-activity field for Italy, 700-1 BCE">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600&family=Spectral:ital,wght@0,400;0,600;1,400&display=swap">
<style>
/* site tokens (assets/css/atlas.scss) */
__TOKENS__

/* base */
*, *::before, *::after { box-sizing: border-box; }
body { margin: 0; background: var(--bg-page); color: var(--text); font-family: var(--font-text); font-size: var(--t-15); line-height: var(--lh-normal); }
.page-wrap { max-width: 80rem; margin: 0 auto; padding-block: var(--s7) var(--s8); padding-inline: var(--s5); }
h1 { margin: 0 0 var(--s3); font-family: var(--font-text); font-size: var(--t-30); font-weight: 600; line-height: var(--lh-tight); letter-spacing: -.012em; text-wrap: balance; }
.lede { margin: 0 0 var(--s6); max-width: 36rem; font-size: var(--t-19); line-height: var(--lh-prose); color: var(--text-2); }
table { border-collapse: collapse; width: 100%; font-family: var(--font-ui); font-size: var(--t-13); }
th, td { padding: var(--s2) var(--s4) var(--s2) 0; border-bottom: 1px solid var(--border); }
thead th { border-bottom: 1px solid var(--border-strong); font-size: var(--t-12); font-weight: 600; color: var(--text-2); }
a { color: var(--accent); }

/* assets/css/economy.scss */
__ECONOMY_CSS__
</style>

<main class="page-wrap">
  <h1>Economic field</h1>
  <p class="lede">A latent index of economic activity across Italy, 700–1 BCE, inferred from eleven archaeological and historical evidence sources and animated through time.</p>
  <div class="econ-prose">
    <p>Posterior mean of the shared latent field <code>E(s,t)</code> fitted jointly to dated cities, Pleiades settlements and infrastructure, shipwrecks, amphora stamps, ports, Republican coin hoards, presses, mines and radiocarbon dates on a 0.25°&nbsp;×&nbsp;25-year grid (model __VERSION__, length scales __LS__&nbsp;km / __LT__&nbsp;yr). Values are in standard deviations of the field over the whole space–time window: blue is below the 700–1&nbsp;BCE mean, red above.</p>
  </div>
  <div id="economy-app">
__APP__
  </div>
</main>

<script>window.ECONOMY_FIELD = __DATA__;</script>
<script>
__ECONOMY_JS__
</script>
"""


def build_standalone(data):
    css = strip_front_matter((ROOT / "assets" / "css" / "economy.scss").read_text(encoding="utf-8"))
    js = (ROOT / "assets" / "js" / "economy.js").read_text(encoding="utf-8")
    app = (ROOT / "_includes" / "economy-app.html").read_text(encoding="utf-8")
    app = re.sub(r"<!--.*?-->\n?", "", app, count=1, flags=re.S)       # the include's note
    html = (STANDALONE
            .replace("__TOKENS__", site_tokens())
            .replace("__ECONOMY_CSS__", css.strip())
            .replace("__APP__", app.strip())
            .replace("__ECONOMY_JS__", js.strip())
            .replace("__VERSION__", VERSION.replace("v0", "v0."))
            .replace("__LS__", str(data["length_scales"]["spatial_km"]))
            .replace("__LT__", str(data["length_scales"]["temporal_yr"]))
            .replace("__DATA__", json.dumps(data, separators=(",", ":"))))
    HTML_OUT.write_text(html, encoding="utf-8")


def main():
    data = build_payload()
    JSON_OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {JSON_OUT.relative_to(ROOT)} ({JSON_OUT.stat().st_size / 1e6:.2f} MB): "
          f"{len(data['lon'])} cells x {len(data['bins'])} bins, {len(data['layers'])} layers, "
          f"{len(data['shore'])} shoreline runs")
    build_standalone(data)
    print(f"wrote {HTML_OUT.relative_to(ROOT)} ({HTML_OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
