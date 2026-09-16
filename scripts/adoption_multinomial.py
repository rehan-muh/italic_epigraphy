#!/usr/bin/env python3
"""
Full multinomial spatiotemporal model of script adoption vs the economy.

Upgrades the one-vs-rest probits of adoption_scripts.py to a single
penalised multinomial (softmax) logit over K = 11 categories: the ten
script families with >= 100 records plus 'other' (Camunic, Umbrian,
South Picene, remaining rarities). Statistical improvements over the
one-vs-rest construction:

  * shares sum to one by construction — the mutual-exclusivity structure
    that pinned pairwise residual correlations at the bound is now
    structural, and the dominant-script maps / trajectories are true
    posterior shares;
  * dual-script inscriptions enter fractionally (1/m to each of their m
    families), not double-counted;
  * one joint likelihood, so cross-category uncertainty is coherent.

Per-category linear predictor (same design as the probit runs):

    eta_ik = alpha_k + trend_k(t_i) + beta_k * Ebar_i + f_k(s_i, t_i)

with the aoristic interval collapse, the Matern-3/2 HSGP space-time field
(110 km x 8 quarters; amplitude re-selected by findspot-blocked CV on
multinomial log-loss), and Ebar the interval-integrated economy field.
Identification: the likelihood is invariant to adding a constant vector
across categories, so the unpenalised rows (alpha, beta) are centred to
sum to zero across categories — beta_k is the effect of E on category
k's log-share relative to the geometric mean of all 11 categories.
Penalised blocks are identified by their ridges.

Estimation: block-coordinate Newton with global step-halving on the
penalised multinomial deviance. Uncertainty on the centred beta vector:
findspot-clustered sandwich computed matrix-free — the full Hessian
(11 x 781 parameters) is never formed; bread columns are obtained by
block-Jacobi-preconditioned conjugate gradients on the projected
(sum-to-zero) operator, and cluster scores lie in that subspace
automatically because response rows sum to one. Posterior uncertainty in
E is propagated by refitting under M = 6 joint field draws (Rubin).

    py -3.12 scripts/adoption_multinomial.py [--quick]

Outputs: db/adoption_multinomial.json, db/adoption_multinomial_grid.npz
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.linalg import cho_factor, cho_solve
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adoption_probit as A                                        # noqa: E402
from adoption_scripts import FAMILIES                              # noqa: E402

OUT_JSON = A.ROOT / "db" / "adoption_multinomial.json"
OUT_GRID = A.ROOT / "db" / "adoption_multinomial_grid.npz"
OUT_JSON_LANG = A.ROOT / "db" / "adoption_multinomial_lang.json"
OUT_GRID_LANG = A.ROOT / "db" / "adoption_multinomial_lang_grid.npz"

MIN_POS = 100
RIDGES = (0.015625, 0.0625, 0.25, 1.0)
K_FOLDS, SEED, M_DRAWS = 3, 7, 6
MAX_SWEEPS, SWEEP_TOL = 60, 5e-3   # stop when a full sweep improves the
                                   # penalised deviance by < SWEEP_TOL —
                                   # beta_E is stable to <1e-3 well before


# ------------------------------------------------------------ response matrix
def _fractional(ind):
    keep = [f for f, v in ind.items() if v.sum() >= MIN_POS]
    Yf = np.stack([ind[f] for f in keep], axis=1)
    rows = Yf.sum(axis=1)
    other = (rows == 0).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        Yf = np.where(rows[:, None] > 0, Yf / np.maximum(rows, 1)[:, None], 0)
    Y = np.hstack([Yf, other[:, None]])
    return Y, keep + ["other"], int((rows > 1).sum())


def build_response(df):
    """Fractional script-family matrix Y (n x K), rows summing to 1."""
    ind = {}
    for fam, subs in FAMILIES.items():
        v = df.alph.str.contains("|".join(subs), regex=True).values
        ind[fam] = v.astype(float)
    return _fractional(ind)


def build_response_language(df):
    """Fractional language matrix (n x K). 'Latin/Greek' splits 1/2-1/2."""
    langs = ["Etruscan", "Latin", "Greek", "Oscan", "Messapic",
             "Cisalpine Celtic", "Elymian", "Raetic", "Faliscan",
             "Venetic", "Camunic", "Umbrian", "Other Sabellic"]
    ind = {}
    for lg in langs:
        v = (df.lang == lg).values.astype(float)
        if lg in ("Latin", "Greek"):
            v = v + (df.lang == "Latin/Greek").values.astype(float)
        ind[lg.lower().replace(" ", "_")] = v
    return _fractional(ind)


# ----------------------------------------------------------------- fitter
def softmax(Eta):
    m = Eta.max(axis=1, keepdims=True)
    e = np.exp(Eta - m)
    return e / e.sum(axis=1, keepdims=True)


def objective(X, Y, B, pen):
    P = softmax(X @ B)
    dev = -float(np.sum(Y * np.log(np.clip(P, 1e-12, None))))
    return dev + 0.5 * float(np.sum(pen[:, None] * B * B))


def center(B, free_rows):
    B[free_rows] -= B[free_rows].mean(axis=1, keepdims=True)
    return B


def fit_multinomial(X, Y, pen, free_rows, B0=None, max_sweeps=MAX_SWEEPS,
                    verbose=False, t0=None, tol=SWEEP_TOL):
    """Block-coordinate Newton for penalised multinomial logit.

    X: n x p shared design; Y: n x K fractional responses; pen: length-p
    ridge vector applied to every category's block; free_rows: indices of
    unpenalised rows (centred across categories after every sweep).
    """
    n, p = X.shape
    K = Y.shape[1]
    B = np.zeros((p, K)) if B0 is None else B0.copy()
    obj = objective(X, Y, B, pen)
    for sweep in range(max_sweeps):
        obj_prev = obj
        for k in range(K):
            Eta = X @ B
            P = softmax(Eta)
            pk = np.clip(P[:, k], 1e-10, 1 - 1e-10)
            w = np.maximum(pk * (1 - pk), 1e-8)
            z = Eta[:, k] + (Y[:, k] - pk) / w
            Xw = X * w[:, None]
            Anew = X.T @ Xw + np.diag(pen)
            bk_new = cho_solve(cho_factor(Anew), Xw.T @ z)
            step = bk_new - B[:, k]
            for _ in range(6):
                Bc = B.copy()
                Bc[:, k] = B[:, k] + step
                o2 = objective(X, Y, Bc, pen)
                if o2 <= obj + 1e-9:
                    break
                step *= 0.5
            B, obj = Bc, o2
        B = center(B, free_rows)
        obj = objective(X, Y, B, pen)
        if verbose:
            el = f"  [{time.time()-t0:.0f}s]" if t0 else ""
            print(f"    sweep {sweep+1}: obj {obj:.2f}{el}", flush=True)
        if abs(obj_prev - obj) < tol:
            break
    return B


# ---------------------------------------------- matrix-free cluster sandwich
def sandwich_beta(X, Y, B, pen, free_rows, iE, clusters, tol=1e-8):
    """Clustered sandwich variance of the centred beta_E vector.

    Bread columns solved by preconditioned CG on the projected operator
    Pi H Pi (Pi centres the unpenalised rows across categories); the full
    Hessian is applied matrix-free. Preconditioner: per-category
    block-Jacobi using diagonal-block Cholesky factors.
    """
    n, p = X.shape
    K = Y.shape[1]
    P = softmax(X @ B)

    def proj(V):                       # centre free rows across categories
        V = V.copy()
        V[free_rows] -= V[free_rows].mean(axis=1, keepdims=True)
        return V

    def hess_v(V):                     # V: p x K -> H V, matrix-free
        U = X @ V                      # n x K
        S = (P * U).sum(axis=1, keepdims=True)
        M = P * (U - S)                # n x K
        return X.T @ M + pen[:, None] * V

    # block-Jacobi preconditioner
    chols = []
    for k in range(K):
        w = np.maximum(P[:, k] * (1 - P[:, k]), 1e-8)
        chols.append(cho_factor(X.T @ (X * w[:, None]) + np.diag(pen)
                                + 1e-8 * np.eye(p)))

    def precond(V):
        out = np.empty_like(V)
        for k in range(K):
            out[:, k] = cho_solve(chols[k], V[:, k])
        return proj(out)

    def cg(rhs, iters=400):
        xv = np.zeros_like(rhs)
        r = proj(rhs)
        z = precond(r)
        d = z.copy()
        rz = float(np.sum(r * z))
        for _ in range(iters):
            Hd = proj(hess_v(d))
            alpha = rz / float(np.sum(d * Hd))
            xv += alpha * d
            r -= alpha * Hd
            if math.sqrt(float(np.sum(r * r))) < tol * (1 + math.sqrt(
                    float(np.sum(rhs * rhs)))):
                break
            z = precond(r)
            rz_new = float(np.sum(r * z))
            d = z + (rz_new / rz) * d
            rz = rz_new
        return xv

    # bread columns for the K centred beta functionals
    Bread = []                          # each entry: p x K solution
    for k in range(K):
        rhs = np.zeros((p, K))
        rhs[iE, k] = 1.0
        Bread.append(cg(proj(rhs)))

    # cluster scores contracted against bread columns
    resid = Y - P                       # n x K, rows sum to 0
    T = np.zeros((len(np.unique(clusters)), K))
    for j, Bj in enumerate(Bread):
        Aj = X @ Bj                     # n x K
        per_obs = (resid * Aj).sum(axis=1)
        T[:, j] = np.bincount(clusters, weights=per_obs)
    return T.T @ T                      # K x K variance of centred beta


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--language", action="store_true",
                    help="language categories instead of script families")
    ap.add_argument("--ls-km", type=float, default=None,
                    help="spatial length scale (default: module constant)")
    ap.add_argument("--lt-q", type=float, default=None,
                    help="temporal length scale in quarters")
    ap.add_argument("--ridge", type=float, default=None,
                    help="fix the field ridge (= 1/tau^2), skipping CV — "
                         "use with the Bayesian MAP from "
                         "adoption_lengthscale.py")
    ap.add_argument("--j-max", type=int, default=None,
                    help="spectral truncation budget for the field basis")
    args = ap.parse_args()
    t0 = time.time()
    rng = np.random.default_rng(SEED)

    df, counts = A.load()
    n = len(df)
    if args.language:
        Y, names, n_dual = build_response_language(df)
        out_json, out_grid = OUT_JSON_LANG, OUT_GRID_LANG
    else:
        Y, names, n_dual = build_response(df)
        out_json, out_grid = OUT_JSON, OUT_GRID
    K = len(names)
    print(f"n = {n}, K = {K} categories "
          + ", ".join(f"{nm}({Y[:, i].sum():.0f})"
                      for i, nm in enumerate(names))
          + f"; fractional duals: {n_dual}", flush=True)

    m_draws = 3 if args.quick else M_DRAWS
    Ebar, Esd, Edraws, _ = A.economy_covariate(df, m_draws, rng)
    n_ord = A.ORD_HI - A.ORD_LO + 1
    bas = A.Bases(df, n_ord, ls_km=args.ls_km, lt_q=args.lt_q,
                  m_caps=(28, 30, 16) if args.j_max else (14, 14, 6),
                  j_max=args.j_max)
    Xtr_ = bas.trend(df.t0.values, df.t1.values)
    Xst = bas.st(df.px.values, df.py.values, df.t0.values, df.t1.values)
    X = np.hstack([np.ones((n, 1)), Xtr_, Ebar[:, None], Xst])
    iE = 1 + Xtr_.shape[1]
    free_rows = np.array([0, iE])
    clusters = pd.factorize(df.place_id.values)[0]
    print(f"design: {X.shape[1]} columns x {K} categories "
          f"[{time.time()-t0:.0f}s]", flush=True)

    def pen_vec(r):
        v = np.zeros(X.shape[1])
        v[1:1 + Xtr_.shape[1]] = 1.0
        v[iE + 1:] = r
        return v

    def mlogloss_bits(Y_, P_):
        return float(-np.sum(Y_ * np.log2(np.clip(P_, 1e-12, None)))
                     / len(Y_))

    # ---- CV for the field ridge --------------------------------------
    results = {"n": n, "categories": names,
               "category_totals": {nm: round(float(Y[:, i].sum()), 1)
                                   for i, nm in enumerate(names)},
               "fractional_dual_records": n_dual, "e_draws": m_draws,
               "kernel": {"ls_km": bas.ls_km, "lt_quarters": bas.lt_q,
                          "kernel": "m32", "j_max": args.j_max,
                          "n_field_cols": int(Xst.shape[1]),
                          "kept_prior_var": round(bas.kept_prior_var, 4)},
               "cv_bits": {}}
    if args.ridge is not None:
        r_sel = args.ridge
        results["ridge_fixed"] = True
    elif args.quick:
        r_sel = 4.0
    else:
        uniq_pl = np.unique(clusters)
        fold = (rng.permuted(np.arange(len(uniq_pl))) % K_FOLDS)[clusters]
        scores = {}
        for r in RIDGES:
            oof = np.zeros_like(Y)
            for fno in range(K_FOLDS):
                tr, te = fold != fno, fold == fno
                Bf = fit_multinomial(X[tr], Y[tr], pen_vec(r), free_rows,
                                     max_sweeps=12)
                oof[te] = softmax(X[te] @ Bf)
            scores[r] = mlogloss_bits(Y, oof)
            print(f"  cv ridge {r:<5} {scores[r]:.4f} bits "
                  f"[{time.time()-t0:.0f}s]", flush=True)
        r_sel = min(scores, key=scores.get)
        results["cv_bits"] = {str(r): round(v, 4) for r, v in scores.items()}
    results["ridge_selected"] = r_sel

    # ---- fits: trend+E and trend+E+field -----------------------------
    i_etr = names.index("etruscan")
    results["models"] = {}
    B = None
    for tag, Xm_, pen in [("trend_E", X[:, :iE + 1],
                           np.r_[np.zeros(1), np.ones(Xtr_.shape[1]), 0.0]),
                          ("trend_E_field", X, pen_vec(r_sel))]:
        Bt = fit_multinomial(Xm_, Y, pen, free_rows,
                             verbose=(tag == "trend_E_field"), t0=t0)
        P_hat = softmax(Xm_ @ Bt)
        bits = round(mlogloss_bits(Y, P_hat), 4)
        print(f"{tag} fit done, {bits} bits [{time.time()-t0:.0f}s]",
              flush=True)
        V_sw = sandwich_beta(Xm_, Y, Bt, pen, free_rows, iE, clusters)
        print(f"  sandwich done [{time.time()-t0:.0f}s]", flush=True)
        beta_draws = np.zeros((m_draws, K))
        for m in range(m_draws):
            Xd = Xm_.copy()
            Xd[:, iE] = Edraws[:, m]
            Bm = fit_multinomial(Xd, Y, pen, free_rows, B0=Bt, max_sweeps=6)
            beta_draws[m] = Bm[iE]
        V_between = (np.cov(beta_draws.T, ddof=1) if m_draws > 1
                     else 0 * V_sw)
        V_tot = V_sw + (1 + 1 / m_draws) * V_between
        beta = Bt[iE]
        out = {}
        for i, nm in enumerate(names):
            se = math.sqrt(V_tot[i, i])
            d = beta[i] - beta[i_etr]
            se_d = math.sqrt(max(V_tot[i, i] + V_tot[i_etr, i_etr]
                                 - 2 * V_tot[i, i_etr], 0)) \
                if i != i_etr else 0
            out[nm] = {"beta_E_centred": round(float(beta[i]), 4),
                       "se_total": round(se, 4),
                       "ci95": [round(float(beta[i]) - 1.96 * se, 4),
                                round(float(beta[i]) + 1.96 * se, 4)],
                       "z": round(float(beta[i]) / se, 2),
                       "contrast_vs_etruscan": round(float(d), 4),
                       "contrast_se": round(se_d, 4)}
            print(f"  {tag} {nm:<16} beta_E {beta[i]:+.3f} (se {se:.3f})",
                  flush=True)
        results["models"][tag] = {"log_loss_bits": bits, "beta_E": out}
        if tag == "trend_E_field":
            B = Bt
            results["log_loss_bits"] = bits
            results["beta_E"] = out            # back-compat for figures
    results["beta_note"] = ("beta_E_centred: effect of +1 sd of E on the "
                            "category's log-share relative to the "
                            "geometric mean of all categories (logit "
                            "scale). Contrasts vs Etruscan also given.")

    # ---- grid shares -------------------------------------------------
    g = pd.read_csv(A.FIELD_CSV)
    g = g[(g.is_land) & (g.in_support)].copy()
    gx, gy = A.project(g.longitude.values, g.latitude.values)
    d_near = cKDTree(np.c_[df.px, df.py]).query(np.c_[gx, gy], k=1)[0]
    g = g[d_near <= A.GRID_COVER_KM].copy()
    gx, gy = A.project(g.longitude.values, g.latitude.values)
    g_t = (g.bin_start_year.values + 700) / 25.0
    Xg = np.hstack([np.ones((len(g), 1)), bas.trend_point(g_t),
                    g.E_mean.values[:, None], bas.st_point(gx, gy, g_t)])
    Pg = softmax(Xg @ B)
    grid_out = {"lon": g.longitude.values, "lat": g.latitude.values,
                "bin_start": g.bin_start_year.values,
                "E_mean": g.E_mean.values,
                "names": np.array(names)}
    for i, nm in enumerate(names):
        grid_out["p_" + nm] = Pg[:, i]
    corr = {}
    for i, nm in enumerate(names):
        eta_rel = np.log(np.clip(Pg[:, i], 1e-12, None)) \
            - np.log(np.clip(Pg, 1e-12, None)).mean(axis=1)
        cent = (g.bin_start_year.values // 100) * 100
        per = {str(int(c)): round(float(np.corrcoef(
            eta_rel[cent == c], g.E_mean.values[cent == c])[0, 1]), 3)
            for c in np.unique(cent)}
        corr[nm] = {"overall": round(float(np.corrcoef(
            eta_rel, g.E_mean.values)[0, 1]), 3), "by_century": per}
    results["grid"] = {"cells": int(len(g)), "cover_km": A.GRID_COVER_KM,
                       "logshare_vs_E_correlation": corr}
    np.savez_compressed(out_grid, **grid_out)
    results["runtime_s"] = round(time.time() - t0, 1)
    out_json.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"wrote {out_json} and {out_grid} [{time.time()-t0:.0f}s]",
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
