#!/usr/bin/env python3
"""
Two structural questions about the model, answered by held-out comparison.

1. The global time curve is redundant in principle: it lies in the span of a
   common shift of the seventeen per-alphabet curves, so with both present the
   split between them is decided by the penalties, not the data. Here M5 is
   refitted WITHOUT the global curve; if the paired held-out difference is
   zero, the term can go and time appears in the final model exactly twice —
   once per alphabet (a script changing its own practice), once in the
   space-time kernel (a region changing) — each carrying a distinct
   hypothesis, which is the reason both remain.

2. Quadrature inside the kernel. A record dated to an interval does not give
   the kernel a time point; the honest evaluation integrates the temporal
   basis over the interval. With quarter-century steps that integral is the
   exact discrete quadrature  w̄_j(i) = (1/|span_i|) Σ_{t∈span_i} w_j(t),
   applied to every time-dependent block (global curve, per-alphabet curves,
   the kernel's temporal weights). That collapses the (record, quarter)
   expansion to one row per record and needs no EM. It is not the same
   likelihood: quadrature-in-the-design averages eta before the logistic
   (Jensen gap), while the EM mixture averages the likelihood over the
   latent quarter — the model already in use, whose E-step sharpens the
   uniform prior. This script fits the quadrature version and lets CV say
   whether the mixture buys anything.

    py -3.12 scripts/direction_structure.py

Merges "structure_checks" into db/direction_model.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direction_model as D                                        # noqa: E402

OUT = D.ROOT / "db" / "direction_model.json"
K_FOLDS, SEED, N_BOOT = 10, 1, 1000

M5 = D.SPECS["M5 + findspot"]
M5_NO_G = dict(M5, time=0)


def collapse(des, n_rec):
    """Average the expanded design over each record's dating interval.

    S has one row per record with weights 1/|span|, so S @ X applies the
    interval quadrature to every column at once — including the kernel's
    temporal weights, which is where 'quadrature inside the kernel' lives.
    """
    n_rows = des.X.shape[0]
    S = sp.csr_matrix((des.base, (des.rec, np.arange(n_rows))),
                      shape=(n_rec, n_rows))
    return S @ des.X


def fit_quad(df, n_ord, trads, places, spec, lam, train_idx):
    des = D.Design(df.iloc[train_idx].reset_index(drop=True),
                   n_ord, trads, places, spec)
    Xq = collapse(des, len(train_idx))
    y = df.y.values[train_idx]
    beta, _ = D.irls(Xq, y, np.ones(len(y)), des.penalty(lam),
                     np.zeros(des.p), iters=25)
    return beta


def predict_quad(df, n_ord, trads, places, spec, beta, test_idx):
    des = D.Design(df.iloc[test_idx].reset_index(drop=True),
                   n_ord, trads, places, spec)
    Xq = collapse(des, len(test_idx))
    return 1.0 / (1.0 + np.exp(-np.clip(Xq @ beta, -30, 30)))


def main():
    t0 = time.time()
    df, quarters, *_ = D.load()
    n_ord = int(quarters.index.max()) + 1
    trads = sorted(df.trad.unique())
    places = sorted(df.place_id.unique())
    D.LANGS[:] = sorted(df.langgrp.unique())

    sel = json.loads(OUT.read_text(encoding="utf-8"))["st_kernel"]
    D.ST.update(ls=sel["length_scale_km"], kernel=sel["kernel"],
                lt=sel["length_scale_quarters"])
    D.KNOTS["xy"], D.KNOTS["t"] = D.st_knots(
        df, n_ord, spacing=sel["length_scale_km"],
        t_every=int(sel["length_scale_quarters"]))
    lam = dict(g=1.0, a=1.0, d=3.0, d0=1.0, u=1.0, k=sel["ridge"])

    fold = D.blocked_folds(df, K_FOLDS, SEED)
    preds = {"M5 (EM mixture, global curve)": np.full(len(df), np.nan),
             "M5 without the global curve": np.full(len(df), np.nan),
             "M5 quadrature-in-kernel, no EM": np.full(len(df), np.nan)}

    for f in range(K_FOLDS):
        tr, te = np.flatnonzero(fold != f), np.flatnonzero(fold == f)
        for name, spec, quad in [
                ("M5 (EM mixture, global curve)", M5, False),
                ("M5 without the global curve", M5_NO_G, False),
                ("M5 quadrature-in-kernel, no EM", M5, True)]:
            if quad:
                beta = fit_quad(df, n_ord, trads, places, spec, lam, tr)
                preds[name][te] = predict_quad(df, n_ord, trads, places,
                                               spec, beta, te)
            else:
                d_tr = D.Design(df.iloc[tr].reset_index(drop=True),
                                n_ord, trads, places, spec)
                beta, _, _, _ = D.em_fit(d_tr, lam)
                d_te = D.Design(df.iloc[te].reset_index(drop=True),
                                n_ord, trads, places, spec)
                mu = 1.0 / (1.0 + np.exp(-np.clip(d_te.X @ beta, -30, 30)))
                preds[name][te] = np.bincount(d_te.rec, weights=d_te.base * mu,
                                              minlength=len(te))
        print(f"fold {f+1}/{K_FOLDS}  [{time.time()-t0:.0f}s]", flush=True)

    y = df.y.values

    def bits(p):
        q = np.clip(p, 1e-12, 1 - 1e-12)
        return -(y * np.log2(q) + (1 - y) * np.log2(1 - q))

    ref = "M5 (EM mixture, global curve)"
    rng = np.random.default_rng(31)
    groups = df.groupby("place_id").indices
    keys = list(groups)
    out = {"reference": ref,
           "metrics": {n: D.metrics(y, p) for n, p in preds.items()},
           "paired_vs_reference": {}}
    for name, p in preds.items():
        if name == ref:
            continue
        d0 = bits(p) - bits(preds[ref])
        deltas = []
        for _ in range(N_BOOT):
            pick = rng.integers(0, len(keys), len(keys))
            idx = np.concatenate([groups[keys[j]] for j in pick])
            deltas.append(float(d0[idx].mean()))
        lo, hi = np.percentile(deltas, [2.5, 97.5])
        out["paired_vs_reference"][name] = {
            "delta_bits": round(float(d0.mean()), 4),
            "lo": round(float(lo), 4), "hi": round(float(hi), 4)}
        print(f"{name:<34} {d0.mean():+.4f} [{lo:+.4f}, {hi:+.4f}]", flush=True)

    doc = json.loads(OUT.read_text(encoding="utf-8"))
    doc["structure_checks"] = out
    OUT.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"merged structure_checks into {OUT}  [{time.time()-t0:.0f}s]")


if __name__ == "__main__":
    sys.exit(main())
