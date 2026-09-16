#!/usr/bin/env python3
"""
Correlated spatiotemporal probit of Greek and Latin adoption vs the economy.

Question: do regions-through-time with higher adoption of Greek / Latin
writing coincide with higher latent economic activity E(s,t) (the v0.3
field in data/economy/field_v03)?

Four binary outcomes per inscription (dual scripts / bilinguals count for
both sides):

    ga  uses a Greek alphabet          la  uses a Latin alphabet
    gl  language is Greek              ll  language is Latin

"Adoption" is compositional: conditional on an inscription existing at
(s, t), which tradition does it belong to?  Modelling the composition, not
the count, is deliberate — raw epigraphic density is itself correlated with
the settlement evidence inside E, and a count model would rediscover that
circularity.  The probit asks the sharper question: where the economy field
is high, is the *mix* of writing more Greek / more Latin?

Latent-Gaussian model, outcome k in {ga, la, gl, ll}:

    z_ik = alpha_k + g_k(t_i) + beta_k * Ebar_i + f_k(s_i, t_i) + eps_ik
    y_ik = 1{z_ik > 0},   eps_i ~ N(0, R),  R a 4x4 correlation matrix

    g_k    smooth national time trend (1-D HSGP, ridge = unit GP prior)
    f_k    space-time field: Matern-3/2 HSGP with the length scales the
           direction analysis CV-selected on this same corpus (110 km x
           8 quarters); amplitude (ridge) re-selected here by
           findspot-blocked CV per outcome
    Ebar_i economy field integrated over the record's dating interval
           (uniform aoristic weights over its quarters — no midpointing),
           via the continuous basis in db/economy_field_v03_basis.npz
    beta_k the target: probit slope per 1 sd of E

Estimation is the repo's hand-rolled penalised-likelihood stack (no
PyMC/Stan here): penalised probit IRLS per outcome for the marginals, then
the residual correlation R by pairwise composite likelihood (bivariate
probit for each of the 6 pairs, marginals held fixed — Owen's-T exact
bivariate normal CDF).  Chronological uncertainty enters by quadrature
collapse over each record's quarter span, the approximation the structure
check found equivalent to the full EM mixture (+0.0007 bits).

Uncertainty on beta_k: findspot-clustered sandwich SEs, inflated for
posterior uncertainty in E by refitting under M posterior draws of the
field (Rubin's rules).  beta is reported both without the field
("trend+E": the raw geography question) and with it ("+field": does E
predict adoption beyond generic smooth spatiotemporal structure — the
conservative, spatially-deconfounded reading).  rho CIs: findspot-clustered
bootstrap of the pairwise step.

Also written: model-implied adoption surfaces on the economy grid and
their per-century correlation with E (the descriptive "do the maps line
up" answer), for scripts/adoption_figures.py.

    py -3.12 scripts/adoption_probit.py [--quick] [--smoke]

Outputs: db/adoption_probit.json, db/adoption_probit_grid.npz
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from math import gamma, pi
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.linalg import cho_factor, cho_solve
from scipy.optimize import minimize_scalar
from scipy.spatial import cKDTree
from scipy.special import ndtr, owens_t

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from economy_field_eval import EconomyField                        # noqa: E402

DB = ROOT / "db" / "atlas.sqlite"
OUT_JSON = ROOT / "db" / "adoption_probit.json"
OUT_GRID = ROOT / "db" / "adoption_probit_grid.npz"
FIELD_CSV = ROOT / "data" / "economy" / "field_v03" / "economic_field_25yr_v03.csv"

LAT0 = math.radians(42.5)
ORD_LO, ORD_HI = 4, 31            # -700 .. -1, the economy field's window
LS_KM, LT_Q = 110.0, 8.0          # direction_model.json st_kernel selection
LT_TREND = 4.0                    # finer national trend (100 y): the trend
                                  # must absorb the shared secular rise so
                                  # beta_E is not identified off co-trending
BOUNDARY = 1.3
RIDGES = (1.0, 4.0, 16.0, 64.0)
K_FOLDS, SEED = 3, 7
M_DRAWS = 6
N_BOOT_RHO = 100
GRID_COVER_KM = 75.0              # grid cells farther than this from any
                                  # findspot are not scored (extrapolation)
OUTCOMES = ["greek_alph", "latin_alph", "greek_lang", "latin_lang"]


def project(lon, lat):
    return (111.32 * math.cos(LAT0) * np.asarray(lon, float),
            110.57 * np.asarray(lat, float))


def mid_year(ord_):
    return -787.5 + 25.0 * np.asarray(ord_, float)


# --------------------------------------------------------------------- data
def load():
    con = sqlite3.connect(DB)
    df = pd.read_sql("""
        select i.pk, l.name lang, a.label alph, i.latitude lat,
               i.longitude lon, i.q_ord_start qs, i.q_ord_end qe,
               i.place_id
        from inscriptions i
        left join languages l on l.id = i.language_id
        left join alphabets a on a.id = i.alphabet_id
    """, con)
    con.close()
    n0 = len(df)
    df = df.dropna(subset=["lat", "lon", "qs", "qe", "alph", "lang"])
    n_miss = n0 - len(df)
    inbox = ((df.lon >= 6.5) & (df.lon <= 19.0)
             & (df.lat >= 35.5) & (df.lat <= 47.5))
    n_box = int((~inbox).sum())
    df = df[inbox].copy()
    df["qs"] = df.qs.astype(int).clip(lower=ORD_LO)
    df["qe"] = df.qe.astype(int).clip(upper=ORD_HI)
    n_win = int((df.qs > df.qe).sum())
    df = df[df.qs <= df.qe].reset_index(drop=True)

    df["greek_alph"] = df.alph.str.contains("Greek").astype(float)
    df["latin_alph"] = df.alph.str.contains("Latin").astype(float)
    df["greek_lang"] = df.lang.isin(["Greek", "Latin/Greek"]).astype(float)
    df["latin_lang"] = df.lang.isin(["Latin", "Latin/Greek"]).astype(float)

    df["px"], df["py"] = project(df.lon.values, df.lat.values)
    df["t0"] = df.qs - ORD_LO                 # rebased quarter index 0..27
    df["t1"] = df.qe - ORD_LO
    counts = {"total_rows": n0, "dropped_missing": n_miss,
              "dropped_outside_field_box": n_box,
              "dropped_outside_window": n_win, "n": len(df),
              "places": int(df.place_id.nunique()),
              "positives": {k: int(df[k].sum()) for k in OUTCOMES}}
    return df, counts


# --------------------------------------------------------- economy covariate
def economy_covariate(df, m_draws, rng):
    """Interval-averaged E per record: posterior mean, sd, and joint draws.

    Records sharing (rounded) location and quarter span share a row of the
    averaging-functional matrix V; draws use one shared eps so spatial
    correlation of the field posterior is carried into the refits.
    """
    f = EconomyField()
    key = pd.Series(list(zip(df.lon.round(5), df.lat.round(5), df.t0, df.t1)))
    uniq, inv = np.unique(key, return_inverse=True)
    V = np.zeros((len(uniq), len(f.beta)))
    for u, (lo, la, t0, t1) in enumerate(uniq):
        yrs = mid_year(np.arange(t0, t1 + 1) + ORD_LO)
        Phi = f.basis(np.full(len(yrs), lo), np.full(len(yrs), la), yrs)
        V[u] = Phi.mean(axis=0)
    mean = (V @ f.beta - f.offset)[inv]
    sd = np.sqrt(np.maximum(((V @ f.L) ** 2).sum(axis=1), 0.0))[inv]
    eps = rng.standard_normal((f.L.shape[1], m_draws))
    draws = (mean[:, None] + ((V @ f.L) @ eps)[inv])
    return mean, sd, draws, f


# -------------------------------------------------------------- HSGP bases
def spectral_density(omega, ls, d, nu=1.5):
    c = (2 ** d * pi ** (d / 2) * gamma(nu + d / 2) * (2 * nu) ** nu
         / (gamma(nu) * ls ** (2 * nu)))
    return c * (2 * nu / ls ** 2 + omega ** 2) ** (-(nu + d / 2))


class Dim:
    """One HSGP dimension: eigenfunctions on a padded box around the data."""

    def __init__(self, x, ls, m_cap):
        self.c = (x.max() + x.min()) / 2
        half = (x.max() - x.min()) / 2
        self.L = BOUNDARY * max(half, 1e-6)
        self.m = min(m_cap, int(np.ceil(1.75 * self.L / ls)) + 1)
        self.freq = pi * np.arange(1, self.m + 1) / (2 * self.L)

    def eval(self, x):
        x = np.asarray(x, float)
        return (np.sqrt(1.0 / self.L)
                * np.sin(self.freq[None, :] * (x[:, None] - self.c + self.L)))


class Bases:
    """Trend (1-D time) and space-time HSGP blocks, collapsed or pointwise.

    ls_km / lt_q override the module defaults (the GP kernel length
    scales of the space-time block); m_caps are per-dimension
    eigenfunction caps.  j_max, if given, keeps only the j_max
    tensor-product terms with the largest prior variance (spectral
    truncation) so that short length scales stay affordable;
    kept_prior_var records the retained fraction of prior variance.
    """

    def __init__(self, df, n_ord, ls_km=None, lt_q=None,
                 m_caps=(14, 14, 6), j_max=None):
        ls = LS_KM if ls_km is None else float(ls_km)
        lt = LT_Q if lt_q is None else float(lt_q)
        self.ls_km, self.lt_q = ls, lt
        self.n_ord = n_ord
        self.dx = Dim(df.px.values, ls, m_caps[0])
        self.dy = Dim(df.py.values, ls, m_caps[1])
        t = np.arange(n_ord, dtype=float)
        self.dt = Dim(t, lt, m_caps[2])
        self.dtr = Dim(t, LT_TREND, 12)               # trend, finer scale
        om = np.sqrt(self.dx.freq[:, None] ** 2
                     + self.dy.freq[None, :] ** 2).ravel()
        self.w_s = np.sqrt(spectral_density(om, ls, 2))
        self.w_t = np.sqrt(spectral_density(self.dt.freq, lt, 1))
        self.w_tr = np.sqrt(spectral_density(self.dtr.freq, LT_TREND, 1))
        var = (self.w_s[:, None] ** 2) * (self.w_t[None, :] ** 2)
        self.n_pool = int(var.size)
        if j_max is not None and var.size > j_max:
            flat = var.ravel()
            self.sel = np.sort(np.argsort(flat)[::-1][:j_max])
            self.kept_prior_var = float(flat[self.sel].sum() / flat.sum())
        else:
            self.sel = None
            self.kept_prior_var = 1.0
        # interval averages of the temporal eigenfunctions at quarter mids
        grid = t
        self.cum_t = np.vstack([np.zeros(self.dt.m),
                                np.cumsum(self.dt.eval(grid), axis=0)])
        self.cum_tr = np.vstack([np.zeros(self.dtr.m),
                                 np.cumsum(self.dtr.eval(grid), axis=0)])

    def _avg(self, cum, t0, t1):
        return (cum[t1 + 1] - cum[t0]) / (t1 - t0 + 1)[:, None]

    def trend(self, t0, t1):
        return self._avg(self.cum_tr, t0, t1) * self.w_tr[None, :]

    def trend_point(self, t):
        return self.dtr.eval(t) * self.w_tr[None, :]

    def _space(self, px, py):
        phix, phiy = self.dx.eval(px), self.dy.eval(py)
        return (np.einsum("ni,nj->nij", phix, phiy)
                .reshape(len(px), -1) * self.w_s[None, :])

    def _st_cols(self, phis, psi):
        if self.sel is None:
            return (np.einsum("ns,nt->nst", phis, psi)
                    .reshape(len(phis), -1))
        s_idx, t_idx = np.divmod(self.sel, self.dt.m)
        return phis[:, s_idx] * psi[:, t_idx]

    def st(self, px, py, t0, t1):
        psi = self._avg(self.cum_t, t0, t1) * self.w_t[None, :]
        return self._st_cols(self._space(px, py), psi)

    def st_point(self, px, py, t):
        psi = self.dt.eval(t) * self.w_t[None, :]
        return self._st_cols(self._space(px, py), psi)


# ---------------------------------------------------------- probit machinery
def probit_irls(X, y, pen, beta0=None, iters=30, tol=1e-9):
    """Penalised probit MLE. pen: per-column ridge (0 = unpenalised)."""
    n, p = X.shape
    beta = np.zeros(p) if beta0 is None else beta0.copy()
    P = np.diag(pen)
    obj_old = np.inf
    for _ in range(iters):
        eta = np.clip(X @ beta, -8, 8)
        prob = np.clip(ndtr(eta), 1e-10, 1 - 1e-10)
        phi = np.exp(-0.5 * eta ** 2) / math.sqrt(2 * pi)
        w = phi ** 2 / (prob * (1 - prob))
        z = eta + (y - prob) / np.maximum(phi, 1e-10)
        Xw = X * w[:, None]
        A = X.T @ Xw + P
        beta_new = cho_solve(cho_factor(A), Xw.T @ z)
        obj = (-np.sum(y * np.log(prob) + (1 - y) * np.log(1 - prob))
               + 0.5 * beta @ P @ beta)
        step = beta_new - beta
        # dampen if the full step worsens the penalised deviance
        for _ in range(6):
            cand = beta + step
            e2 = np.clip(X @ cand, -8, 8)
            p2 = np.clip(ndtr(e2), 1e-10, 1 - 1e-10)
            o2 = (-np.sum(y * np.log(p2) + (1 - y) * np.log(1 - p2))
                  + 0.5 * cand @ P @ cand)
            if o2 <= obj + 1e-9:
                break
            step *= 0.5
        beta = beta + step
        if abs(obj_old - o2) < tol * (1 + abs(o2)):
            beta, obj_old = cand, o2
            break
        obj_old = o2
    return beta


def sandwich_var(X, y, beta, pen, clusters):
    eta = np.clip(X @ beta, -8, 8)
    prob = np.clip(ndtr(eta), 1e-10, 1 - 1e-10)
    phi = np.exp(-0.5 * eta ** 2) / math.sqrt(2 * pi)
    w = phi ** 2 / (prob * (1 - prob))
    A = X.T @ (X * w[:, None]) + np.diag(pen)
    bread = cho_solve(cho_factor(A), np.eye(X.shape[1]))
    s = X * (phi * (y - prob) / (prob * (1 - prob)))[:, None]
    G = np.zeros((X.shape[1], X.shape[1]))
    order = np.argsort(clusters, kind="stable")
    cl, s = clusters[order], s[order]
    cuts = np.flatnonzero(np.r_[True, cl[1:] != cl[:-1], True])
    for a, b in zip(cuts[:-1], cuts[1:]):
        v = s[a:b].sum(axis=0)
        G += np.outer(v, v)
    return bread @ G @ bread


def log_loss_bits(y, p):
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-(y * np.log2(p) + (1 - y) * np.log2(1 - p)).mean())


# ------------------------------------------------- bivariate normal (Owen)
def bvn_cdf(h, k, rho):
    """P(Z1<=h, Z2<=k), corr rho — Owen (1956), vectorised."""
    h = np.where(np.abs(h) < 1e-10, 1e-10, h)
    k = np.where(np.abs(k) < 1e-10, 1e-10, k)
    r = math.sqrt(max(1.0 - rho * rho, 1e-12))
    a1 = (k - rho * h) / (h * r)
    a2 = (h - rho * k) / (k * r)
    delta = np.where(h * k > 0, 0.0, 0.5)
    out = 0.5 * (ndtr(h) + ndtr(k)) - owens_t(h, a1) - owens_t(k, a2) - delta
    return np.clip(out, 1e-12, 1.0)


def pair_loglik(rho, e1, e2, y1, y2):
    p11 = bvn_cdf(e1, e2, rho)
    p1, p2 = ndtr(e1), ndtr(e2)
    p10 = np.clip(p1 - p11, 1e-12, 1)
    p01 = np.clip(p2 - p11, 1e-12, 1)
    p00 = np.clip(1 - p1 - p2 + p11, 1e-12, 1)
    return float(np.sum(np.where(y1 > 0, np.where(y2 > 0, np.log(p11), np.log(p10)),
                                 np.where(y2 > 0, np.log(p01), np.log(p00)))))


def fit_rho(e1, e2, y1, y2):
    res = minimize_scalar(lambda r: -pair_loglik(r, e1, e2, y1, y2),
                          bounds=(-0.995, 0.995), method="bounded",
                          options={"xatol": 1e-4})
    return float(res.x)


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="skip CV (ridge=1), fewer draws/boots")
    ap.add_argument("--smoke", action="store_true",
                    help="3000-record subsample pipeline test")
    args = ap.parse_args()
    t_start = time.time()
    rng = np.random.default_rng(SEED)

    df, counts = load()
    if args.smoke:
        df = df.sample(3000, random_state=SEED).reset_index(drop=True)
    n = len(df)
    print(f"n = {n} records, {counts['places']} places  "
          f"positives {counts['positives']}", flush=True)

    m_draws = 2 if args.smoke else (3 if args.quick else M_DRAWS)
    Ebar, Esd, Edraws, _ = economy_covariate(df, m_draws, rng)
    df["Ebar"] = Ebar
    print(f"Ebar: mean {Ebar.mean():+.2f}, sd {Ebar.std():.2f}, "
          f"post-sd median {np.median(Esd):.2f}  [{time.time()-t_start:.0f}s]",
          flush=True)

    n_ord = ORD_HI - ORD_LO + 1
    bas = Bases(df, n_ord)
    t0, t1 = df.t0.values, df.t1.values
    Xtr_ = bas.trend(t0, t1)
    Xst = bas.st(df.px.values, df.py.values, t0, t1)
    print(f"bases: trend {Xtr_.shape[1]}, space-time {Xst.shape[1]} columns",
          flush=True)

    ones = np.ones((n, 1))
    X0 = np.hstack([ones, Xtr_, Ebar[:, None]])                # trend+E
    X1 = np.hstack([X0, Xst])                                  # +field
    iE = 1 + Xtr_.shape[1]                                     # E column
    pen0 = np.r_[0.0, np.ones(Xtr_.shape[1]), 0.0]

    def pen1(ridge):
        return np.r_[pen0, np.full(Xst.shape[1], ridge)]

    places = df.place_id.values
    clusters = pd.factorize(places)[0]

    # ---- ridge by findspot-blocked CV --------------------------------
    ridge_sel, cv_table = {}, {}
    if args.quick or args.smoke:
        ridge_sel = {k: 1.0 for k in OUTCOMES}
    else:
        uniq_pl = np.unique(clusters)
        fold_of = rng.permuted(np.arange(len(uniq_pl))) % K_FOLDS
        fold = fold_of[clusters]
        for k in OUTCOMES:
            y = df[k].values
            scores = {}
            for r in RIDGES:
                p_oof = np.full(n, np.nan)
                for fno in range(K_FOLDS):
                    tr, te = fold != fno, fold == fno
                    b = probit_irls(X1[tr], y[tr], pen1(r), iters=20)
                    p_oof[te] = ndtr(np.clip(X1[te] @ b, -8, 8))
                scores[r] = log_loss_bits(y, p_oof)
                print(f"  cv {k:<11} ridge {r:<5} {scores[r]:.4f} bits"
                      f"  [{time.time()-t_start:.0f}s]", flush=True)
            ridge_sel[k] = min(scores, key=scores.get)
            cv_table[k] = {str(r): round(v, 4) for r, v in scores.items()}

    # ---- full fits, sandwich SEs, E-draw refits ----------------------
    results = {"n": n, "counts": counts, "cv_bits": cv_table,
               "ridge_selected": ridge_sel,
               "kernel": {"ls_km": LS_KM, "lt_quarters": LT_Q,
                          "kernel": "m32", "boundary": BOUNDARY,
                          "source": "direction_model.json st_kernel"},
               "e_draws": m_draws, "models": {}}
    beta_full, eta_store = {}, {}
    for k in OUTCOMES:
        y = df[k].values
        r = ridge_sel[k]
        out_k = {}
        for tag, X, pen in [("trend_E", X0, pen0),
                            ("trend_E_field", X1, pen1(r))]:
            b = probit_irls(X, y, pen)
            V = sandwich_var(X, y, b, pen, clusters)
            se_sw = math.sqrt(V[iE, iE])
            bdraws = []
            for m in range(m_draws):
                Xm = X.copy()
                Xm[:, iE] = Edraws[:, m]
                bm = probit_irls(Xm, y, pen, beta0=b, iters=12)
                bdraws.append(bm[iE])
            var_between = float(np.var(bdraws, ddof=1)) if m_draws > 1 else 0.0
            se_tot = math.sqrt(se_sw ** 2 + (1 + 1 / m_draws) * var_between)
            eta = np.clip(X @ b, -8, 8)
            out_k[tag] = {
                "beta_E": round(float(b[iE]), 4),
                "se_cluster": round(se_sw, 4),
                "se_total": round(se_tot, 4),
                "ci95": [round(float(b[iE]) - 1.96 * se_tot, 4),
                         round(float(b[iE]) + 1.96 * se_tot, 4)],
                "z": round(float(b[iE]) / se_tot, 2),
                "beta_E_draws": [round(float(v), 4) for v in bdraws],
                "log_loss_bits": round(log_loss_bits(y, ndtr(eta)), 4),
            }
            if tag == "trend_E_field":
                beta_full[k], eta_store[k] = b, eta
            else:
                eta_store[k + "_nofield"] = eta
            print(f"{k:<11} {tag:<14} beta_E {b[iE]:+.3f} "
                  f"(se {se_tot:.3f})  [{time.time()-t_start:.0f}s]",
                  flush=True)
        results["models"][k] = out_k

    # ---- residual correlation matrices (pairwise composite) ----------
    pairs = [(a, b) for i, a in enumerate(OUTCOMES)
             for b in OUTCOMES[i + 1:]]
    for tag, suff in [("trend_E", "_nofield"), ("trend_E_field", "")]:
        R = np.eye(4)
        for a, b in pairs:
            ia, ib = OUTCOMES.index(a), OUTCOMES.index(b)
            rho = fit_rho(eta_store[a + suff] if suff else eta_store[a],
                          eta_store[b + suff] if suff else eta_store[b],
                          df[a].values, df[b].values)
            R[ia, ib] = R[ib, ia] = rho
        results.setdefault("residual_correlation", {})[tag] = {
            "outcomes": OUTCOMES, "R": np.round(R, 3).tolist()}
        print(f"R ({tag}):\n{np.round(R, 3)}", flush=True)

    # rho bootstrap (field model), clustered on findspot
    nb = 10 if (args.quick or args.smoke) else N_BOOT_RHO
    groups = pd.Series(np.arange(n)).groupby(clusters).indices
    keys = list(groups)
    boots = {f"{a}|{b}": [] for a, b in pairs}
    for _ in range(nb):
        pick = rng.integers(0, len(keys), len(keys))
        idx = np.concatenate([groups[keys[j]] for j in pick])
        for a, b in pairs:
            boots[f"{a}|{b}"].append(
                fit_rho(eta_store[a][idx], eta_store[b][idx],
                        df[a].values[idx], df[b].values[idx]))
    results["rho_ci95_field_model"] = {
        pk: [round(float(np.percentile(v, 2.5)), 3),
             round(float(np.percentile(v, 97.5)), 3)]
        for pk, v in boots.items()}
    print(f"rho bootstrap done ({nb})  [{time.time()-t_start:.0f}s]",
          flush=True)

    # ---- adoption surfaces on the economy grid -----------------------
    g = pd.read_csv(FIELD_CSV)
    g = g[(g.is_land) & (g.in_support)].copy()
    gx, gy = project(g.longitude.values, g.latitude.values)
    d_near = cKDTree(np.c_[df.px, df.py]).query(np.c_[gx, gy], k=1)[0]
    g = g[d_near <= GRID_COVER_KM].copy()
    gx, gy = project(g.longitude.values, g.latitude.values)
    g_t = (g.bin_start_year.values + 700) / 25.0        # rebased quarter
    Xg = np.hstack([np.ones((len(g), 1)), bas.trend_point(g_t),
                    g.E_mean.values[:, None],
                    bas.st_point(gx, gy, g_t)])
    grid_out = {"lon": g.longitude.values, "lat": g.latitude.values,
                "bin_start": g.bin_start_year.values,
                "E_mean": g.E_mean.values}
    corr = {}
    for k in OUTCOMES:
        eta_g = np.clip(Xg @ beta_full[k], -8, 8)
        grid_out["eta_" + k] = eta_g
        grid_out["p_" + k] = ndtr(eta_g)
        cent = (g.bin_start_year.values // 100) * 100
        per = {}
        for c in np.unique(cent):
            m = cent == c
            per[str(int(c))] = round(float(np.corrcoef(
                eta_g[m], g.E_mean.values[m])[0, 1]), 3)
        corr[k] = {"overall_pearson_eta_vs_E": round(float(np.corrcoef(
            eta_g, g.E_mean.values)[0, 1]), 3), "by_century": per}
    results["grid"] = {"cells": int(len(g)),
                       "cover_km": GRID_COVER_KM,
                       "adoption_vs_E_correlation": corr}
    np.savez_compressed(OUT_GRID, **grid_out)

    results["runtime_s"] = round(time.time() - t_start, 1)
    OUT_JSON.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"wrote {OUT_JSON} and {OUT_GRID}  [{time.time()-t_start:.0f}s]",
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
