#!/usr/bin/env python3
"""
Two upgrades to the space-time field, judged by held-out comparison.

1. RANK, decoupled from smoothness. The kernel sweep tied knot spacing to
   the length scale, so "45 km" meant both more knots AND a rougher kernel;
   rank per se was never varied. Here the knot grid is densified to half the
   length scale (55 km, ~4x the knots) with the kernel held at the selected
   110 km, so any gain is attributable to rank alone.

2. A Hilbert-space (Solin & Sarkka 2020) reduced-rank GP. The knot basis is
   a kernel interpolant with a ridge -- an honest but ad hoc stand-in for a
   GP prior. The HSGP basis is the Laplacian eigenfunctions of a box around
   the domain, with each column scaled by the square root of the kernel's
   spectral density, so the implied prior IS (an approximation to) the
   stationary GP with the selected kernel. Separable in space x time, with
   the same Matern 3/2 family in both. One caveat is inherited from the
   construction: the box's Dirichlet boundary pushes the field toward its
   mean near the edges, so the box is padded to 1.3x the data's half-width.

Everything is fitted on the quadrature-collapsed design (the record x
quarter expansion averaged over each dating interval), which the structure
check showed matches the EM mixture to +0.0007 bits -- the dense HSGP block
would be prohibitive on the 56k-row expansion. Folds and seed match the
main ladder. Writes db/direction_rank.json (separate file: the main rerun
overwrites the primary JSON on completion).

    py -3.12 scripts/direction_hsgp.py
"""
from __future__ import annotations

import json
import sys
import time
from math import gamma, pi, sqrt
from pathlib import Path

import numpy as np
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direction_model as D                                        # noqa: E402
from direction_structure import collapse                           # noqa: E402

OUT = D.ROOT / "db" / "direction_rank.json"
K_FOLDS, SEED, N_BOOT = 10, 1, 1000
BOUNDARY = 1.3
RIDGES = (0.25, 1.0, 4.0)


# ------------------------------------------------------------------ HSGP basis
def spectral_density(omega, ls, nu, d):
    """Matern-nu spectral density in d dimensions, unit variance."""
    if nu is None:                                     # squared exponential
        return (2 * pi) ** (d / 2) * ls ** d * np.exp(-(ls ** 2) * omega ** 2 / 2)
    c = (2 ** d * pi ** (d / 2) * gamma(nu + d / 2) * (2 * nu) ** nu
         / (gamma(nu) * ls ** (2 * nu)))
    return c * (2 * nu / ls ** 2 + omega ** 2) ** (-(nu + d / 2))


def dim_basis(x, half, centre, m):
    """1-D Laplacian eigenfunctions on [-L, L] and their frequencies."""
    L = BOUNDARY * half
    j = np.arange(1, m + 1)
    freq = pi * j / (2 * L)
    phi = np.sqrt(1.0 / L) * np.sin(freq[None, :] * (x[:, None] - centre + L))
    return phi, freq


