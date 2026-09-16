#!/usr/bin/env python3
"""
Hierarchical aoristic model of writing direction in the epigraphic atlas.

Outcome is strictly binary on the direction codes confirmed by the compiler:
0 = sinistroverse (right-to-left), 1 = dextroverse (left-to-right). Codes 2, 3
and "both" are not reliably coded and are excluded (83 records), and reported
as such.

The model estimates one quantity:

    p(tradition, quarter, place, geography) = P(dextroverse)

Chronological uncertainty is handled as a latent-quarter mixture rather than by
binning on start dates or by discarding interval-dated material. Each record is
dated only to an interval spanning n quarters; the true quarter is a latent
variable, uniform over that interval a priori. The observed-data log-likelihood
is therefore

    sum_i log( (1/n_i) * sum_{t in span_i} Bernoulli(y_i | p_it) )

which is fitted by EM: the E-step is the posterior over quarters for each
record, the M-step a penalised weighted logistic regression on the (record,
quarter) expansion. This is the "aoristic EM" referred to in the write-up.

Linear predictor:

    logit p_it = mu + l_lang(i) + a_alph(i) + d_[alph(i), t]
                 + u_place(i) + x_i' gamma + f(s_i, t)

    l, a  language and alphabet levels, ridge
    d_at  each alphabet's own time curve, RW2 + ridge to zero
    u_p   findspot random effect, ridge (= a normal random effect)
    gamma five distances: coast, roads, the 60 BCE frontier, and the nearest
          Greek and the nearest Latin epigraphy
    f     space-time field: a low-rank Gaussian process on knots crossed with
          time knots, ridge on the coefficients standing in for the GP prior

Time enters exactly once per hypothesis: d_at is a script changing its own
practice, f is a region changing across scripts, and separating the two is the
point of the exercise. There is no global time curve — it lies in the span of
a common shift of the d_at and removing it is a held-out tie; the date-only
hypothesis is still tested, as its own rung of the ladder.

Every model is fitted on the same records: the 12,137 with coordinates, since
geography and the field need them. Smoothing parameters, and the kernel's shape
and length scale, are chosen by findspot-blocked cross-validation, so no
variance-component theory is relied on. Uncertainty on every reported quantity
comes from a findspot-clustered nonparametric bootstrap of the whole EM fit.

Usage:
    py -3.12 scripts/direction_model.py            # full run
    py -3.12 scripts/direction_model.py --quick    # small grid, few bootstraps
"""
from __future__ import annotations

import argparse, json, math, sqlite3, sys, time
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "atlas.sqlite"
OUT = ROOT / "db" / "direction_model.json"
LAYERS = ROOT / "assets" / "data" / "layers"

MIN_LANG_N = 100          # languages below this are pooled into "other language"
MIN_ALPH_N = 40           # alphabets below this are pooled into "other alphabet"
LANGS: list = []          # language levels, set once in main(); read by Design
REPORT_ORDS = range(4, 32)  # 700 BCE .. 25 BCE, the pre-0 window
LAT0 = math.radians(42.5)


# --------------------------------------------------------------------------- data

def project(lon, lat):
    """Equirectangular km, good enough over Italy for distance covariates."""
    return np.c_[111.32 * math.cos(LAT0) * np.asarray(lon, float),
                 110.57 * np.asarray(lat, float)]


def layer_points(name):
    import json as _json
    g = _json.loads((LAYERS / name).read_text(encoding="utf-8"))
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
    a = np.asarray(pts, float)
    return project(a[:, 0], a[:, 1])


def min_distance(A, B):
    """Nearest-neighbour distance, km. KD-tree — the layers run to ~10^5 vertices."""
    from scipy.spatial import cKDTree
    return cKDTree(B).query(A, k=1)[0]


