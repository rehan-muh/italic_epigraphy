#!/usr/bin/env python3
"""
Did proximity to Latin writing reorient Etruscan writing?

The handbooks' mechanism, stated as a coefficient. Among inscriptions written
in an Etruscan alphabet, is the probability of dextroverse writing higher at
findspots near Latin epigraphy? If Latin influence turned Etruscan writing
around, distance to the nearest Latin epigraphy should carry a negative
coefficient here.

The specification matters. The main model carries a random effect for every
findspot, which is right when the question is whether places differ, and wrong
here: the distances are constant within a findspot, so a free effect per
findspot competes with them for the same between-place variation. On the full
corpus that is merely inefficient; on this subset it is unstable enough that
bootstrap draws of the coefficient run to 10^7. Findspot effects are therefore
dropped, and the clustering they were absorbing is handled by bootstrapping
whole findspots instead.

    py -3.12 scripts/direction_contact.py [--boot 200]

Writes its result into db/direction_model.json under "contact_test".
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direction_model as D                                        # noqa: E402

OUT = D.ROOT / "db" / "direction_model.json"
# Etruscan alphabets only, so language is constant and drops out; findspot
# effects are out for the reason in the docstring. The per-alphabet time curves
# are out too: 612 parameters against 253 dextroverse records let a resample
# separate the outcome, which is what sent the earlier draws to 10^174. Date is
# still controlled, by the shared time curve.
# time=1 is deliberate here although the main model dropped its global curve:
# this spec has no per-alphabet curves, so the shared curve is the only date
# control and is not redundant in it.
SPEC = dict(time=1, lang=0, trad=1, trad_time=0, place=0, geo=1)
# x: a ridge on the distances. They are collinear on this subset (coast against
# frontier, r = -0.9), so without it individual coefficients are unstable under
# resampling even though the fitted rates are not.
LAM = dict(g=1.0, a=1.0, d=3.0, d0=1.0, u=1.0, k=4.0, x=2.0)


def fitted_rate(des, beta, weights, col, value, observed):
    """Population-averaged P(dextroverse) with one covariate set to `value`.

    The linear predictor is linear in the covariate, so the counterfactual is
    an offset on eta rather than a rebuild of the design.
    """
    eta = des.X @ beta + beta[col] * (value - observed[des.rec])
    mu = 1.0 / (1.0 + np.exp(-np.clip(eta, -30, 30)))
    return float((weights * mu).sum() / weights.sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=200)
    args = ap.parse_args()
    t0 = time.time()

    df, quarters, *_ = D.load()
    n_ord = int(quarters.index.max()) + 1
    trads = sorted(df.trad.unique())
    places = sorted(df.place_id.unique())
    D.LANGS[:] = sorted(df.langgrp.unique())

    etr = D.etruscan_alphabets(df)
    d_lat = etr.d_latin.values
    q10, q90 = float(np.percentile(d_lat, 10)), float(np.percentile(d_lat, 90))
    print(f"{len(etr)} Etruscan-alphabet records, {etr.place_id.nunique()} findspots; "
          f"d_latin 10th/90th percentile {np.expm1(q10):.1f}/{np.expm1(q90):.0f} km",
          flush=True)

    des = D.Design(etr, n_ord, trads, places, SPEC)
    beta, ll, _, w = D.em_fit(des, LAM, inner=25)
    gs = des.slices["x"]
    j = D.GEO_COLS.index("d_latin")
    near = fitted_rate(des, beta, w, gs.start + j, q10, d_lat)
    far = fitted_rate(des, beta, w, gs.start + j, q90, d_lat)
    print(f"fit [{time.time()-t0:.0f}s]  d_latin beta {beta[gs][j]:+.4f}  "
          f"near {near:.4f}  far {far:.4f}", flush=True)

    rng = np.random.default_rng(11)
    groups = etr.groupby("place_id").indices
    keys = list(groups)
    coefs, gaps, diverged = [], [], 0
    for b in range(args.boot):
        pick = rng.integers(0, len(keys), len(keys))
        bs = etr.iloc[np.concatenate([groups[keys[k]] for k in pick])].reset_index(drop=True)
        try:
            db = D.Design(bs, n_ord, trads, places, SPEC)
            bb, _, _, wb = D.em_fit(db, LAM, beta=beta.copy(), max_em=25)
            dl = bs.d_latin.values
            gs_b = db.slices["x"]
            if np.abs(bb[gs_b]).max() > 20:      # separated draw, not an estimate
                diverged += 1
                continue
            coefs.append(float(bb[gs_b][j]))
            gaps.append(fitted_rate(db, bb, wb, gs_b.start + j, q10, dl)
                        - fitted_rate(db, bb, wb, gs_b.start + j, q90, dl))
        except Exception as exc:                                   # pragma: no cover
            print(f"   draw {b} failed: {exc}", flush=True)
        if (b + 1) % 25 == 0:
            print(f"   {b+1}/{args.boot}  [{time.time()-t0:.0f}s]", flush=True)

    c = np.array(coefs, float)
    g = np.array(gaps, float)
    res = {
        "n": int(len(etr)),
        "n_findspots": int(etr.place_id.nunique()),
        "n_draws": int(len(c)),
        "n_diverged": int(diverged),
        "spec": "time curve + alphabet + geography, no findspot effects, damped IRLS",
        "d_latin": {
            "beta": round(float(beta[gs][j]), 4),
            "odds_ratio": round(float(np.exp(beta[gs][j])), 4),
            "or_lo": round(float(np.exp(np.percentile(c, 2.5))), 4),
            "or_hi": round(float(np.exp(np.percentile(c, 97.5))), 4),
        },
        "fitted_rate": {
            "km_near": round(float(np.expm1(q10)), 1),
            "km_far": round(float(np.expm1(q90)), 1),
            "p_near": round(near, 4),
            "p_far": round(far, 4),
            "gap_lo": round(float(np.percentile(g, 2.5)), 4),
            "gap_hi": round(float(np.percentile(g, 97.5)), 4),
        },
        "other_coefficients": {c2: round(float(beta[gs][k]), 4)
                               for k, c2 in enumerate(D.GEO_COLS) if c2 != "d_latin"},
    }

    doc = json.loads(OUT.read_text(encoding="utf-8"))
    doc["contact_test"] = res
    OUT.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))
    print(f"\nmerged into {OUT}  [{time.time()-t0:.0f}s]")


if __name__ == "__main__":
    sys.exit(main())
