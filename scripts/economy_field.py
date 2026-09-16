#!/usr/bin/env python3
"""
Latent spatiotemporal economic-activity field for Italy, 700 BCE - 1 BCE (v0.4).

Replaces the exploratory v0.2 weighted-sum raster (fixed 0.45/0.35/0.20 weights,
fixed 50-km kernel) with the latent-factor model specified in
data/economy/FRESH_SESSION_HANDOFF.md:

    log lambda_k(s,t) = alpha_k + rho_k * E(s,t) + delta_{d(k)}(s,t)

where E(s,t) is a shared latent economic field (reduced-rank Matern-3/2 HSGP in
projected km x calendar years, Var(E) standardised to 1), rho_k = exp(theta_k)
are positive per-source loadings with the handoff's log-normal prior
(theta ~ N(log 0.5, 0.75^2)), and delta_d are coarser domain-specific residual
GPs (settlement, exchange, connectivity, production). Observation model per
source is a weighted-Poisson grid quadrature of a log-Gaussian Cox process:
each source's aoristic / calibrated 25-year temporal probability allocation is
the chronological-uncertainty integration (handoff section 11), and each source
has its own spatial support (wrecks: sea/coastal cells only; ports: coastal
band; land sources: Italy land mask from the AWMC province rings).

Inference is penalised maximum likelihood with alternating Newton steps
(fields <-> loadings), quasi-Poisson dispersion downweighting so row-rich
sources (amphorae) cannot mechanically dominate, and a Laplace approximation
for the posterior SD of E. Spatial and temporal ranges are selected by
spatial-block cross-validation, not fixed at 50 km. Leave-one-source-out
refits quantify per-source influence.

Deviations from the full handoff spec, chosen to match this repo's existing
hand-rolled HSGP/penalised-likelihood stack (direction_hsgp.py; no
PyMC/Stan/INLA is installed here):
  - empirical-Bayes penalised likelihood + Laplace, not full MCMC;
  - amphora evidence enters as log1p site/type pseudo-counts (as in v0.2)
    with dispersion downweighting, not a NegBin site-random-effect model;
  - Itiner-e roads are EXCLUDED: the local layer has no segment chronology,
    and the handoff forbids back-projecting undated imperial roads;
  - Palmisano and HYDE are still not materialised and are not invented.

v0.4 (2026-09) adds the Project MERCURY sources materialised by
scripts/economy_sources_v04.py (see data/economy/data_sources_v04.csv):
  chrr_hoard            CHRR Republican coin hoards (new "monetization"
                        domain; likelihood window from 200 BCE, before which
                        there is no Roman coinage to hoard)
  oil_wine_press        OXREP olive-oil/wine presses (production)
  pleiades_settlement   Pleiades period-attested settlements (settlement) -
                        the main pre-400 BCE coverage gain
  pleiades_production   Pleiades villas/estates/centuriation/quarries/mines
  pleiades_transport    Pleiades bridges/roads/stations/ports/canals
and refreshes maritime_shipwreck from the 2026 OXREP export (118 dated
Italian wrecks instead of 98). `--v03` reproduces the six-source v0.3 build.

Outputs (VERSION = v04, or v03 with --v03)
  data/economy/field_<VERSION>/economic_field_25yr_<VERSION>.csv   posterior grid
  data/economy/field_<VERSION>/figures/*.png                        diagnostics
  db/economy_field_<VERSION>.json                                   loadings, CV, LOSO
  db/economy_field_<VERSION>_basis.npz                              basis + posterior
                                                                    for exact downstream
                                                                    evaluation of E(s,t)

    python scripts/economy_field.py            # full v0.4 run (resumes from
                                               # data/economy/raw_v04/checkpoints)
    python scripts/economy_field.py --fresh    # ignore checkpoints
    python scripts/economy_field.py --quick    # small test run
    python scripts/economy_field.py --v03      # reproduce v0.3
"""
from __future__ import annotations

import json
import math
import sys
import time
from math import gamma, pi
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ECON = ROOT / "data" / "economy"
LAYERS = ROOT / "assets" / "data" / "layers"
DB = ROOT / "db"

QUICK = "--quick" in sys.argv
V03 = "--v03" in sys.argv                    # reproduce the six-source v0.3 build
VERSION = "v03" if V03 else "v04"
OUTDIR = ECON / f"field_{VERSION}"
FIGDIR = OUTDIR / "figures"
# checkpoints (CV points, full fit, LOSO refits) so an interrupted run — the
# full v0.4 fit takes ~40 min and this machine kills long jobs under memory
# pressure — resumes instead of restarting; `--fresh` discards them
CKPT = ECON / f"raw_{VERSION}" / "checkpoints"
FRESH = "--fresh" in sys.argv
# --budget SECONDS: stop cleanly at the next checkpoint once elapsed; rerun to
# continue (foreground tool calls here are capped at 10 minutes)
BUDGET = float(sys.argv[sys.argv.index("--budget") + 1]) if "--budget" in sys.argv else None
T_START = time.time()


class OutOfBudget(SystemExit):
    pass


def fingerprint(d):
    """Hash of the assembled evidence + source list; checkpoints carrying a
    different fingerprint are ignored (inputs changed since they were made)."""
    import hashlib
    h = hashlib.sha1()
    h.update(",".join(SOURCES).encode())
    for k in SOURCES:
        y = d.src[k]["y"]
        h.update(f"{k}:{len(y)}:{y.sum():.6f}:{(y * np.arange(len(y))).sum():.6f};".encode())
    return h.hexdigest()[:16]


def check_budget(where):
    if BUDGET is not None and time.time() - T_START > BUDGET:
        print(f"budget reached after {where}; checkpoints saved, rerun to continue", flush=True)
        raise OutOfBudget(0)
SEED = 1
LAT0 = math.radians(42.5)
BOUNDARY = 1.25
ETA_CLIP = (-30.0, 8.0)

BIN_STARTS = np.arange(-700, 0, 25)          # 28 bins, [-700,-675) ... [-25,0)
BIN_MIDS = BIN_STARTS + 12.5
LONS = np.arange(6.5, 19.0 + 1e-9, 0.25)     # 51, matches v0.2 grid
LATS = np.arange(35.5, 47.5 + 1e-9, 0.25)    # 49

DOMAIN_OF = {
    "urban_city": "settlement",
    "archaeological_occupation": "settlement",
    "maritime_shipwreck": "exchange",
    "amphora_trade": "exchange",
    "ancient_port": "connectivity",
    "mining_production": "production",
}
if not V03:
    DOMAIN_OF.update({
        "pleiades_settlement": "settlement",
        "pleiades_production": "production",
        "pleiades_transport": "connectivity",
        "oil_wine_press": "production",
        "chrr_hoard": "monetization",
    })