def polygon_rings(name):
    import json as _json
    g = _json.loads((LAYERS / name).read_text(encoding="utf-8"))
    rings = []
    for f in g["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            for ring in poly:
                rings.append(np.asarray(ring, float)[:, :2])
    return rings


def inside_rings(lonlat, rings):
    res = np.zeros(len(lonlat), bool)
    x, y = lonlat[:, 0], lonlat[:, 1]
    for R in rings:
        x1, y1, x2, y2 = R[:-1, 0], R[:-1, 1], R[1:, 0], R[1:, 1]
        crosses = (y1[None] > y[:, None]) != (y2[None] > y[:, None])
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (x2 - x1)[None] * (y[:, None] - y1[None]) / (y2 - y1)[None] + x1[None]
        res ^= (crosses & (x[:, None] < xint)).sum(1) % 2 == 1
    return res


def load():
    con = sqlite3.connect(DB)
    df = pd.read_sql("""
        select i.pk, l.name lang, a.label alph, d.code dir,
               p.id place_id, p.name place, r.name region,
               i.latitude lat, i.longitude lon,
               i.q_ord_start qs, i.q_ord_end qe
        from inscriptions i
        join directions d on d.id = i.direction_id
        left join languages l on l.id = i.language_id
        left join alphabets a on a.id = i.alphabet_id
        left join places    p on p.id = i.place_id
        left join regions   r on r.id = p.region_id
    """, con)
    quarters = pd.read_sql("select ord, edge from quarters order by ord", con).set_index("ord").edge
    con.close()

    n_coded = len(df)
    n_messy = int((~df.dir.isin(["0", "1"])).sum())
    df = df[df.dir.isin(["0", "1"])].dropna(subset=["qs", "qe", "place_id"]).copy()
    df["y"] = (df.dir == "1").astype(np.float64)
    df["qs"] = df.qs.astype(int)
    df["qe"] = df.qe.astype(int)
    df["nq"] = df.qe - df.qs + 1

    # Two candidate units for a "writing community": the language, and the
    # alphabet it is written in. The ladder below tests which one the
    # directional norm actually belongs to, so both are carried.
    df["lang"] = df.lang.fillna("(language unrecorded)")
    df["alph"] = df.alph.fillna("(alphabet unrecorded)")
    lc = df.lang.value_counts()
    df["langgrp"] = np.where(df.lang.isin(set(lc[lc >= MIN_LANG_N].index)),
                             df.lang, "other language")
    ac = df.alph.value_counts()
    df["trad"] = np.where(df.alph.isin(set(ac[ac >= MIN_ALPH_N].index)),
                          df.alph, "other alphabet")

    # geography, on the georeferenced part
    has_xy = df.lat.notna() & df.lon.notna()
    P = project(df.lon.where(has_xy, 0).values, df.lat.where(has_xy, 0).values)
    geo = {}
    geo["d_coast"] = min_distance(P, layer_points("awmc-shoreline.geojson"))
    geo["d_road"] = min_distance(P, layer_points("itinere-roads.geojson"))
    # Exposure to Greek and to Latin writing: how far the findspot is from the
    # nearest findspot carrying epigraphy in that language.
    #
    # Whether the findspot itself counts depends on the record. For a Latin
    # inscription, its own findspot trivially has Latin epigraphy, so including
    # it makes the distance zero by construction and the covariate just
    # re-encodes the language; those records get the distance to the nearest
    # OTHER Latin findspot. For an Etruscan inscription, Latin writing at the
    # same site is exactly the exposure being measured and must be counted.
    contact = df[has_xy]
    place_xy = df[has_xy].groupby("place_id")[["lat", "lon"]].first()
    S = project(place_xy.lon.values, place_xy.lat.values)
    for lg, key in [("Greek", "d_greek"), ("Latin", "d_latin")]:
        tgt = contact[contact.lang == lg].groupby("place_id")[["lat", "lon"]].first()
        T = project(tgt.lon.values, tgt.lat.values)
        D = np.sqrt(((S[:, None, :] - T[None, :, :]) ** 2).sum(-1))
        incl = pd.Series(D.min(1), index=place_xy.index)
        D[place_xy.index.values[:, None] == tgt.index.values[None, :]] = np.inf
        excl = pd.Series(D.min(1), index=place_xy.index)
        same_lang = (df.lang == lg).values
        v = np.where(same_lang, df.place_id.map(excl).values, df.place_id.map(incl).values)
        geo[key] = np.where(has_xy, v, 0.0)
    rings = polygon_rings("awmc-provinces-60bc.geojson")
    lonlat = df[["lon", "lat"]].where(has_xy, 0).values
    inside = inside_rings(lonlat, rings)
    boundary = np.vstack([project(R[:, 0], R[:, 1]) for R in rings])
    dfr = min_distance(P, boundary)
    geo["d_frontier"] = np.where(inside, -dfr, dfr)          # negative = inside
    df["px"], df["py"] = P[:, 0], P[:, 1]                    # projected km, for the kernel

    for k, v in geo.items():
        if k == "d_frontier":
            df[k] = np.sign(v) * np.log1p(np.abs(v))
        else:
            df[k] = np.log1p(v)
    df["has_xy"] = has_xy.values
    df.loc[~df.has_xy, list(geo)] = np.nan

    # Every model is fitted on the same records. Geography and the space-time
    # field need coordinates, and a ladder whose rungs sit on different subsets
    # is not a ladder, so the 509 records without coordinates are dropped once,
    # here, rather than silently under some of the rungs.
    n_no_xy = int((~df.has_xy).sum())
    df = df[df.has_xy].reset_index(drop=True)
    return df, quarters, n_coded, n_messy, n_no_xy


# ------------------------------------------------------------------- design matrix

GEO_COLS = ["d_coast", "d_road", "d_greek", "d_latin", "d_frontier"]

# ---- space-time kernel ----------------------------------------------------
# A low-rank Gaussian process: a separable squared-exponential kernel in space
# and time, evaluated at knots on a grid and carried as basis functions, with a
# ridge on the coefficients standing in for the GP prior. Findspot effects test
# whether a *place* has its own norm; this tests whether a *region* does, and
# whether that region moves through time, which is what a diffusion account of
# writing direction predicts and what discrete effects cannot express.
# Length scales are not guesses: KERNEL_GRID below is swept by the same
# findspot-blocked CV as everything else, and the winner is what the reported
# model uses. A squared exponential assumes a field that is infinitely smooth,
# which is a strong claim about how a practice spreads, so Matern 3/2 — rough
# at short range, the usual default in spatial statistics — is swept against it.
ST = {"ls": 110.0, "lt": 8.0, "kernel": "se"}
KNOTS = {"xy": None, "t": None}
# The ridge has to move with the length scale: a 45 km grid has six times the
# knots of a 110 km one, so the same penalty is a different prior.
KERNEL_GRID = [(ls, k, lk) for ls in (45.0, 70.0, 110.0)
               for k in ("se", "m32") for lk in (1.0, 4.0, 16.0)]
SQRT3 = 1.7320508075688772


def st_knots(df, n_ord, spacing=None, t_every=8):
    """Knots on a grid at the length scale, kept where the corpus has records."""
    spacing = spacing or ST["ls"]
    pts = np.unique(df[["px", "py"]].values, axis=0)
    lo, hi = pts.min(0), pts.max(0)
    gx = np.arange(lo[0], hi[0] + spacing, spacing)
    gy = np.arange(lo[1], hi[1] + spacing, spacing)
    G = np.array([(x, y) for x in gx for y in gy])
    near = np.sqrt(((G[:, None, :] - pts[None, :, :]) ** 2).sum(-1)).min(1)
    return G[near <= spacing], np.arange(0, n_ord, t_every, dtype=float)


def st_weights(v, knots, ls, keep=4, kernel=None):
    """Row-normalised kernel weights on the `keep` nearest knots."""
    kernel = kernel or ST["kernel"]
    d2 = (v[:, None] - knots[None, :]) ** 2 if knots.ndim == 1 else \
         ((v[:, None, :] - knots[None, :, :]) ** 2).sum(-1)
    idx = np.argsort(d2, axis=1)[:, :keep]
    d2 = np.take_along_axis(d2, idx, 1)
    if kernel == "m32":
        r = SQRT3 * np.sqrt(d2) / ls
        w = (1.0 + r) * np.exp(-r)
    else:
        w = np.exp(-d2 / (2 * ls * ls))
    return idx, w / np.maximum(w.sum(1, keepdims=True), 1e-12)


class Design:
    """Sparse (record, quarter) expansion plus the block structure of the penalty."""

    def __init__(self, df, n_ord, trads, places, spec):
        self.spec = spec
        self.trads = list(trads)
        self.places = list(places)
        self.n_ord = n_ord
        t_ix = {t: i for i, t in enumerate(self.trads)}
        p_ix = {p: i for i, p in enumerate(self.places)}

        rec, qq = [], []
        for i, (a, b) in enumerate(zip(df.qs.values, df.qe.values)):
            k = b - a + 1
            rec.append(np.full(k, i))
            qq.append(np.arange(a, b + 1))
        self.rec = np.concatenate(rec)
        self.q = np.concatenate(qq)
        self.nq = df.nq.values.astype(float)
        self.y = df.y.values[self.rec]
        self.base = 1.0 / self.nq[self.rec]
        n = len(self.rec)

        blocks, self.slices, col = [], {}, 0

        def add(name, mat, width):
            nonlocal col
            blocks.append(mat)
            self.slices[name] = slice(col, col + width)
            col += width

        add("mu", sp.csr_matrix(np.ones((n, 1))), 1)

        if spec["time"]:
            add("g", sp.csr_matrix((np.ones(n), (np.arange(n), self.q)),
                                   shape=(n, n_ord)), n_ord)
        if spec.get("lang"):
            l_ix = {l: i for i, l in enumerate(LANGS)}
            li = df.langgrp.map(l_ix).values[self.rec]
            add("l", sp.csr_matrix((np.ones(n), (np.arange(n), li)),
                                   shape=(n, len(LANGS))), len(LANGS))
        if spec["trad"]:
            ti = df.trad.map(t_ix).values[self.rec]
            add("a", sp.csr_matrix((np.ones(n), (np.arange(n), ti)),
                                   shape=(n, len(self.trads))), len(self.trads))
        if spec["trad_time"]:
            ti = df.trad.map(t_ix).values[self.rec]
            add("d", sp.csr_matrix((np.ones(n), (np.arange(n), ti * n_ord + self.q)),
                                   shape=(n, len(self.trads) * n_ord)),
                len(self.trads) * n_ord)
        if spec["place"]:
            pi = df.place_id.map(p_ix).values[self.rec]
            add("u", sp.csr_matrix((np.ones(n), (np.arange(n), pi)),
                                   shape=(n, len(self.places))), len(self.places))
        if spec["geo"]:
            G = df[GEO_COLS].values[self.rec]
            add("x", sp.csr_matrix(G), len(GEO_COLS))

        if spec.get("st"):
            KX, KT = KNOTS["xy"], KNOTS["t"]
            ns, nt = len(KX), len(KT)
            si, sw = st_weights(df[["px", "py"]].values, KX, ST["ls"])
            ti, tw = st_weights(np.arange(n_ord, dtype=float), KT, ST["lt"])
            si, sw = si[self.rec], sw[self.rec]            # per expanded row
            tj, tv = ti[self.q], tw[self.q]
            has = 1.0
            rows, cols, vals = [], [], []
            for a in range(si.shape[1]):
                for b in range(tj.shape[1]):
                    rows.append(np.arange(n))
                    cols.append(si[:, a] * nt + tj[:, b])
                    vals.append(sw[:, a] * tv[:, b] * has)
            add("k", sp.csr_matrix((np.concatenate(vals),
                                    (np.concatenate(rows), np.concatenate(cols))),
                                   shape=(n, ns * nt)), ns * nt)

        self.X = sp.hstack(blocks, format="csr")
        self.p = self.X.shape[1]

    # -- penalty -------------------------------------------------------------
    def penalty(self, lam):
        n_ord = self.n_ord
        D = np.zeros((n_ord - 2, n_ord))
        for i in range(n_ord - 2):
            D[i, i:i + 3] = [1, -2, 1]
        R = D.T @ D                                    # RW2 roughness
        P = np.zeros((self.p, self.p))

        if "g" in self.slices:
            s = self.slices["g"]
            P[s, s] = lam["g"] * R + 1e-4 * np.eye(n_ord)
        if "l" in self.slices:
            s = self.slices["l"]
            P[s, s] = lam["a"] * np.eye(s.stop - s.start)
        if "a" in self.slices:
            s = self.slices["a"]
            P[s, s] = lam["a"] * np.eye(s.stop - s.start)
        if "d" in self.slices:
            s = self.slices["d"]
            blk = lam["d"] * R + lam["d0"] * np.eye(n_ord)
            for k in range(len(self.trads)):
                a = s.start + k * n_ord
                P[a:a + n_ord, a:a + n_ord] = blk
        if "u" in self.slices:
            s = self.slices["u"]
            P[s, s] = lam["u"] * np.eye(s.stop - s.start)
        if "x" in self.slices:
            # Effectively unpenalised on the full corpus. On a subset the
            # distances are collinear enough (coast against frontier, r = -0.9)
            # that individual coefficients diverge under resampling, so callers
            # can ask for a real ridge with lam["x"].
            s = self.slices["x"]
            P[s, s] = lam.get("x", 1e-4) * np.eye(s.stop - s.start)
        if "k" in self.slices:
            s = self.slices["k"]
            P[s, s] = lam["k"] * np.eye(s.stop - s.start)
        P[0, 0] = 1e-6
        return P


# ------------------------------------------------------------------------ fitting

def irls(X, y, w, P, beta, iters=4, tol=1e-8):
    """Penalised weighted logistic regression (Fisher scoring)."""
    Xc = X.tocsc()
    for _ in range(iters):
        eta = np.clip(X @ beta, -30, 30)
        mu = 1.0 / (1.0 + np.exp(-eta))
        s = np.clip(mu * (1 - mu), 1e-9, None) * w
        XtWX = (Xc.T @ sp.diags(s) @ Xc).toarray() + P
        grad = X.T @ (w * (y - mu)) - P @ beta
        try:
            step = np.linalg.solve(XtWX, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(XtWX, grad, rcond=None)[0]
        # Undamped Newton on a near-separated design can take a single step of
        # arbitrary size and never come back; on bootstrap resamples of a 2.7%
        # positive class that happened in 96% of draws. Near the optimum the
        # step is far smaller than this, so the cap does not bind on a healthy
        # fit — it only stops a runaway.
        big = np.max(np.abs(step))
        if big > 2.0:
            step *= 2.0 / big
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break
    return beta, XtWX


def em_fit(des, lam, beta=None, max_em=60, tol=1e-7, verbose=False, inner=4):
    """EM over the latent quarter. Returns beta, observed-data log-likelihood.

    `inner` is the number of Fisher-scoring steps per M-step; the M-step need
    not be run to convergence (generalised EM), and 3-4 steps reach the same
    log-likelihood as 30 in half the time.
    """
    P = des.penalty(lam)
    if beta is None:
        beta = np.zeros(des.p)
    n_rec = int(des.rec.max()) + 1
    prev = -np.inf
    w = des.base.copy()
    for it in range(max_em):
        beta, XtWX = irls(des.X, des.y, w, P, beta, iters=inner)
        eta = np.clip(des.X @ beta, -30, 30)
        mu = 1.0 / (1.0 + np.exp(-eta))
        lik = np.where(des.y > 0.5, mu, 1 - mu)
        contrib = des.base * lik
        tot = np.bincount(des.rec, weights=contrib, minlength=n_rec)
        ll = float(np.log(np.maximum(tot, 1e-300)).sum())
        w = contrib / np.maximum(tot[des.rec], 1e-300)     # E-step posterior
        if verbose:
            print(f"    EM {it:3d}  loglik {ll:.4f}", flush=True)
        if ll - prev < tol * max(1.0, abs(ll)):
            prev = ll
            break
        prev = ll
    return beta, prev, XtWX, w


def loglik_on(des, beta):
    n_rec = int(des.rec.max()) + 1
    eta = np.clip(des.X @ beta, -30, 30)
    mu = 1.0 / (1.0 + np.exp(-eta))
    lik = np.where(des.y > 0.5, mu, 1 - mu)
    tot = np.bincount(des.rec, weights=des.base * lik, minlength=n_rec)
    return np.log(np.maximum(tot, 1e-300))


# ------------------------------------------------------------------- model ladder

# No rung except M1 carries the global time curve. A shared trend lies in the
# span of a common shift of the per-alphabet curves, so inside the larger
# models the split between the two was decided by the penalties, not the data
# — and removing it is a held-out tie (direction_structure.py: -0.0001 bits,
# 95% CI [-0.0036, +0.0032]). M1 keeps it as the standalone date-only
# hypothesis; everywhere else time enters exactly once per hypothesis: a curve
# per alphabet (a script changing its own practice) and the kernel's time axis
# (a region changing).
SPECS = {
    "M0 intercept":          dict(time=0, lang=0, trad=0, trad_time=0, place=0, geo=0),
    "M1 + century":          dict(time=1, lang=0, trad=0, trad_time=0, place=0, geo=0),
    "M1b space-time kernel": dict(time=0, lang=0, trad=0, trad_time=0, place=0, geo=0, st=1),
    "M2 + language":         dict(time=0, lang=1, trad=0, trad_time=0, place=0, geo=0),
    # The claim under test is alphabet against language, so the two have to be
    # comparable at the same rung: M2a swaps language out for alphabet. Reading
    # it off M3 instead measures only what alphabet adds after language has
    # already absorbed the shared signal, which the two are highly correlated on.
    "M2a alphabet, no language": dict(time=0, lang=0, trad=1, trad_time=0, place=0, geo=0),
    "M3 + alphabet":         dict(time=0, lang=1, trad=1, trad_time=0, place=0, geo=0),
    "M4 + alphabet x time":  dict(time=0, lang=1, trad=1, trad_time=1, place=0, geo=0),
    "M5 + findspot":         dict(time=0, lang=1, trad=1, trad_time=1, place=1, geo=0),
    "M6 + geography":        dict(time=0, lang=1, trad=1, trad_time=1, place=1, geo=1),
    "M7 + space-time kernel": dict(time=0, lang=1, trad=1, trad_time=1, place=1, geo=1, st=1),
}
FINAL = "M5 + findspot"
GEO_SUBSET = ()


def blocked_folds(df, k, seed=0):
    rng = np.random.default_rng(seed)
    pl = df.place_id.unique()
    rng.shuffle(pl)
    assign = {p: i % k for i, p in enumerate(pl)}
    return df.place_id.map(assign).values


def cv_predict(df, n_ord, trads, places, spec, lam, k=10, seed=0, geo_only=False):
    """Out-of-fold P(dextroverse) per record, folds blocked on findspot.

    The record-level prediction marginalises the latent quarter under its
    uniform prior, so it is the same quantity the observed-data likelihood
    scores, and every threshold metric below is computed from it.
    """
    fold = blocked_folds(df, k, seed)
    keep = np.ones(len(df), bool)
    p = np.full(len(df), np.nan)
    for f in range(k):
        tr_m, te_m = (fold != f) & keep, (fold == f) & keep
        if not tr_m.any() or not te_m.any():
            continue
        d_tr = Design(df[tr_m], n_ord, trads, places, spec)
        beta, _, _, _ = em_fit(d_tr, lam)
        d_te = Design(df[te_m], n_ord, trads, places, spec)
        mu = 1.0 / (1.0 + np.exp(-np.clip(d_te.X @ beta, -30, 30)))
        p[np.flatnonzero(te_m)] = np.bincount(d_te.rec, weights=d_te.base * mu,
                                              minlength=int(te_m.sum()))
    return p


def roc_auc(y, p):
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), float)
    ranks[order] = np.arange(1, len(p) + 1)
    ps = p[order]                                   # average ranks over ties
    i = 0
    while i < len(ps):
        j = i
        while j + 1 < len(ps) and ps[j + 1] == ps[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2
        i = j + 1
    n1 = y.sum()
    n0 = len(y) - n1
    return float((ranks[y > 0.5].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def avg_precision(y, p):
    order = np.argsort(-p, kind="mergesort")
    yy = y[order]
    tp = np.cumsum(yy)
    prec = tp / np.arange(1, len(yy) + 1)
    return float(prec[yy > 0.5].sum() / max(yy.sum(), 1))


def metrics(y, p, thresh=0.5):
    """Threshold and threshold-free scores, for a 1-in-6 positive class."""
    ok = np.isfinite(p)
    y, p = y[ok], np.clip(p[ok], 1e-9, 1 - 1e-9)
    hat = p >= thresh
    tp = float((hat & (y > 0.5)).sum())
    fp = float((hat & (y < 0.5)).sum())
    fn = float((~hat & (y > 0.5)).sum())
    tn = float((~hat & (y < 0.5)).sum())
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    spec = tn / max(tn + fp, 1)
    return {
        "n": int(len(y)),
        "prevalence": round(float(y.mean()), 4),
        "log_loss_bits": round(float(-(y * np.log2(p) + (1 - y) * np.log2(1 - p)).mean()), 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(2 * prec * rec / max(prec + rec, 1e-9), 4),
        "balanced_accuracy": round((rec + spec) / 2, 4),
        "roc_auc": round(roc_auc(y, p), 4),
        "pr_auc": round(avg_precision(y, p), 4),
        "brier": round(float(((p - y) ** 2).mean()), 4),
    }


def cv_logloss(df, n_ord, trads, places, spec, lam, k=10, seed=0, geo_only=False):
    """Mean negative log-likelihood per record, in nats (for the tuner)."""
    p = cv_predict(df, n_ord, trads, places, spec, lam, k, seed, geo_only)
    ok = np.isfinite(p)
    y, q = df.y.values[ok], np.clip(p[ok], 1e-12, 1 - 1e-12)
    return float(-(y * np.log(q) + (1 - y) * np.log(1 - q)).mean())


# ------------------------------------------------------------------------ reports

def h_bits(p):
    p = np.clip(np.asarray(p, float), 1e-12, 1 - 1e-12)
    return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))


def population_p(des, df, beta, weights):
    """Population-averaged P(dextroverse) per tradition x quarter.

    g-computation over the findspots and geography actually attested in each
    cell, with aoristic weights, so the result is comparable with a raw
    proportion rather than being a prediction for a hypothetical site.
    """
    eta = np.clip(des.X @ beta, -30, 30)
    mu = 1.0 / (1.0 + np.exp(-eta))
    trad = df.trad.values[des.rec]
    out = {}
    # "" is the corpus as a whole: the same g-computation with no grouping, so
    # the pooled curve and the per-alphabet curves are the same quantity and
    # the gap between them is the composition of the corpus, nothing else.
    for t in list(des.trads) + [""]:
        m = np.ones(len(trad), bool) if t == "" else (trad == t)
        num = np.bincount(des.q[m], weights=(weights[m] * mu[m]), minlength=des.n_ord)
        den = np.bincount(des.q[m], weights=weights[m], minlength=des.n_ord)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[t] = np.where(den > 1e-9, num / np.maximum(den, 1e-12), np.nan)
        out[t + "__mass"] = den
    return out


def build_trajectories(pop, curves, quarters, trads):
    """Population-averaged P(dextroverse) per alphabet and quarter.

    Bands come from whatever bootstrap draws exist, so this is callable before
    the bootstrap has run (point estimates only) and after each checkpoint.
    """
    out = {}
    for t in trads:
        stack = curves.get(t) or []
        lo = hi = None
        if stack:
            A = np.array(stack, float)
            with np.errstate(invalid="ignore"):
                lo = np.nanpercentile(A, 2.5, axis=0)
                hi = np.nanpercentile(A, 97.5, axis=0)
        p, mass = pop[t], pop[t + "__mass"]
        rows = []
        for o in REPORT_ORDS:
            if not np.isfinite(p[o]) or mass[o] < 3:
                continue
            draws = np.array([c[o] for c in stack], float) if stack else np.array([])
            draws = draws[np.isfinite(draws)]
            rows.append({
                "ord": int(o), "year": int(quarters[o]),
                "mass": round(float(mass[o]), 2),
                "p": round(float(p[o]), 4),
                "p_lo": round(float(lo[o]), 4) if lo is not None else None,
                "p_hi": round(float(hi[o]), 4) if hi is not None else None,
                "H": round(float(h_bits(p[o])), 4),
                "H_lo": round(float(np.percentile(h_bits(draws), 2.5)), 4) if len(draws) else None,
                "H_hi": round(float(np.percentile(h_bits(draws), 97.5)), 4) if len(draws) else None,
            })
        out[t] = rows
    return out


def etruscan_alphabets(frame):
    """Georeferenced records written in an Etruscan alphabet, dual scripts out."""
    return frame[(frame.langgrp == "Etruscan")
                 & frame.trad.str.contains("Etruscan")
                 & ~frame.trad.str.contains("dual")]


def cell_p(des, df, beta, weights, min_mass=20.0):
    """Fitted P(dextroverse) for each language written in each alphabet.

    The same g-computation as population_p, but pooled over time within a
    (language, alphabet) cell, so a language attested in two scripts can be
    compared with itself without falling back on raw proportions.
    """
    mu = 1.0 / (1.0 + np.exp(-np.clip(des.X @ beta, -30, 30)))
    key = (df.langgrp.values[des.rec] + " | " + df.trad.values[des.rec])
    out = {}
    for k in np.unique(key):
        m = key == k
        den = float(weights[m].sum())
        if den >= min_mass:
            lang, alph = k.split(" | ", 1)
            out[k] = {"language": lang, "alphabet": alph,
                      "p": round(float((weights[m] * mu[m]).sum() / den), 4),
                      "mass": round(den, 1)}
    return out


def st_field(des, beta, quarters, years=(-600, -450, -300, -150)):
    """The fitted space-time field on the knot grid, at a few time slices.

    Returned in log-odds, with the aoristic mass supporting each knot, so a
    figure can drop the knots the corpus does not actually cover.
    """
    KX, KT = KNOTS["xy"], KNOTS["t"]
    b = beta[des.slices["k"]].reshape(len(KX), len(KT))
    mass = np.asarray(des.X[:, des.slices["k"]].T @ des.base).reshape(len(KX), len(KT))
    edges = quarters.values.astype(float)
    out = []
    for year in years:
        o = float(np.argmin(np.abs(edges - year)))
        ti, tw = st_weights(np.array([o]), KT, ST["lt"])
        val = (b[:, ti[0]] * tw[0][None, :]).sum(1)
        sup = (mass[:, ti[0]] * tw[0][None, :]).sum(1)
        out.append({"year": int(year),
                    "knots": [{"x": round(float(KX[i, 0]), 1),
                               "y": round(float(KX[i, 1]), 1),
                               "logodds": round(float(val[i]), 4),
                               "mass": round(float(sup[i]), 2)}
                              for i in range(len(KX)) if sup[i] > 1.0]})
    return out


def morans_i(xy, val, wgt, bandwidth=40.0, perms=999, seed=0):
    rng = np.random.default_rng(seed)
    d = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
    W = np.exp(-(d / bandwidth) ** 2)
    np.fill_diagonal(W, 0.0)
    W = W * np.sqrt(np.outer(wgt, wgt))
    z = val - np.average(val, weights=wgt)
    denom = (z ** 2).sum()
    n = len(val)
    stat = n / W.sum() * (W * np.outer(z, z)).sum() / denom
    null = np.empty(perms)
    for b in range(perms):
        zz = z[rng.permutation(n)]
        null[b] = n / W.sum() * (W * np.outer(zz, zz)).sum() / (zz ** 2).sum()
    return float(stat), float((1 + (null >= stat).sum()) / (perms + 1))


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--boot", type=int, default=None)
    ap.add_argument("--folds", type=int, default=10)
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT,
                    help="write the results here instead of db/direction_model.json")
    args = ap.parse_args()
    n_boot = args.boot if args.boot is not None else (40 if args.quick else 300)

    t0 = time.time()
    df, quarters, n_coded, n_messy, n_no_xy = load()
    n_ord = int(quarters.index.max()) + 1
    trads = sorted(df.trad.unique())
    LANGS[:] = sorted(df.langgrp.unique())
    places = sorted(df.place_id.unique())
    print(f"records {len(df)} | alphabets {len(trads)} | languages {len(LANGS)} | findspots {len(places)} "
          f"| excluded messy codes {n_messy} | {time.time()-t0:.1f}s", flush=True)

    results = {
        "n_coded_total": n_coded,
        "n_excluded_messy": n_messy,
        "n_modelled": int(len(df)),
        "n_dropped_no_coordinates": n_no_xy,
        "n_findspots": len(places),
        "alphabets": trads,
        "languages": LANGS,
        "quarter_edges": {int(k): int(v) for k, v in quarters.items()},
        "counts_by_alphabet": df.groupby("trad").agg(n=("y", "size"), raw_dextro=("y", "mean")
                                         ).round(4).to_dict("index"),
    }

    # ---- smoothing parameters ---------------------------------------------
    # A 27-point findspot-blocked CV sweep over (lambda_g, lambda_d, lambda_u)
    # moved the loss only between 0.2029 and 0.2054 — the surface is flat, so
    # the values below (its argmin) are fixed rather than re-swept on every run.
    # --tune re-runs the sweep.
    k = 3 if args.quick else 5
    lam = dict(g=1.0, a=1.0, d=3.0, d0=1.0, u=1.0, k=4.0)

    # ---- which kernel, and at what length scale ----------------------------
    # Swept, not assumed: the field's job is to detect a practice spreading, so
    # the wrong length scale would let the model miss one. Scored on the kernel
    # rung alone, where the kernel is the only thing carrying the outcome.
    print("choosing the space-time kernel (findspot-blocked CV)…", flush=True)
    sweep, best, best_score = [], None, np.inf
    grid = [g for g in KERNEL_GRID if g[2] == 4.0][:2] if args.quick else KERNEL_GRID
    for ls, kern, lk in grid:
        ST.update(ls=ls, kernel=kern)
        KNOTS["xy"], KNOTS["t"] = st_knots(df, n_ord, spacing=ls)
        s = cv_logloss(df, n_ord, trads, places, SPECS["M1b space-time kernel"],
                       {**lam, "k": lk}, k=3, seed=2)
        sweep.append({"length_scale_km": ls, "kernel": kern, "ridge": lk,
                      "spatial_knots": int(len(KNOTS["xy"])), "cv_neg_loglik": round(s, 5)})
        print(f"   {kern}  ls={ls:>5.0f} km  ridge {lk:>5.1f}  "
              f"{len(KNOTS['xy']):>3d} knots  -> {s:.5f}"
              f"{'   *' if s < best_score else ''}", flush=True)
        if s < best_score:
            best, best_score = (ls, kern, lk), s
    ST.update(ls=best[0], kernel=best[1])
    lam["k"] = best[2]

    # The temporal scale was assumed where the spatial one was swept, and a
    # spreading practice the field is meant to detect could be faster than a
    # two-century kernel can express. Swept here at the winning spatial
    # configuration: 8 quarters (the default) against 4 and 2.
    t_best = (8, 8.0)
    if not args.quick:
        for tev, lt in ((4, 4.0), (2, 2.0)):
            ST["lt"] = lt
            KNOTS["xy"], KNOTS["t"] = st_knots(df, n_ord, spacing=best[0], t_every=tev)
            s = cv_logloss(df, n_ord, trads, places, SPECS["M1b space-time kernel"],
                           lam, k=3, seed=2)
            sweep.append({"length_scale_km": best[0], "kernel": best[1],
                          "ridge": best[2], "t_every": tev, "lt": lt,
                          "spatial_knots": int(len(KNOTS["xy"])),
                          "cv_neg_loglik": round(s, 5)})
            print(f"   {best[1]}  lt={lt:g} quarters  {len(KNOTS['t'])} time knots"
                  f"  -> {s:.5f}{'   *' if s < best_score else ''}", flush=True)
            if s < best_score:
                t_best, best_score = (tev, lt), s
    ST["lt"] = t_best[1]
    KNOTS["xy"], KNOTS["t"] = st_knots(df, n_ord, spacing=best[0], t_every=t_best[0])
    print(f"space-time kernel: {best[1]}, {len(KNOTS['xy'])} spatial knots at "
          f"{best[0]:.0f} km, ridge {best[2]} x {len(KNOTS['t'])} time knots "
          f"(lt {t_best[1]:g} quarters)  [{time.time()-t0:.0f}s]", flush=True)
    results["st_kernel"] = {"spatial_knots": int(len(KNOTS["xy"])),
                            "time_knots": int(len(KNOTS["t"])),
                            "length_scale_km": best[0],
                            "kernel": best[1],
                            "ridge": best[2],
                            "length_scale_quarters": ST["lt"],
                            "selection": sweep}
    if args.tune:
        best, best_score = None, np.inf
        print("tuning (findspot-blocked CV)…", flush=True)
        for lg in [1.0, 5.0, 25.0]:
            for ld in [3.0, 15.0, 75.0]:
                for lu in [1.0, 4.0, 16.0]:
                    cand = dict(g=lg, a=1.0, d=ld, d0=1.0, u=lu)
                    s = cv_logloss(df, n_ord, trads, places, SPECS[FINAL],
                                   cand, k=k, seed=1)
                    print(f"   g={lg:<5} d={ld:<6} u={lu:<5} -> {s:.5f}"
                          f"{'   *' if s < best_score else ''}", flush=True)
                    if s < best_score:
                        best, best_score = cand, s
        lam = best
    results["lambda"] = lam
    results["lambda_tuned_this_run"] = bool(args.tune)
    results["cv_folds"] = k
    print(f"lambda {lam}  [{time.time()-t0:.0f}s]", flush=True)

    # ---- model ladder ------------------------------------------------------
    kl = 3 if args.quick else args.folds
    ladder = {}
    heldout_p = {}
    for name, spec in SPECS.items():
        geo_only = name in GEO_SUBSET
        p = cv_predict(df, n_ord, trads, places, spec, lam, k=kl, seed=1, geo_only=geo_only)
        heldout_p[name] = p
        m = metrics(df.y.values, p)
        d = Design(df, n_ord, trads, places, spec)
        beta, ll, _, _ = em_fit(d, lam)
        ok = np.isfinite(p)
        q = np.clip(p[ok], 1e-12, 1 - 1e-12)
        yv = df.y.values[ok]
        ladder[name] = {
            "cv_neg_loglik": round(float(-(yv * np.log(q) + (1 - yv) * np.log(1 - q)).mean()), 5),
            "in_sample_loglik": round(ll, 2), "n": int(d.rec.max()) + 1,
            "geo_subset": bool(geo_only), "metrics": m}
        print(f"   {name:<24} bits {m['log_loss_bits']:.4f}  F1 {m['f1']:.3f}  "
              f"PR-AUC {m['pr_auc']:.3f}  ROC {m['roc_auc']:.3f}   [{time.time()-t0:.0f}s]",
              flush=True)
    results["ladder"] = ladder

    # ---- paired model comparisons, with uncertainty ------------------------
    # A point difference between two CV scores is not a claim. The same records
    # were held out under every model, so the difference is paired per record,
    # and its uncertainty comes from a bootstrap over findspots — the unit the
    # folds were blocked on. Negative delta means the first model is better.
    def rec_bits(p):
        q = np.clip(p, 1e-12, 1 - 1e-12)
        y = df.y.values
        return -(y * np.log2(q) + (1 - y) * np.log2(1 - q))

    PAIRS = [("alphabet alone vs language alone",
              "M2a alphabet, no language", "M2 + language"),
             ("adding language to alphabet",
              "M3 + alphabet", "M2a alphabet, no language"),
             ("adding alphabet to language",
              "M3 + alphabet", "M2 + language"),
             ("geography + field beyond M5",
              "M7 + space-time kernel", "M5 + findspot")]
    rng_p = np.random.default_rng(21)
    groups_p = df.groupby("place_id").indices
    keys_p = list(groups_p)
    results["cv_pairs"] = {}
    print("paired CV differences (bits per record; negative = first better)")
    for label, m_a, m_b in PAIRS:
        d0 = rec_bits(heldout_p[m_a]) - rec_bits(heldout_p[m_b])
        deltas = []
        for _ in range(1000):
            pick = rng_p.integers(0, len(keys_p), len(keys_p))
            idx = np.concatenate([groups_p[keys_p[j]] for j in pick])
            deltas.append(float(d0[idx].mean()))
        lo, hi = np.percentile(deltas, [2.5, 97.5])
        results["cv_pairs"][label] = {
            "first": m_a, "second": m_b,
            "delta_bits": round(float(d0.mean()), 4),
            "lo": round(float(lo), 4), "hi": round(float(hi), 4)}
        print(f"   {label:<34} {d0.mean():+.4f}  [{lo:+.4f}, {hi:+.4f}]", flush=True)

    sub = df                     # every rung is on the same records now

    # ---- final fit ---------------------------------------------------------
    print(f"fitting final model {FINAL}…", flush=True)
    des = Design(df, n_ord, trads, places, SPECS[FINAL])
    beta, ll, XtWX, wpost = em_fit(des, lam, verbose=True, inner=25)
    pop = population_p(des, df, beta, wpost)
    results["final_loglik"] = round(ll, 2)

    des_g = Design(sub, n_ord, trads, places, SPECS["M6 + geography"])
    beta_g, ll_g, XtWX_g, w_g = em_fit(des_g, lam, inner=25)
    gs = des_g.slices["x"]
    results["geography"] = {c: {"beta": round(float(beta_g[gs][j]), 4),
                                "odds_ratio": round(float(np.exp(beta_g[gs][j])), 4)}
                            for j, c in enumerate(GEO_COLS)}

    # The contact test lives in scripts/direction_contact.py, not here: it
    # needs a specification without findspot random effects (a findspot-level
    # covariate and a free effect per findspot compete for the same variation,
    # and on the Etruscan subset that competition made bootstrap draws of the
    # coefficient diverge). Run it after this script; it merges its result into
    # the same JSON under "contact_test".

    # The full model, so the variance decomposition can include the space-time
    # field and the geographic covariates alongside the community terms.
    print("fitting M7 for the variance decomposition…", flush=True)
    des_k = Design(sub, n_ord, trads, places, SPECS["M7 + space-time kernel"])
    beta_k, ll_k, _, w_k = em_fit(des_k, lam, inner=25)
    results["spacetime_field"] = st_field(des_k, beta_k, quarters)

    # The same field with nothing else in the model, which is what a diffusion
    # account would look like, against the field above, which is what is left
    # of it once the writing communities are in.
    print("fitting M1b for the space-time field alone…", flush=True)
    des_st = Design(sub, n_ord, trads, places, SPECS["M1b space-time kernel"])
    beta_st, _, _, _ = em_fit(des_st, lam, inner=25)
    results["spacetime_field_alone"] = st_field(des_st, beta_st, quarters)
    results["cells"] = cell_p(des, df, beta, wpost)

    # ---- variance decomposition -------------------------------------------
    # The blocks are correlated by construction — alphabet nearly determines
    # language, the global curve is collinear with a common shift of the
    # per-alphabet curves, and findspot, geography and the field all live at
    # the level of the place — so per-component variances double-count and
    # their sum missed ~half of Var(eta) in cross-covariances. Shares here are
    # covariance-allocated instead: share_i = Cov_w(c_i, eta) / Var_w(eta),
    # which sums to exactly 1, assigns each covariance half-and-half to the
    # two blocks involved, and can go negative when a block leans against the
    # fit. The correlations that force this choice are reported next to it.
    NAMES = {"g": "time curve", "l": "language", "a": "alphabet",
             "d": "alphabet x time", "u": "findspot", "x": "geography",
             "k": "space-time field"}

    def decompose(d, bb, ww):
        comps = {}
        for key, label in NAMES.items():
            if key in d.slices:
                s = d.slices[key]
                c = np.asarray(d.X[:, s] @ bb[s], float)
                comps[label] = c - np.average(c, weights=ww)
        eta = np.asarray(d.X @ bb, float)
        eta = eta - np.average(eta, weights=ww)
        tot = float(np.average(eta ** 2, weights=ww))
        out = {label: round(float(np.average(c * eta, weights=ww)) / tot, 4)
               for label, c in comps.items()}
        out["shares sum to"] = round(float(sum(out.values())), 4)
        out["linear predictor variance"] = round(tot, 4)
        out["alphabet total (levels + curves)"] = round(
            out.get("alphabet", 0) + out.get("alphabet x time", 0), 4)
        corr = {}
        labels = list(comps)
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                va = float(np.average(comps[labels[i]] ** 2, weights=ww))
                vb = float(np.average(comps[labels[j]] ** 2, weights=ww))
                if va > 1e-12 and vb > 1e-12:
                    r = float(np.average(comps[labels[i]] * comps[labels[j]],
                                         weights=ww) / np.sqrt(va * vb))
                    if abs(r) >= 0.3:
                        corr[f"{labels[i]} ~ {labels[j]}"] = round(r, 3)
        out["component correlations |r| >= 0.3"] = corr
        return out

    results["variance_share"] = decompose(des, beta, wpost)
    results["variance_share_full_model"] = decompose(des_k, beta_k, w_k)

    # ---- bootstrap, clustered on findspot ----------------------------------
    # Everything above is deterministic and is the bulk of what the write-up
    # needs; only the intervals come from below. Dump it now so an hour of
    # bootstrapping does not stand between a finished fit and a usable file.
    partial = args.out.with_suffix(".partial.json")
    results["trajectories"] = build_trajectories(pop, {}, quarters, list(trads) + [""])
    partial.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"wrote {partial} (point estimates, no intervals yet)", flush=True)

    print(f"bootstrap ({n_boot} draws, clustered on findspot)…", flush=True)
    rng = np.random.default_rng(7)
    groups = df.groupby("place_id").indices
    keys = list(groups)
    curves = {t: [] for t in list(trads) + [""]}
    geo_draws = []
    for b in range(n_boot):
        pick = rng.integers(0, len(keys), len(keys))
        idx = np.concatenate([groups[keys[j]] for j in pick])
        bs = df.iloc[idx].reset_index(drop=True)
        try:
            db = Design(bs, n_ord, trads, places, SPECS[FINAL])
            bb, _, _, wb = em_fit(db, lam, beta=beta.copy(), max_em=25)
            pb = population_p(db, bs, bb, wb)
            for t in list(trads) + [""]:
                curves[t].append(pb[t])
            if len(bs) > 500:
                dg = Design(bs, n_ord, trads, places, SPECS["M6 + geography"])
                bg, _, _, _ = em_fit(dg, lam, beta=beta_g.copy(), max_em=25)
                geo_draws.append(bg[dg.slices["x"]])
        except Exception as exc:                      # pragma: no cover
            print(f"   draw {b} failed: {exc}", flush=True)
        if (b + 1) % 10 == 0:
            print(f"   {b+1}/{n_boot}  [{time.time()-t0:.0f}s]", flush=True)
        if (b + 1) % 20 == 0:            # checkpoint: bands from the draws so far
            results["trajectories"] = build_trajectories(pop, curves, quarters, list(trads) + [""])
            results["n_bootstrap_so_far"] = b + 1
            partial.write_text(json.dumps(results, indent=1), encoding="utf-8")

    results["trajectories"] = build_trajectories(pop, curves, quarters, list(trads) + [""])

    if geo_draws:
        A = np.array(geo_draws, float)
        for j, c in enumerate(GEO_COLS):
            results["geography"][c].update(
                lo=round(float(np.percentile(A[:, j], 2.5)), 4),
                hi=round(float(np.percentile(A[:, j], 97.5)), 4),
                or_lo=round(float(np.exp(np.percentile(A[:, j], 2.5))), 4),
                or_hi=round(float(np.exp(np.percentile(A[:, j], 97.5))), 4))

    # ---- residual spatial structure inside Etruscan ------------------------
    # Guard on the language groups: `trads` holds alphabet labels, so testing it
    # for "Etruscan" silently skipped this whole block.
    if "Etruscan" in LANGS:
        e = df[(df.langgrp == "Etruscan") & df.has_xy].copy()
        de = Design(e, n_ord, trads, places, SPECS["M4 + alphabet x time"])
        be, _, _, we = em_fit(de, lam, max_em=40)
        mu_e = 1.0 / (1.0 + np.exp(-np.clip(de.X @ be, -30, 30)))
        resid = np.bincount(de.rec, weights=we * (de.y - mu_e),
                            minlength=int(de.rec.max()) + 1)
        e["resid"] = resid
        g = e.groupby("place_id").agg(lat=("lat", "first"), lon=("lon", "first"),
                                      n=("y", "size"), r=("resid", "mean"),
                                      raw=("y", "mean"))
        g = g[g.n >= 8]
        xy = project(g.lon.values, g.lat.values)
        i_raw, p_raw = morans_i(xy, g.raw.values, g.n.values)
        i_res, p_res = morans_i(xy, g.r.values, g.n.values)
        results["etruscan_spatial"] = {
            "n_findspots": int(len(g)),
            "raw_rate": {"morans_I": round(i_raw, 4), "p": round(p_raw, 4)},
            "model_residual": {"morans_I": round(i_res, 4), "p": round(p_res, 4)},
        }

    # all-tradition raw clustering, for contrast
    a = df[df.has_xy]
    ga = a.groupby("place_id").agg(lat=("lat", "first"), lon=("lon", "first"),
                                   n=("y", "size"), raw=("y", "mean"))
    ga = ga[ga.n >= 8]
    i_all, p_all = morans_i(project(ga.lon.values, ga.lat.values), ga.raw.values, ga.n.values)
    results["pooled_spatial"] = {"n_findspots": int(len(ga)),
                                 "morans_I": round(i_all, 4), "p": round(p_all, 4)}

    results["runtime_seconds"] = round(time.time() - t0, 1)
    results["n_bootstrap"] = n_boot
    args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nwrote {args.out}  [{results['runtime_seconds']}s]")

    print("\n== held out, folds blocked on findspot ==")
    print(f"  {'model':<24}{'bits':>7}{'F1':>7}{'prec':>7}{'rec':>7}{'PR-AUC':>8}{'ROC':>7}")
    for k2, v in ladder.items():
        m = v["metrics"]
        print(f"  {k2:<24}{m['log_loss_bits']:7.3f}{m['f1']:7.3f}{m['precision']:7.3f}"
              f"{m['recall']:7.3f}{m['pr_auc']:8.3f}{m['roc_auc']:7.3f}"
              + ("   (georeferenced subset)" if v["geo_subset"] else ""))
    for label, key in [("variance shares (covariance-allocated), M5", "variance_share"),
                       ("variance shares (covariance-allocated), full model",
                        "variance_share_full_model")]:
        print(f"\n== {label} ==")
        for k2, v in results[key].items():
            print(f"  {k2:<36} {v}")
    print("\n== trajectories (population-averaged P(dextroverse)) ==")
    for t, rows in results["trajectories"].items():
        if not rows:
            continue
        print(f"\n  {t or '(pooled)'}")
        for r in rows[::2]:
            band = (f" [{r['p_lo']:.3f},{r['p_hi']:.3f}]"
                    if r.get("p_lo") is not None else "")
            print(f"    {r['year']:>5}  p={r['p']:.3f}{band}   mass={r['mass']:.0f}")


if __name__ == "__main__":
    sys.exit(main())