def hsgp_block(df, n_ord, ls, lt, nu=1.5):
    """Collapsed space-time HSGP design block and its per-column prior sds.

    Separable: spatial columns are products of x- and y-eigenfunctions
    weighted by the 2-D spectral density at the combined frequency; temporal
    eigenfunctions are averaged over each record's dating interval, which is
    the same interval quadrature the rest of the collapsed design uses.
    """
    x, y = df.px.values, df.py.values
    cx, cy = (x.max() + x.min()) / 2, (y.max() + y.min()) / 2
    hx, hy = (x.max() - x.min()) / 2, (y.max() - y.min()) / 2
    mx = min(14, int(np.ceil(1.75 * BOUNDARY * hx / ls)) + 1)
    my = min(14, int(np.ceil(1.75 * BOUNDARY * hy / ls)) + 1)
    phix, fx = dim_basis(x, hx, cx, mx)
    phiy, fy = dim_basis(y, hy, cy, my)

    t = np.arange(n_ord, dtype=float)
    ct, ht = (n_ord - 1) / 2, (n_ord - 1) / 2
    mt = min(8, int(np.ceil(1.75 * BOUNDARY * ht / lt)) + 1)
    psi_t, ft = dim_basis(t, ht, ct, mt)               # (n_ord, mt)
    qs, qe = df.qs.values, df.qe.values
    cum = np.vstack([np.zeros(mt), np.cumsum(psi_t, axis=0)])
    psi_bar = (cum[qe + 1] - cum[qs]) / (qe - qs + 1)[:, None]

    omega_s = np.sqrt((fx[:, None] ** 2 + fy[None, :] ** 2)).ravel()
    w_s = np.sqrt(spectral_density(omega_s, ls, nu, 2))
    w_t = np.sqrt(spectral_density(ft, lt, nu, 1))

    phi_s = np.einsum("ni,nj->nij", phix, phiy).reshape(len(df), mx * my)
    X = np.einsum("ns,nt->nst", phi_s * w_s[None, :],
                  psi_bar * w_t[None, :]).reshape(len(df), mx * my * mt)
    print(f"HSGP basis: {mx}x{my} spatial x {mt} temporal = {X.shape[1]} columns",
          flush=True)
    return X


# ------------------------------------------------------------------ fitting
def fit_predict(Xtr, ytr, Xte, P):
    beta, _ = D.irls(Xtr, ytr, np.ones(len(ytr)), P, np.zeros(Xtr.shape[1]),
                     iters=25)
    return 1.0 / (1.0 + np.exp(-np.clip(Xte @ beta, -30, 30)))


def build(df, n_ord, trads, places, spec, lam, extra=None, extra_ridge=None):
    """Collapsed design (+ optional dense extra block) and its penalty."""
    des = D.Design(df.reset_index(drop=True), n_ord, trads, places, spec)
    Xq = collapse(des, len(df))
    P = des.penalty(lam)
    if extra is not None:
        Xq = sp.hstack([Xq, sp.csr_matrix(extra)], format="csr")
        P2 = np.zeros((Xq.shape[1], Xq.shape[1]))
        P2[:P.shape[0], :P.shape[1]] = P
        P2[P.shape[0]:, P.shape[0]:] = extra_ridge * np.eye(extra.shape[1])
        P = P2
    return Xq, P