SOURCES = list(DOMAIN_OF)
DOMAINS = ["settlement", "exchange", "connectivity", "production"] \
    + ([] if V03 else ["monetization"])
# sources whose evidence class cannot exist before a given year: the
# likelihood is restricted to bins from that year on, so structural zeros
# (no amphora typology, no Roman coinage) are never read as low activity
SOURCE_WINDOW_FROM = {"amphora_trade": -400, "chrr_hoard": -200}

DELTA_SD = 0.5            # prior sd of domain-residual fields (E has sd 1)
CAP_EXTRAPOLATION = not V03   # CV: cap held-out eta at each source's training max
THETA_MU, THETA_SD = math.log(0.5), 0.75   # prior on log loading (handoff s.9)


def project(lon, lat):
    """Equirectangular km, same convention as direction_model.py."""
    return (111.32 * math.cos(LAT0) * np.asarray(lon, float),
            110.57 * np.asarray(lat, float))


# --------------------------------------------------------------- geometry
def load_rings(layer="awmc-provinces-60bc.geojson"):
    g = json.loads((LAYERS / layer).read_text(encoding="utf-8"))
    rings = []
    for f in g["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            rings.append(np.asarray(poly[0], float)[:, :2])   # outer ring only
    return rings


def inside_ring(pts, R):
    """Vectorised even-odd point-in-polygon."""
    x, y = pts[:, 0], pts[:, 1]
    x0, y0 = R[:-1, 0], R[:-1, 1]
    x1, y1 = R[1:, 0], R[1:, 1]
    inside = np.zeros(len(pts), bool)
    for a0, b0, a1, b1 in zip(x0, y0, x1, y1):
        cond = (b0 > y) != (b1 > y)
        if not cond.any():
            continue
        xi = a0 + (y - b0) / (b1 - b0 + 1e-30) * (a1 - a0)
        inside ^= cond & (x < xi)
    return inside


def shoreline_points():
    g = json.loads((LAYERS / "awmc-shoreline.geojson").read_text(encoding="utf-8"))
    pts = []

    def walk(c):
        if isinstance(c, list):
            if c and isinstance(c[0], (int, float)):
                pts.append(c[:2])
            else:
                for k in c:
                    walk(k)

    for f in g["features"]:
        walk(f["geometry"]["coordinates"])
    return np.asarray(pts, float)


def min_dist_km(lon, lat, ref_lonlat):
    from scipy.spatial import cKDTree
    rx, ry = project(ref_lonlat[:, 0], ref_lonlat[:, 1])
    tree = cKDTree(np.c_[rx, ry])
    px, py = project(lon, lat)
    d, _ = tree.query(np.c_[px, py])
    return d


# --------------------------------------------------------------- HSGP basis
def spectral_density(omega, ls, d, nu=1.5):
    c = (2 ** d * pi ** (d / 2) * gamma(nu + d / 2) * (2 * nu) ** nu
         / (gamma(nu) * ls ** (2 * nu)))
    return c * (2 * nu / ls ** 2 + omega ** 2) ** (-(nu + d / 2))


def dim_basis(x, half, centre, m):
    L = BOUNDARY * half
    j = np.arange(1, m + 1)
    freq = pi * j / (2 * L)
    phi = np.sqrt(1.0 / L) * np.sin(freq[None, :] * (x[:, None] - centre + L))
    return phi, freq


class Basis:
    """Separable space x time Matern-3/2 HSGP basis with spectral scaling."""

    def __init__(self, ls, lt, cx, cy, ct, hx, hy, ht, cap_s=12, cap_t=8):
        self.ls, self.lt = ls, lt
        self.cx, self.cy, self.ct = cx, cy, ct
        self.hx, self.hy, self.ht = hx, hy, ht
        self.mx = max(3, min(cap_s, int(np.ceil(1.75 * BOUNDARY * hx / ls)) + 1))
        self.my = max(3, min(cap_s, int(np.ceil(1.75 * BOUNDARY * hy / ls)) + 1))
        self.mt = max(3, min(cap_t, int(np.ceil(1.75 * BOUNDARY * ht / lt)) + 1))
        _, fx = dim_basis(np.zeros(1), hx, cx, self.mx)
        _, fy = dim_basis(np.zeros(1), hy, cy, self.my)
        _, ft = dim_basis(np.zeros(1), ht, ct, self.mt)
        omega_s = np.sqrt(fx[:, None] ** 2 + fy[None, :] ** 2).ravel()
        self.w_s = np.sqrt(spectral_density(omega_s, ls, 2))
        self.w_t = np.sqrt(spectral_density(ft, lt, 1))
        self.m = self.mx * self.my * self.mt

    def eval(self, px, py, t):
        phix, _ = dim_basis(px, self.hx, self.cx, self.mx)
        phiy, _ = dim_basis(py, self.hy, self.cy, self.my)
        psit, _ = dim_basis(t, self.ht, self.ct, self.mt)
        phi_s = np.einsum("ni,nj->nij", phix, phiy).reshape(len(px), -1)
        phi_s = phi_s * self.w_s[None, :]
        psit = psit * self.w_t[None, :]
        return np.einsum("ns,nt->nst", phi_s, psit).reshape(len(px), -1)

    def spec(self):
        return {k: getattr(self, k) for k in
                ("ls", "lt", "cx", "cy", "ct", "hx", "hy", "ht", "mx", "my", "mt")}


# --------------------------------------------------------------- data assembly
def snap(lon, lat):
    """Map coordinates to fine-grid cell index, or -1 if outside the box."""
    ci = np.round((np.asarray(lon, float) - LONS[0]) / 0.25).astype(int)
    ri = np.round((np.asarray(lat, float) - LATS[0]) / 0.25).astype(int)
    ok = (ci >= 0) & (ci < len(LONS)) & (ri >= 0) & (ri < len(LATS))
    return np.where(ok, ri * len(LONS) + ci, -1)


def bin_index(bs):
    return ((np.asarray(bs, int) + 700) // 25)


def assemble():
    """Per-source (cell, bin, weighted count) tables on the fine grid."""
    sp = pd.read_csv(ECON / "source_points_all.csv")
    out = {}

    up = pd.read_csv(ECON / "urban_city_time_weights_25yr.csv")
    up["cell"] = snap(up.longitude, up.latitude)
    up["bin"] = bin_index(up.bin_start_year)
    out["urban_city"] = up[up.cell >= 0].groupby(["cell", "bin"]).active_probability.sum()

    pt = pd.read_csv(ECON / "ports_time_weights_25yr.csv")
    pt["cell"] = snap(pt.longitude, pt.latitude)
    pt["bin"] = bin_index(pt.bin_start_year)
    out["ancient_port"] = pt[pt.cell >= 0].groupby(["cell", "bin"]).active_probability.sum()

    nb = pd.read_csv(ECON / "base_nonurban_source_time_weights_25yr.csv")
    xy = sp.set_index(["source_type", sp.source_id.astype(str)])[["longitude", "latitude"]]
    nb["sid"] = nb.source_id.astype(str)
    nb = nb.join(xy, on=["source_type", "sid"])
    nb["cell"] = snap(nb.longitude, nb.latitude)
    nb["bin"] = bin_index(nb.bin_start_year)
    nb = nb[(nb.cell >= 0) & (nb.bin >= 0) & (nb.bin < len(BIN_STARTS))]
    # multiple 14C dates from one site are not independent events: cap each
    # site x bin occupation weight at 1 (presence probability)
    nb.loc[nb.source_type == "archaeological_occupation", "weight"] = \
        nb.loc[nb.source_type == "archaeological_occupation", "weight"].clip(upper=1.0)
    for st in ["archaeological_occupation", "maritime_shipwreck", "mining_production"]:
        out[st] = nb[nb.source_type == st].groupby(["cell", "bin"]).weight.sum()

    am = pd.read_csv(ECON / "amphora_time_weights_25yr.csv")
    am["cell"] = snap(am.longitude, am.latitude)
    am["bin"] = bin_index(am.bin_start_year)
    am = am[(am.cell >= 0) & (am.bin >= 0)]
    out["amphora_trade"] = am.groupby(["cell", "bin"]).weighted_evidence.sum()

    if not V03:
        def cellbin(df):
            df = df.copy()
            df["cell"] = snap(df.longitude, df.latitude)
            df["bin"] = bin_index(df.bin_start_year)
            return df[(df.cell >= 0) & (df.bin >= 0) & (df.bin < len(BIN_STARTS))]
        # 2026 OXREP export replaces the v0.1 wreck extract
        wk = cellbin(pd.read_csv(ECON / "shipwreck_time_weights_25yr_v04.csv"))
        out["maritime_shipwreck"] = wk.groupby(["cell", "bin"]).weight.sum()
        hd = cellbin(pd.read_csv(ECON / "chrr_hoard_time_weights_25yr.csv"))
        out["chrr_hoard"] = hd.groupby(["cell", "bin"]).weight.sum()
        pr = cellbin(pd.read_csv(ECON / "presses_time_weights_25yr.csv"))
        out["oil_wine_press"] = pr.groupby(["cell", "bin"]).weight.sum()
        pl = cellbin(pd.read_csv(ECON / "pleiades_time_weights_25yr.csv"))
        for st in ["pleiades_settlement", "pleiades_production", "pleiades_transport"]:
            out[st] = pl[pl.source_type == st].groupby(["cell", "bin"]).weight.sum()

    return {k: v.rename("y").reset_index() for k, v in out.items()}


def build_masks(counts):
    """Fine-grid spatial support per source + Italy land mask.

    v0.3: land = AWMC 60 BCE province rings containing Hanson cities; any
    observed event outside a source's nominal support extended the support
    cell by cell. That let stray points (Carthage, Istria, the Alpine arc,
    which the 60 BCE rings omit) enter as isolated cells with no zero-count
    surroundings.
    v0.4: land = modern Italy (ISTAT provinces, which include the Alpine
    districts, Friuli and every island); the Italian shoreline is the AWMC
    shoreline within 8 km of it; land sources are supported on land plus a
    20-km coastal band (a 0.25-degree cell containing a coastal place can
    centre up to ~17 km offshore); events outside a source's support are
    DROPPED (reported), so the field is defined for Italy and foreign places
    (Carthage, Istria, the Riviera, Corsica) never enter."""
    glon = np.tile(LONS, len(LATS))
    glat = np.repeat(LATS, len(LONS))
    pts = np.c_[glon, glat]
    land = np.zeros(len(glon), bool)
    shore = shoreline_points()
    if V03:
        rings = load_rings()
        cities = pd.read_csv(ECON / "urban_city_time_weights_25yr.csv") \
            .drop_duplicates("source_id")[["longitude", "latitude"]].values
        for R in rings:
            if inside_ring(cities, R).any():          # Italian rings only
                land |= inside_ring(pts, R)
        # distance to the ITALIAN shoreline only: shoreline vertices further
        # than 30 km from any Italian land cell (Tunisia, Dalmatia, Gaul) must
        # not create coastal/wreck support where the Italy-focused catalogues
        # simply never looked — zero counts there would masquerade as low
        # activity
        keep = min_dist_km(shore[:, 0], shore[:, 1],
                           np.c_[glon[land], glat[land]]) <= 30.0
    else:
        rings = load_rings("istat-province.geojson")
        # the local ISTAT extract has 106 provinces and omits the Valle
        # d'Aosta; add an approximate outline of the region (ridge-line
        # vertices) so the Alpine north-west is not a hole in modern Italy
        rings.append(np.array([[6.80, 45.83], [7.05, 45.93], [7.55, 45.98],
                               [7.87, 45.92], [7.95, 45.62], [7.60, 45.47],
                               [7.10, 45.45], [6.85, 45.65], [6.80, 45.83]]))
        verts = np.concatenate(rings)
        for R in rings:
            land |= inside_ring(pts, R)
        keep = min_dist_km(shore[:, 0], shore[:, 1], verts) <= 8.0
        for R in rings:
            keep |= inside_ring(shore, R)
    d_coast = min_dist_km(glon, glat, shore[keep])
    masks = {
        "urban_city": land.copy(),
        "archaeological_occupation": land.copy(),
        "mining_production": land.copy(),
        "amphora_trade": land.copy(),
        "ancient_port": d_coast <= 30.0,
        "maritime_shipwreck": (~land & (d_coast <= 80.0)) | (d_coast <= 15.0),
        # v0.4 sources: land evidence; transport features also sit on the
        # coastal band (ports, harbours, lighthouses)
        "chrr_hoard": land.copy(),
        "oil_wine_press": land.copy(),
        "pleiades_settlement": land.copy(),
        "pleiades_production": land.copy(),
        "pleiades_transport": land | (d_coast <= 30.0),
    }
    masks = {k: masks[k] for k in SOURCES}
    if V03:
        # quadrature support must cover every observed event
        for k, df in counts.items():
            masks[k][df.cell.values] = True
    else:
        for k in SOURCES:
            if k != "maritime_shipwreck":
                masks[k] = land | (d_coast <= 20.0)
        for k, df in counts.items():
            inside = masks[k][df.cell.values]
            if (~inside).any():
                print(f"  {k}: dropping {df.y[~inside].sum():.1f} of {df.y.sum():.1f} "
                      f"evidence mass in {df.cell[~inside].nunique()} cells outside the "
                      f"Italian support", flush=True)
            counts[k] = df[inside].reset_index(drop=True)
    return masks, land, d_coast, glon, glat


# --------------------------------------------------------------- model fit
class Data:
    """Everything the fitter needs, on the (coarse or fine) quadrature grid."""


def make_fit_data(counts, masks, coarse=True):
    """Aggregate to 0.5-degree quadrature cells; build shared point list."""
    n_lon = len(LONS)
    if coarse:
        cell_of = (np.repeat(LATS_I // 2, n_lon) * ((n_lon + 1) // 2)
                   + np.tile(LONS_I // 2, len(LATS)))
    else:
        cell_of = np.arange(n_lon * len(LATS))
    n_cc = cell_of.max() + 1
    # coarse-cell centres
    glon = np.tile(LONS, len(LATS))
    glat = np.repeat(LATS, n_lon)
    cx = np.bincount(cell_of, glon, n_cc) / np.bincount(cell_of, None, n_cc)
    cy = np.bincount(cell_of, glat, n_cc) / np.bincount(cell_of, None, n_cc)

    d = Data()
    d.n_cc, d.cell_of, d.cx, d.cy = n_cc, cell_of, cx, cy
    d.src = {}
    used = np.zeros(n_cc * len(BIN_STARTS), bool)
    for k in SOURCES:
        m = np.zeros(n_cc, bool)
        m[cell_of[masks[k]]] = True
        cells = np.where(m)[0]
        if k in SOURCE_WINDOW_FROM:                  # evidence-class window only
            bins = np.where(BIN_STARTS >= SOURCE_WINDOW_FROM[k])[0]
        else:
            bins = np.arange(len(BIN_STARTS))
        cc = np.repeat(cells, len(bins))
        bb = np.tile(bins, len(cells))
        pt_id = cc * len(BIN_STARTS) + bb
        y = np.zeros(len(pt_id))
        df = counts[k]
        agg = df.groupby([cell_of[df.cell.values], df.bin.values]).y.sum()
        key = pd.Series(np.arange(len(pt_id)), index=pd.MultiIndex.from_arrays([cc, bb]))
        hit = key.reindex(agg.index)
        ok = hit.notna().values
        y[hit[ok].astype(int).values] = agg.values[ok]
        d.src[k] = {"pt": pt_id, "y": y, "w": 1.0}
        used[pt_id] = True
    d.pt_ids = np.where(used)[0]
    d.remap = np.full(n_cc * len(BIN_STARTS), -1)
    d.remap[d.pt_ids] = np.arange(len(d.pt_ids))
    for k in SOURCES:
        d.src[k]["idx"] = d.remap[d.src[k]["pt"]]
    pc = d.pt_ids // len(BIN_STARTS)
    pb = d.pt_ids % len(BIN_STARTS)
    px, py = project(cx[pc], cy[pc])
    d.px, d.py, d.t = px, py, BIN_MIDS[pb]
    d.pt_cell, d.pt_bin = pc, pb
    return d


def make_bases(ls, lt, px, py, t):
    cx, cy = (px.max() + px.min()) / 2, (py.max() + py.min()) / 2
    hx, hy = (px.max() - px.min()) / 2, (py.max() - py.min()) / 2
    ct, ht = (t.max() + t.min()) / 2, max((t.max() - t.min()) / 2, 1.0)
    bE = Basis(ls, lt, cx, cy, ct, hx, hy, ht)
    bD = Basis(2 * ls, 2 * lt, cx, cy, ct, hx, hy, ht, cap_s=6, cap_t=4)
    return bE, bD


def fit(d, bE, bD, use_delta=True, outer=8, inner=3, drop=None, verbose=False):
    """Alternating penalised Newton. Returns dict of fitted pieces."""
    srcs = [k for k in SOURCES if k != drop]
    PhiE = bE.eval(d.px, d.py, d.t)
    PhiD = bD.eval(d.px, d.py, d.t) if use_delta else None
    mE = PhiE.shape[1]
    mD = PhiD.shape[1] if use_delta else 0
    doms = [dm for dm in DOMAINS if any(DOMAIN_OF[k] == dm for k in srcs)] if use_delta else []
    p = mE + mD * len(doms) + len(srcs)
    off_d = {dm: mE + i * mD for i, dm in enumerate(doms)}
    off_a = {k: mE + mD * len(doms) + i for i, k in enumerate(srcs)}

    beta = np.zeros(p)
    theta = {k: math.log(0.5) for k in srcs}
    for k in srcs:
        s = d.src[k]
        beta[off_a[k]] = math.log(max(s["y"].sum(), 0.1) / len(s["y"]))

    prior = np.ones(p)                      # N(0,1) on field coefficients
    if use_delta:
        for dm in doms:
            prior[off_d[dm]:off_d[dm] + mD] = 1.0 / DELTA_SD ** 2
    prior[mE + mD * len(doms):] = 1e-4      # near-flat on intercepts

    def etas(k):
        s = d.src[k]
        e = (math.exp(theta[k]) * (PhiE[s["idx"]] @ beta[:mE])
             + beta[off_a[k]])
        if use_delta:
            dm = off_d[DOMAIN_OF[k]]
            e = e + PhiD[s["idx"]] @ beta[dm:dm + mD]
        return np.clip(e, *ETA_CLIP)

    def penlik():
        ll = 0.0
        for k in srcs:
            s = d.src[k]
            e = etas(k)
            ll += s["w"] * np.sum(s["y"] * e - np.exp(e))
        ll -= 0.5 * np.sum(prior * beta ** 2)
        for k in srcs:
            ll -= 0.5 * ((theta[k] - THETA_MU) / THETA_SD) ** 2
        return ll

    last = -np.inf
    for it in range(outer):
        # ---- (a) joint Newton on all field coefficients + intercepts
        for _ in range(inner):
            H = np.diag(prior.copy())
            g = -prior * beta
            for k in srcs:
                s = d.src[k]
                rho = math.exp(theta[k])
                e = etas(k)
                mu = np.exp(e)
                r = s["w"] * (s["y"] - mu)
                Wk = s["w"] * mu
                Xe = PhiE[s["idx"]]
                cols = [(0, mE, rho, Xe)]
                if use_delta:
                    dm = off_d[DOMAIN_OF[k]]
                    cols.append((dm, mD, 1.0, PhiD[s["idx"]]))
                for (o1, m1, c1, X1) in cols:
                    g[o1:o1 + m1] += c1 * (X1.T @ r)
                    for (o2, m2, c2, X2) in cols:
                        if o2 < o1:
                            continue
                        blk = c1 * c2 * (X1.T @ (Wk[:, None] * X2))
                        H[o1:o1 + m1, o2:o2 + m2] += blk
                        if o2 > o1:
                            H[o2:o2 + m2, o1:o1 + m1] += blk.T
                    H[o1:o1 + m1, off_a[k]] += c1 * (X1.T @ Wk)
                    H[off_a[k], o1:o1 + m1] += c1 * (X1.T @ Wk)
                g[off_a[k]] += r.sum()
                H[off_a[k], off_a[k]] += Wk.sum()
            try:
                step = np.linalg.solve(H, g)
            except np.linalg.LinAlgError:
                step = np.linalg.lstsq(H, g, rcond=None)[0]
            big = np.max(np.abs(step))
            if big > 3.0:
                step *= 3.0 / big
            beta += step
        # ---- (b) per-source (alpha, theta) Newton
        Efield = PhiE @ beta[:mE]
        for k in srcs:
            s = d.src[k]
            Ei = Efield[s["idx"]]
            base = np.zeros(len(Ei))
            if use_delta:
                dm = off_d[DOMAIN_OF[k]]
                base = PhiD[s["idx"]] @ beta[dm:dm + mD]
            for _ in range(4):
                rho = math.exp(theta[k])
                e = np.clip(beta[off_a[k]] + rho * Ei + base, *ETA_CLIP)
                mu = np.exp(e)
                r = s["w"] * (s["y"] - mu)
                W = s["w"] * mu
                ga = r.sum()
                gt = np.sum(r * rho * Ei) - (theta[k] - THETA_MU) / THETA_SD ** 2
                Haa = W.sum()
                Hat = np.sum(W * rho * Ei)
                Htt = np.sum(W * (rho * Ei) ** 2) + 1.0 / THETA_SD ** 2 \
                    - np.sum(r * rho * Ei)
                Htt = max(Htt, 1e-6)
                det = Haa * Htt - Hat ** 2
                if abs(det) < 1e-12:
                    break
                da = (ga * Htt - gt * Hat) / det
                dt = (gt * Haa - ga * Hat) / det
                beta[off_a[k]] += np.clip(da, -2, 2)
                theta[k] += float(np.clip(dt, -1, 1))
        # ---- (c) standardise E to sd 1 over quadrature points; the scale
        # moves into the loadings exactly (eta unchanged). The mean cannot be
        # absorbed exactly (the sine basis has no constant column), so it is
        # only subtracted at export time as E_mean_offset.
        Efield = PhiE @ beta[:mE]
        sE_ = Efield.std()
        if sE_ > 1e-8:
            beta[:mE] /= sE_
            for k in srcs:
                theta[k] += math.log(sE_)
        pl = penlik()
        if verbose:
            print(f"  outer {it}: penlik {pl:.2f}  "
                  + " ".join(f"{k.split('_')[0]}={math.exp(v):.2f}" for k, v in theta.items()),
                  flush=True)
        if abs(pl - last) < 0.05:
            break
        last = pl

    # E is standardised to sd 1 but not mean 0; record the mean for export
    Efield = PhiE @ beta[:mE]
    res = {"beta": beta, "theta": theta, "mE": mE, "mD": mD, "doms": doms,
           "off_d": off_d, "off_a": off_a, "srcs": srcs, "prior": prior,
           "E_mean_offset": float(Efield.mean()), "penlik": float(last),
           "use_delta": use_delta}
    return res


def dispersion(d, bE, bD, res):
    """Quasi-Poisson dispersion per source at the current fit."""
    PhiE = bE.eval(d.px, d.py, d.t)
    PhiD = bD.eval(d.px, d.py, d.t) if res["use_delta"] else None
    out = {}
    beta, theta = res["beta"], res["theta"]
    mE, mD = res["mE"], res["mD"]
    for k in res["srcs"]:
        s = d.src[k]
        e = math.exp(theta[k]) * (PhiE[s["idx"]] @ beta[:mE]) + beta[res["off_a"][k]]
        if res["use_delta"]:
            dm = res["off_d"][DOMAIN_OF[k]]
            e = e + PhiD[s["idx"]] @ beta[dm:dm + mD]
        mu = np.exp(np.clip(e, *ETA_CLIP))
        keep = mu > 1e-8
        out[k] = float(np.sum((s["y"][keep] - mu[keep]) ** 2 / mu[keep])
                       / max(keep.sum() - 1, 1))
    return out


def laplace_cov_E(d, bE, bD, res):
    """Block of H^-1 for the shared-field coefficients (marginal over rest)."""
    PhiE = bE.eval(d.px, d.py, d.t)
    PhiD = bD.eval(d.px, d.py, d.t) if res["use_delta"] else None
    beta, theta = res["beta"], res["theta"]
    mE, mD = res["mE"], res["mD"]
    p = len(beta)
    H = np.diag(res["prior"].copy())
    for k in res["srcs"]:
        s = d.src[k]
        rho = math.exp(theta[k])
        e = rho * (PhiE[s["idx"]] @ beta[:mE]) + beta[res["off_a"][k]]
        if res["use_delta"]:
            dm = res["off_d"][DOMAIN_OF[k]]
            e = e + PhiD[s["idx"]] @ beta[dm:dm + mD]
        W = s["w"] * np.exp(np.clip(e, *ETA_CLIP))
        Xe = PhiE[s["idx"]]
        cols = [(0, mE, rho, Xe)]
        if res["use_delta"]:
            dm = res["off_d"][DOMAIN_OF[k]]
            cols.append((dm, mD, 1.0, PhiD[s["idx"]]))
        for (o1, m1, c1, X1) in cols:
            for (o2, m2, c2, X2) in cols:
                if o2 < o1:
                    continue
                blk = c1 * c2 * (X1.T @ (W[:, None] * X2))
                H[o1:o1 + m1, o2:o2 + m2] += blk
                if o2 > o1:
                    H[o2:o2 + m2, o1:o1 + m1] += blk.T
            H[o1:o1 + m1, res["off_a"][k]] += c1 * (X1.T @ W)
            H[res["off_a"][k], o1:o1 + m1] += c1 * (X1.T @ W)
        H[res["off_a"][k], res["off_a"][k]] += W.sum()
    Hinv = np.linalg.inv(H + 1e-8 * np.eye(p))
    return Hinv[:mE, :mE]


# --------------------------------------------------------------- CV selection
def spatial_blocks(cx, cy):
    """Five deterministic geographic blocks (islands, S, C, N-C, Po)."""
    blk = np.full(len(cx), 3)
    blk[(cy < 41.3)] = 1                                   # southern mainland
    blk[(cy >= 41.3) & (cy < 43.3)] = 2                    # central
    blk[(cy >= 43.3) & (cy < 45.0)] = 3                    # north-central
    blk[(cy >= 45.0)] = 4                                  # Po / Alpine
    blk[(cx < 10.2) & (cy < 44.0)] = 0                     # Sardinia/Corsica
    blk[(cy < 38.6) & (cx > 11.0)] = 0                     # Sicily -> islands
    return blk


def cv_select(d, grid, verbose=True):
    """Spatial-block CV of the length scales. Held-out Poisson deviance per
    source, with each source's held-out linear predictor capped at the
    maximum fitted on its training points (CAP_EXTRAPOLATION): v0.4 adds
    small, spatially concentrated sources with steep loadings (presses,
    Pleiades rural sites), and without the cap a single over-extrapolated
    fold (Sardinia/Sicily predicted from the mainland) drives their deviance
    to exp(8) per cell and swamps the criterion. The cap keeps the deviance
    a proper score of over-prediction up to the intensity range actually
    observed; it is applied identically to every candidate."""
    blocks = spatial_blocks(d.cx, d.cy)
    results = []
    ck = CKPT / "cv.json"
    fp = fingerprint(d)
    done = {}
    if not (QUICK or FRESH) and ck.exists():
        saved = json.loads(ck.read_text())
        if saved.get("fingerprint") == fp:
            done = {(r["ls_km"], r["lt_yr"]): r for r in saved["results"]}
        else:
            print("CV checkpoint ignored: evidence tables changed", flush=True)
    for (ls, lt) in grid:
        if (ls, lt) in done:
            results.append(done[(ls, lt)])
            if verbose:
                print(f"CV ls={ls:>4} km lt={lt:>4} yr -> deviance "
                      f"{done[(ls, lt)]['heldout_deviance']:.1f}  (checkpoint)", flush=True)
            continue
        bE, bD = make_bases(ls, lt, d.px, d.py, d.t)
        dev = 0.0
        fold_dev = []
        for b in range(5):
            hold_cells = np.where(blocks == b)[0]
            hold = np.isin(d.pt_cell, hold_cells)
            dtr = Data()
            dtr.px, dtr.py, dtr.t = d.px, d.py, d.t
            dtr.src = {}
            for k in SOURCES:
                s = d.src[k]
                keep = ~hold[s["idx"]]
                dtr.src[k] = {"idx": s["idx"][keep], "y": s["y"][keep], "w": 1.0}
            res = fit(dtr, bE, bD, use_delta=False, outer=3, inner=3)
            PhiE = bE.eval(d.px, d.py, d.t)
            Ef = PhiE @ res["beta"][:res["mE"]]
            fd = 0.0
            for k in SOURCES:
                s = d.src[k]
                ho = hold[s["idx"]]
                if not ho.any():
                    continue
                eta_all = np.clip(math.exp(res["theta"][k]) * Ef[s["idx"]]
                                  + res["beta"][res["off_a"][k]], *ETA_CLIP)
                e = eta_all[ho]
                if CAP_EXTRAPOLATION and (~ho).any():
                    e = np.minimum(e, eta_all[~ho].max())
                mu = np.exp(e)
                y = s["y"][ho]
                with np.errstate(divide="ignore", invalid="ignore"):
                    term = np.where(y > 0, y * (np.log(y) - e), 0.0)
                fd += 2.0 * np.sum(term - (y - mu))
            fold_dev.append(float(fd))
            dev += fd
        results.append({"ls_km": ls, "lt_yr": lt, "heldout_deviance": float(dev),
                        "fold_deviance": [round(v, 1) for v in fold_dev]})
        if not QUICK:
            CKPT.mkdir(parents=True, exist_ok=True)
            ck.write_text(json.dumps({"fingerprint": fp, "results": results}, indent=1))
        if verbose:
            print(f"CV ls={ls:>4} km lt={lt:>4} yr -> deviance {dev:.1f}  folds "
                  + " ".join(f"{v:.0f}" for v in fold_dev), flush=True)
        check_budget(f"CV ({ls},{lt})")
    best = min(results, key=lambda r: r["heldout_deviance"])
    return best, results


# --------------------------------------------------------------- prediction
def predict_fine(bE, bD, res, glon, glat):
    """Posterior fields on the fine 0.25-degree x 25-yr grid."""
    beta, theta = res["beta"], res["theta"]
    mE, mD = res["mE"], res["mD"]
    px, py = project(glon, glat)
    CE = res["covE"]
    rows = []
    rho_bar = {dm: np.mean([math.exp(theta[k]) for k in res["srcs"]
                            if DOMAIN_OF[k] == dm]) for dm in res["doms"]}
    for bi, bs in enumerate(BIN_STARTS):
        t = np.full(len(glon), BIN_MIDS[bi])
        Phi = bE.eval(px, py, t)
        E = Phi @ beta[:mE] - res["E_mean_offset"]
        sd = np.sqrt(np.maximum(((Phi @ CE) * Phi).sum(axis=1), 0.0))
        rec = {"bin_start_year": np.full(len(glon), bs),
               "bin_end_year": np.full(len(glon), bs + 25),
               "longitude": glon, "latitude": glat,
               "E_mean": E, "E_sd": sd}
        if res["use_delta"]:
            PhiDf = bD.eval(px, py, t)
            for dm in DOMAINS:
                if dm in res["doms"]:
                    off = res["off_d"][dm]
                    delta = PhiDf @ beta[off:off + mD]
                    rec[f"{dm}_field"] = rho_bar[dm] * E + delta
                else:
                    rec[f"{dm}_field"] = np.full(len(glon), np.nan)
        rows.append(pd.DataFrame(rec))
    return pd.concat(rows, ignore_index=True)


# --------------------------------------------------------------- main
def main():
    t0 = time.time()
    np.random.seed(SEED)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    FIGDIR.mkdir(parents=True, exist_ok=True)

    print("assembling sources ...", flush=True)
    counts = assemble()
    for k, df in counts.items():
        print(f"  {k}: {len(df)} cell-bin rows, total weight {df.y.sum():.1f}")
    masks, land, d_coast, glon, glat = build_masks(counts)
    print(f"  land cells {land.sum()} / {len(land)}")

    d = make_fit_data(counts, masks, coarse=True)
    print(f"quadrature: {d.n_cc} coarse cells, {len(d.pt_ids)} active points, "
          + ", ".join(f"{k}:{len(d.src[k]['y'])}" for k in SOURCES), flush=True)

    grid = ([(140, 150)] if QUICK else
            [(90, 80), (90, 150), (140, 80), (140, 150), (200, 80), (200, 150)]
            + ([] if V03 else [(90, 50), (140, 50), (200, 50)]))
    print("selecting length scales by spatial-block CV ...", flush=True)
    best, cv_table = cv_select(d, grid)
    ls, lt = best["ls_km"], best["lt_yr"]
    print(f"selected ls={ls} km, lt={lt} yr", flush=True)

    bE, bD = make_bases(ls, lt, d.px, d.py, d.t)
    print(f"basis: E {bE.mx}x{bE.my}x{bE.mt}={bE.m}, delta {bD.mx}x{bD.my}x{bD.mt}={bD.m}",
          flush=True)
    import pickle
    fp = fingerprint(d)
    ck_fit = CKPT / f"fit_{ls}_{lt}_{fp}.pkl"
    if not QUICK and not FRESH and ck_fit.exists():
        print("full fit: loading checkpoint", flush=True)
        res, phi, phi2, wts = pickle.loads(ck_fit.read_bytes())
        for k in SOURCES:
            d.src[k]["w"] = wts[k]
    else:
        print("fitting full model ...", flush=True)
        res = fit(d, bE, bD, use_delta=True, outer=4 if QUICK else 16, verbose=True)

        print("dispersion pass ...", flush=True)
        phi = dispersion(d, bE, bD, res)
        print("  " + " ".join(f"{k}={v:.2f}" for k, v in phi.items()))
        for k in SOURCES:
            d.src[k]["w"] = 1.0 / max(1.0, phi[k])
        res = fit(d, bE, bD, use_delta=True, outer=4 if QUICK else 12, verbose=True)
        phi2 = dispersion(d, bE, bD, res)

        print("Laplace covariance ...", flush=True)
        res["covE"] = laplace_cov_E(d, bE, bD, res)
        if not QUICK:
            CKPT.mkdir(parents=True, exist_ok=True)
            ck_fit.write_bytes(pickle.dumps(
                (res, phi, phi2, {k: d.src[k]["w"] for k in SOURCES})))
            check_budget("full fit")

    # ---- leave-one-source-out
    loso = {}
    if not QUICK:
        ck_loso = CKPT / f"loso_{ls}_{lt}_{fp}.json"
        if not FRESH and ck_loso.exists():
            loso = json.loads(ck_loso.read_text())
        pxq, pyq, tq = d.px, d.py, d.t
        PhiEq = bE.eval(pxq, pyq, tq)
        E_all = PhiEq @ res["beta"][:res["mE"]] - res["E_mean_offset"]
        for k in SOURCES:
            if k in loso:
                continue
            print(f"LOSO refit without {k} ...", flush=True)
            r2 = fit(d, bE, bD, use_delta=True, outer=8, drop=k)
            E_k = PhiEq @ r2["beta"][:r2["mE"]] - r2["E_mean_offset"]
            dd = E_all - E_k
            loso[k] = {"mean_abs_delta": float(np.mean(np.abs(dd))),
                       "max_abs_delta": float(np.max(np.abs(dd))),
                       "corr_with_full": float(np.corrcoef(E_all, E_k)[0, 1])}
            ck_loso.write_text(json.dumps(loso, indent=1))
            check_budget(f"LOSO {k}")

    # ---- fine-grid posterior export
    print("predicting on fine grid ...", flush=True)
    out = predict_fine(bE, bD, res, glon, glat)
    n_cells = len(glon)
    out["is_land"] = np.tile(land, len(BIN_STARTS))
    out["dist_coast_km"] = np.tile(np.round(d_coast, 1), len(BIN_STARTS))
    in_support = np.zeros(n_cells, bool)
    for k in SOURCES:
        in_support |= masks[k]
    out["in_support"] = np.tile(in_support, len(BIN_STARTS))
    for c in ["E_mean", "E_sd"] + [f"{dm}_field" for dm in DOMAINS]:
        out[c] = out[c].round(4)
    out.to_csv(OUTDIR / f"economic_field_25yr_{VERSION}.csv", index=False)
    print(f"wrote {OUTDIR / f'economic_field_25yr_{VERSION}.csv'} ({len(out)} rows)")

    # ---- basis + posterior artifact for exact downstream evaluation
    covE = res["covE"]
    try:
        LE = np.linalg.cholesky(covE + 1e-10 * np.eye(len(covE)))
    except np.linalg.LinAlgError:
        w, V = np.linalg.eigh(covE)
        LE = V * np.sqrt(np.clip(w, 0, None))
    np.savez_compressed(
        DB / f"economy_field_{VERSION}_basis.npz",
        beta_E=res["beta"][:res["mE"]], cov_chol=LE,
        E_mean_offset=res["E_mean_offset"], lat0_deg=42.5,
        **{f"spec_{k}": v for k, v in bE.spec().items()},
        boundary=BOUNDARY, nu=1.5)

    # ---- summary JSON
    theta = res["theta"]
    rhos = {k: math.exp(theta[k]) for k in SOURCES}
    wk = {k: rhos[k] ** 2 / sum(r ** 2 for r in rhos.values()) for k in SOURCES}
    dom_load = {dm: float(np.mean([rhos[k] for k in SOURCES if DOMAIN_OF[k] == dm]))
                for dm in DOMAINS}
    reliability = {k: rhos[k] ** 2 / (rhos[k] ** 2 + DELTA_SD ** 2
                                      + max(0.0, phi2[k] - 1.0)) for k in SOURCES}
    summary = {
        "model": "latent-factor spatiotemporal HSGP LGCP, penalised ML + Laplace",
        "version": "v0.3" if V03 else "v0.4", "seed": SEED, "quick": QUICK,
        "selected_length_scales": {"spatial_km": ls, "temporal_yr": lt},
        "cv_table": cv_table,
        "loadings_rho": {k: round(rhos[k], 4) for k in SOURCES},
        "normalized_weights_rho2": {k: round(wk[k], 4) for k in SOURCES},
        "domain_mean_loading": {k: round(v, 4) for k, v in dom_load.items()},
        "alphas": {k: round(float(res["beta"][res["off_a"][k]]), 4) for k in SOURCES},
        "dispersion_initial": {k: round(v, 3) for k, v in phi.items()},
        "dispersion_after_reweight": {k: round(v, 3) for k, v in phi2.items()},
        "likelihood_weights_1_over_phi": {k: round(d.src[k]["w"], 4) for k in SOURCES},
        "reliability_R": {k: round(v, 4) for k, v in reliability.items()},
        "leave_one_source_out": loso,
        "domain_of_source": DOMAIN_OF,
        "source_windows_from": SOURCE_WINDOW_FROM,
        "excluded_sources": ({
            "itinere_roads": "no segment chronology in local layer; handoff forbids undated back-projection",
            "palmisano": "not materialised", "chrr_hoards": "not materialised",
            "hyde33": "not materialised"} if V03 else {
            "itinere_roads / orbis": "no segment chronology; handoff forbids undated back-projection",
            "oxrep_quarries": "free-text dating only", "oxrep_water_technology": "Egyptian papyri only",
            "awmc_shapefiles": "listed URLs 404; features covered by Pleiades with period attestation",
            "palmisano": "not materialised", "hyde33": "not materialised",
            "see": "data/economy/data_sources_v04.csv"}),
        "runtime_s": round(time.time() - t0, 1),
    }
    (DB / f"economy_field_{VERSION}.json").write_text(json.dumps(summary, indent=1),
                                                     encoding="utf-8")
    print(json.dumps({k: summary[k] for k in
                      ["selected_length_scales", "loadings_rho",
                       "normalized_weights_rho2", "reliability_R"]}, indent=1))

    # ---- figures
    print("figures ...", flush=True)
    make_figures(out, land, in_support, summary, res, bE)
    print(f"done in {time.time() - t0:.0f}s")


def savefig(fig, path, dpi=140):
    """savefig with retry: Windows indexers/thumbnailers briefly lock freshly
    written PNGs and raise EINVAL on the next overwrite."""
    import os
    tmp = path.with_suffix(".tmp.png")
    for attempt in range(5):
        try:
            fig.savefig(tmp, dpi=dpi)
            os.replace(tmp, path)
            return
        except OSError:
            time.sleep(1.0 + attempt)
    fig.savefig(path, dpi=dpi)


def make_figures(out, land, in_support, summary, res, bE):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    shore = shoreline_points()
    box = (shore[:, 0] >= LONS[0]) & (shore[:, 0] <= LONS[-1]) \
        & (shore[:, 1] >= LATS[0]) & (shore[:, 1] <= LATS[-1])
    shore = shore[box]
    show = land | in_support

    def field_map(col, bs, fname, title, vmin, vmax, cmap="viridis"):
        sl = out[out.bin_start_year == bs]
        Z = sl[col].values.reshape(len(LATS), len(LONS)).copy()
        Z[~show.reshape(len(LATS), len(LONS))] = np.nan
        fig, ax = plt.subplots(figsize=(6, 6.6))
        pm = ax.pcolormesh(LONS, LATS, Z, vmin=vmin, vmax=vmax, cmap=cmap,
                           shading="nearest")
        ax.scatter(shore[:, 0], shore[:, 1], s=0.05, c="k", alpha=0.4)
        ax.set_title(title)
        ax.set_aspect(1 / math.cos(LAT0))
        fig.colorbar(pm, ax=ax, shrink=0.7)
        fig.tight_layout()
        savefig(fig, FIGDIR / fname, dpi=140)
        plt.close(fig)

    lo, hi = np.nanpercentile(out.E_mean[np.tile(show, len(BIN_STARTS))], [1, 99])
    for bs in [-700, -600, -500, -400, -300, -200, -100, -25]:
        yr = f"{-bs}BCE"
        field_map("E_mean", bs, f"latent_E_mean_{yr}.png",
                  f"Latent economic field E, {-bs} BCE (posterior mean)", lo, hi)
    for bs in [-500, -100]:
        yr = f"{-bs}BCE"
        field_map("E_sd", bs, f"latent_E_sd_{yr}.png",
                  f"Posterior SD of E, {-bs} BCE", 0,
                  float(np.nanpercentile(out.E_sd, 99)), cmap="magma")
        for dm in DOMAINS:
            col = f"{dm}_field"
            if out[col].notna().any():
                v = out[col][np.tile(show, len(BIN_STARTS))]
                field_map(col, bs, f"{dm}_field_{yr}.png",
                          f"{dm.capitalize()} domain field, {-bs} BCE",
                          float(np.nanpercentile(v, 1)), float(np.nanpercentile(v, 99)),
                          cmap="cividis")

    # national trajectory with CI over land cells
    landrep = np.tile(land, len(BIN_STARTS))
    tr = out[landrep].groupby("bin_start_year").agg(E=("E_mean", "mean"))
    px, py = project(out.longitude.values[:len(land)][land],
                     out.latitude.values[:len(land)][land])
    sds = []
    for bi in range(len(BIN_STARTS)):
        Phi = bE.eval(px, py, np.full(land.sum(), BIN_MIDS[bi]))
        v = Phi.mean(axis=0)
        sds.append(math.sqrt(max(float(v @ res["covE"] @ v), 0.0)))
    sds = np.asarray(sds)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(BIN_MIDS, tr.E.values, c="#1f5fa8")
    ax.fill_between(BIN_MIDS, tr.E.values - 1.96 * sds, tr.E.values + 1.96 * sds,
                    alpha=0.25, color="#1f5fa8")
    ax.set_xlabel("year (negative = BCE)")
    ax.set_ylabel("mean latent E over land")
    ax.set_title("National latent economic trajectory, 95% interval")
    fig.tight_layout()
    savefig(fig, FIGDIR / "national_trajectory.png", dpi=140)
    plt.close(fig)

    # loadings + reliability
    fig, axes = plt.subplots(1, 2, figsize=(11, 0.4 * len(SOURCES) + 2))
    ks = SOURCES
    axes[0].barh(ks, [summary["loadings_rho"][k] for k in ks], color="#1f5fa8")
    axes[0].set_title("Estimated loading rho_k on shared field")
    axes[1].barh(ks, [summary["reliability_R"][k] for k in ks], color="#a83232")
    axes[1].set_title("Approx. reliability R_k")
    for a in axes:
        a.tick_params(labelsize=8)
    fig.tight_layout()
    savefig(fig, FIGDIR / "loadings_reliability.png", dpi=140)
    plt.close(fig)

    if summary["leave_one_source_out"]:
        loso = summary["leave_one_source_out"]
        fig, ax = plt.subplots(figsize=(7, 0.4 * len(loso) + 2))
        ax.barh(list(loso), [loso[k]["mean_abs_delta"] for k in loso], color="#3a7d44")
        ax.set_title("Leave-one-source-out: mean |change in E| (sd units)")
        ax.tick_params(labelsize=8)
        fig.tight_layout()
        savefig(fig, FIGDIR / "loso_influence.png", dpi=140)
        plt.close(fig)


LONS_I = np.arange(len(LONS))
LATS_I = np.arange(len(LATS))

if __name__ == "__main__":
    main()
