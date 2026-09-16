#!/usr/bin/env python3
"""
Every script's adoption vs the economy: the correlated spatiotemporal
probit of adoption_probit.py, generalised from the Greek/Latin pair to all
alphabet families with >= 100 records.

Outcomes are one-vs-rest binaries per script family (dual scripts count
for both families; 'Este' is pooled with Venetic, Sanzeno/Magre with
Raetic — same tradition, different label). Same design as
adoption_probit.py: national 100-yr trend + interval-integrated economy
field E + Matern-3/2 space-time HSGP (110 km x 8 quarters), penalised
probit marginals, findspot-blocked CV for the field ridge,
findspot-clustered sandwich SEs inflated over 6 posterior draws of E
(Rubin), and the residual correlation matrix by pairwise composite
likelihood (no bootstrap here — 55 pairs; point estimates only, and R is
dominated by mutual exclusivity anyway).

Caveat for the small, regionally confined scripts (Elymian, Camunic,
Raetic...): their beta_E is identified almost entirely from one region's
E level against the rest of Italy, so the "+field" model can absorb most
of it — wide CIs there are honest, not a bug.

    py -3.12 scripts/adoption_scripts.py

Outputs: db/adoption_scripts.json, db/adoption_scripts_grid.npz
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from scipy.special import ndtr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adoption_probit as A                                        # noqa: E402

OUT_JSON = A.ROOT / "db" / "adoption_scripts.json"
OUT_GRID = A.ROOT / "db" / "adoption_scripts_grid.npz"

FAMILIES = {
    "etruscan": ["Etruscan"], "latin": ["Latin"], "greek": ["Greek"],
    "oscan": ["Oscan"], "messapic": ["Messapic"], "lepontic": ["Lepontic"],
    "elymian": ["Elymian"], "faliscan": ["Faliscan"],
    "venetic": ["Venetic", "Este"], "raetic": ["Raetic", "Sanzeno", "Magr"],
    "camunic": ["Camunic"], "umbrian": ["Umbrian"],
    "south_picene": ["South Picene"],
}
MIN_POS = 100
RIDGES = (1.0, 4.0, 16.0)
K_FOLDS, SEED, M_DRAWS = 3, 7, 6


def main():
    t0 = time.time()
    rng = np.random.default_rng(SEED)
    df, counts = A.load()
    n = len(df)

    fams = {}
    for fam, subs in FAMILIES.items():
        y = df.alph.str.contains("|".join(subs), regex=True).astype(float)
        if y.sum() >= MIN_POS:
            fams[fam] = y.values
    dropped = {f: int(df.alph.str.contains("|".join(s)).sum())
               for f, s in FAMILIES.items() if f not in fams}
    print(f"n = {n}; outcomes: "
          + ", ".join(f"{f}({int(v.sum())})" for f, v in fams.items())
          + f"; dropped <{MIN_POS}: {dropped}", flush=True)
    names = list(fams)

    Ebar, Esd, Edraws, _ = A.economy_covariate(df, M_DRAWS, rng)
    n_ord = A.ORD_HI - A.ORD_LO + 1
    bas = A.Bases(df, n_ord)
    t0i, t1i = df.t0.values, df.t1.values
    Xtr_ = bas.trend(t0i, t1i)
    Xst = bas.st(df.px.values, df.py.values, t0i, t1i)
    X0 = np.hstack([np.ones((n, 1)), Xtr_, Ebar[:, None]])
    X1 = np.hstack([X0, Xst])
    iE = 1 + Xtr_.shape[1]
    pen0 = np.r_[0.0, np.ones(Xtr_.shape[1]), 0.0]
    clusters = pd.factorize(df.place_id.values)[0]
    print(f"bases ready [{time.time()-t0:.0f}s]", flush=True)

    def pen1(r):
        return np.r_[pen0, np.full(Xst.shape[1], r)]

    uniq_pl = np.unique(clusters)
    fold = (rng.permuted(np.arange(len(uniq_pl))) % K_FOLDS)[clusters]

    results = {"n": n, "counts": counts, "min_positives": MIN_POS,
               "outcome_positives": {f: int(v.sum()) for f, v in fams.items()},
               "dropped_families": dropped, "e_draws": M_DRAWS,
               "kernel": {"ls_km": A.LS_KM, "lt_quarters": A.LT_Q,
                          "kernel": "m32"},
               "cv_bits": {}, "ridge_selected": {}, "models": {}}
    beta_full, eta_store = {}, {}
    for k in names:
        y = fams[k]
        scores = {}
        for r in RIDGES:
            p_oof = np.full(n, np.nan)
            for fno in range(K_FOLDS):
                tr, te = fold != fno, fold == fno
                b = A.probit_irls(X1[tr], y[tr], pen1(r), iters=20)
                p_oof[te] = ndtr(np.clip(X1[te] @ b, -8, 8))
            scores[r] = A.log_loss_bits(y, p_oof)
        r_sel = min(scores, key=scores.get)
        results["cv_bits"][k] = {str(r): round(v, 4) for r, v in scores.items()}
        results["ridge_selected"][k] = r_sel
        print(f"  cv {k:<13} " + "  ".join(
            f"{r:g}:{v:.4f}" for r, v in scores.items())
            + f"  -> ridge {r_sel:g}  [{time.time()-t0:.0f}s]", flush=True)

        out_k = {}
        for tag, X, pen in [("trend_E", X0, pen0),
                            ("trend_E_field", X1, pen1(r_sel))]:
            b = A.probit_irls(X, y, pen)
            V = A.sandwich_var(X, y, b, pen, clusters)
            se_sw = math.sqrt(V[iE, iE])
            bdraws = []
            for m in range(M_DRAWS):
                Xm = X.copy()
                Xm[:, iE] = Edraws[:, m]
                bdraws.append(A.probit_irls(Xm, y, pen, beta0=b, iters=12)[iE])
            se = math.sqrt(se_sw ** 2
                           + (1 + 1 / M_DRAWS) * float(np.var(bdraws, ddof=1)))
            eta = np.clip(X @ b, -8, 8)
            out_k[tag] = {"beta_E": round(float(b[iE]), 4),
                          "se_total": round(se, 4),
                          "ci95": [round(float(b[iE]) - 1.96 * se, 4),
                                   round(float(b[iE]) + 1.96 * se, 4)],
                          "z": round(float(b[iE]) / se, 2),
                          "log_loss_bits": round(
                              A.log_loss_bits(y, ndtr(eta)), 4)}
            if tag == "trend_E_field":
                beta_full[k], eta_store[k] = b, eta
        print(f"{k:<13} beta_E {out_k['trend_E']['beta_E']:+.3f} -> "
              f"{out_k['trend_E_field']['beta_E']:+.3f} "
              f"(se {out_k['trend_E_field']['se_total']:.3f})"
              f"  [{time.time()-t0:.0f}s]", flush=True)
        results["models"][k] = out_k

    # residual correlations, field model (point estimates)
    R = np.eye(len(names))
    for i, a in enumerate(names):
        for j in range(i + 1, len(names)):
            b = names[j]
            R[i, j] = R[j, i] = A.fit_rho(eta_store[a], eta_store[b],
                                          fams[a], fams[b])
    results["residual_correlation_field_model"] = {
        "outcomes": names, "R": np.round(R, 3).tolist()}
    print(f"R done  [{time.time()-t0:.0f}s]", flush=True)

    # adoption surfaces on the economy grid
    g = pd.read_csv(A.FIELD_CSV)
    g = g[(g.is_land) & (g.in_support)].copy()
    gx, gy = A.project(g.longitude.values, g.latitude.values)
    d_near = cKDTree(np.c_[df.px, df.py]).query(np.c_[gx, gy], k=1)[0]
    g = g[d_near <= A.GRID_COVER_KM].copy()
    gx, gy = A.project(g.longitude.values, g.latitude.values)
    g_t = (g.bin_start_year.values + 700) / 25.0
    Xg = np.hstack([np.ones((len(g), 1)), bas.trend_point(g_t),
                    g.E_mean.values[:, None], bas.st_point(gx, gy, g_t)])
    grid_out = {"lon": g.longitude.values, "lat": g.latitude.values,
                "bin_start": g.bin_start_year.values,
                "E_mean": g.E_mean.values}
    corr = {}
    for k in names:
        eta_g = np.clip(Xg @ beta_full[k], -8, 8)
        grid_out["eta_" + k] = eta_g
        grid_out["p_" + k] = ndtr(eta_g)
        cent = (g.bin_start_year.values // 100) * 100
        per = {str(int(c)): round(float(np.corrcoef(
            eta_g[cent == c], g.E_mean.values[cent == c])[0, 1]), 3)
            for c in np.unique(cent)}
        corr[k] = {"overall_pearson_eta_vs_E": round(float(np.corrcoef(
            eta_g, g.E_mean.values)[0, 1]), 3), "by_century": per}
    results["grid"] = {"cells": int(len(g)),
                       "cover_km": A.GRID_COVER_KM,
                       "adoption_vs_E_correlation": corr}
    np.savez_compressed(OUT_GRID, **grid_out)
    results["runtime_s"] = round(time.time() - t0, 1)
    OUT_JSON.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"wrote {OUT_JSON} and {OUT_GRID}  [{time.time()-t0:.0f}s]",
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