def main():
    t0 = time.time()
    df, quarters, *_ = D.load()
    df = df.reset_index(drop=True)
    n_ord = int(quarters.index.max()) + 1
    trads = sorted(df.trad.unique())
    places = sorted(df.place_id.unique())
    D.LANGS[:] = sorted(df.langgrp.unique())

    sel = json.loads((D.ROOT / "db" / "direction_model.json")
                     .read_text(encoding="utf-8"))["st_kernel"]
    ls, lt = sel["length_scale_km"], sel["length_scale_quarters"]
    D.ST.update(ls=ls, kernel=sel["kernel"], lt=lt)
    lam = dict(g=1.0, a=1.0, d=3.0, d0=1.0, u=1.0, k=sel["ridge"])
    nu = 1.5 if sel["kernel"] == "m32" else None

    H = hsgp_block(df, n_ord, ls, lt, nu)

    knots_sel = D.st_knots(df, n_ord, spacing=ls, t_every=int(lt))
    knots_dense = D.st_knots(df, n_ord, spacing=ls / 2, t_every=int(lt))
    print(f"knot bases: selected {len(knots_sel[0])}, dense {len(knots_dense[0])} "
          f"spatial knots (kernel {ls:.0f} km in both)  [{time.time()-t0:.0f}s]",
          flush=True)

    M1B, M5 = D.SPECS["M1b space-time kernel"], D.SPECS["M5 + findspot"]
    M5F = dict(M5, st=1)
    fold = D.blocked_folds(df, K_FOLDS, SEED)
    y = df.y.values

    def run(name, spec, knots=None, extra=None, ridge=None):
        if knots is not None:
            D.KNOTS["xy"], D.KNOTS["t"] = knots
        lam2 = dict(lam, k=ridge) if (ridge is not None and extra is None) else lam
        p = np.full(len(df), np.nan)
        for f in range(K_FOLDS):
            tr, te = fold != f, fold == f
            Xtr, P = build(df[tr], n_ord, trads, places, spec, lam2,
                           None if extra is None else extra[tr], ridge)
            Xte, _ = build(df[te], n_ord, trads, places, spec, lam2,
                           None if extra is None else extra[te], ridge)
            p[np.flatnonzero(te)] = fit_predict(Xtr, y[tr], Xte, P)
        m = D.metrics(y, p)
        print(f"  {name:<44} bits {m['log_loss_bits']:.4f}  F1 {m['f1']:.3f}"
              f"   [{time.time()-t0:.0f}s]", flush=True)
        return p, m

    results = {"kernel": sel, "boundary_factor": BOUNDARY,
               "hsgp_columns": int(H.shape[1]),
               "dense_knots": int(len(knots_dense[0])), "models": {}}
    preds = {}

    # ridge sweeps on the field-alone rungs, then the winners everywhere
    best = {}
    for tag, kn, ex in [("knot", knots_sel, None),
                        ("knot-dense", knots_dense, None),
                        ("hsgp", None, H)]:
        scores = {}
        for r in RIDGES:
            spec = M1B if ex is None else D.SPECS["M0 intercept"]
            p, m = run(f"{tag} field alone, ridge {r:g}", spec, kn, ex, r)
            scores[r] = (m["log_loss_bits"], p, m)
        r_best = min(scores, key=lambda r: scores[r][0])
        best[tag] = r_best
        preds[f"{tag} field alone"] = scores[r_best][1]
        results["models"][f"{tag} field alone"] = {
            "ridge": r_best, "metrics": scores[r_best][2]}

    p, m = run("M5 (no field)", M5)
    preds["M5"] = p
    results["models"]["M5 (no field)"] = {"metrics": m}
    for tag, kn, ex in [("knot", knots_sel, None),
                        ("knot-dense", knots_dense, None),
                        ("hsgp", None, H)]:
        spec = M5F if ex is None else M5
        lam["k"] = best[tag]
        p, m = run(f"M5 + {tag} field", spec, kn, ex,
                   best[tag] if ex is not None else None)
        preds[f"M5 + {tag} field"] = p
        results["models"][f"M5 + {tag} field"] = {
            "ridge": best[tag], "metrics": m}

    # paired deltas vs M5, clustered on findspot
    def bits(p):
        q = np.clip(p, 1e-12, 1 - 1e-12)
        return -(y * np.log2(q) + (1 - y) * np.log2(1 - q))

    rng = np.random.default_rng(41)
    groups = df.groupby("place_id").indices
    keys = list(groups)
    results["paired_vs_M5"] = {}
    for name in ["M5 + knot field", "M5 + knot-dense field", "M5 + hsgp field"]:
        d0 = bits(preds[name]) - bits(preds["M5"])
        deltas = []
        for _ in range(N_BOOT):
            pick = rng.integers(0, len(keys), len(keys))
            idx = np.concatenate([groups[keys[j]] for j in pick])
            deltas.append(float(d0[idx].mean()))
        lo, hi = np.percentile(deltas, [2.5, 97.5])
        results["paired_vs_M5"][name] = {
            "delta_bits": round(float(d0.mean()), 4),
            "lo": round(float(lo), 4), "hi": round(float(hi), 4)}
        print(f"{name:<28} vs M5: {d0.mean():+.4f} [{lo:+.4f}, {hi:+.4f}]",
              flush=True)

    OUT.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"wrote {OUT}  [{time.time()-t0:.0f}s]")


if __name__ == "__main__":
    sys.exit(main())
