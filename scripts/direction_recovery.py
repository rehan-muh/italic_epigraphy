#!/usr/bin/env python3
"""
Does the aoristic EM recover a known trajectory from interval-dated data?

Simulates a corpus with the real corpus's shape — same number of records per
tradition, same distribution of dating-interval widths, same findspot sizes —
under a known P(dextroverse) curve, then compares three estimators:

  aoristic EM   the model in direction_model.py
  midpoint      each record assigned to the quarter its interval midpoint falls in
  narrow only   records with intervals of 100 years or less, binned on midpoint

Reported as RMSE against the true curve, per tradition, over the quarters where
the tradition is attested. This is the check behind the claim that the interval
handling is not cosmetic.

    py -3.12 scripts/direction_recovery.py --reps 40
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direction_model as M

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "db" / "direction_recovery.json"


def truth_curves(n_ord, trads, rng):
    """Plausible but arbitrary logistic-in-time curves, one per tradition.

    Shapes deliberately include a monotone fall, a monotone rise, a late
    reversal and a flat norm, so no estimator is favoured by construction.
    """
    t = np.linspace(0, 1, n_ord)
    shapes = {
        "fall":     -2.0 + -3.0 * t,
        "rise":      0.5 + 4.0 * t,
        "reversal": -1.0 - 4.0 * t + 9.0 * t ** 2,
        "flat":      3.0 + 0.0 * t,
        "slowfall":  0.2 - 2.5 * t,
    }
    keys = list(shapes)
    return {tr: shapes[keys[i % len(keys)]] for i, tr in enumerate(sorted(trads))}


def simulate(df, n_ord, curves, rng):
    """Keep the real spans, findspots and traditions; regenerate y."""
    sim = df.copy()
    true_q = np.array([rng.integers(a, b + 1) for a, b in zip(sim.qs.values, sim.qe.values)])
    place_eff = {p: rng.normal(0, 0.6) for p in sim.place_id.unique()}
    eta = np.array([curves[tr][q] for tr, q in zip(sim.trad.values, true_q)])
    eta = eta + sim.place_id.map(place_eff).values
    p = 1.0 / (1.0 + np.exp(-eta))
    sim["y"] = (rng.random(len(sim)) < p).astype(float)
    sim["true_q"] = true_q
    return sim


def binned_estimate(sim, n_ord, trads, mask=None):
    s = sim if mask is None else sim[mask]
    mid = ((s.qs + s.qe) // 2).values
    out = {}
    for tr in trads:
        m = (s.trad.values == tr)
        num = np.bincount(mid[m], weights=s.y.values[m], minlength=n_ord)
        den = np.bincount(mid[m], minlength=n_ord).astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[tr] = np.where(den > 0, num / np.maximum(den, 1e-9), np.nan)
        out[tr + "__n"] = den
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=40)
    args = ap.parse_args()

    df, quarters, _, _ = M.load()
    n_ord = int(quarters.index.max()) + 1
    trads = sorted(df.trad.unique())
    places = sorted(df.place_id.unique())
    lam = dict(g=1.0, a=1.0, d=3.0, d0=1.0, u=3.0)
    rng = np.random.default_rng(11)
    curves = truth_curves(n_ord, trads, rng)
    truth_p = {tr: 1.0 / (1.0 + np.exp(-curves[tr])) for tr in trads}

    err = {k: {tr: [] for tr in trads} for k in ("aoristic EM", "midpoint", "narrow only")}
    for rep in range(args.reps):
        sim = simulate(df, n_ord, curves, rng)
        des = M.Design(sim, n_ord, trads, places, M.SPECS["M4 + findspot"])
        beta, _, _, w = M.em_fit(des, lam, inner=4)
        em = M.population_p(des, sim, beta, w)
        mid = binned_estimate(sim, n_ord, trads)
        nar = binned_estimate(sim, n_ord, trads, mask=(sim.qe - sim.qs + 1) <= 4)

        for tr in trads:
            support = mid[tr + "__n"] >= 5           # judge only where data exists
            if support.sum() < 3:
                continue
            for name, est in (("aoristic EM", em[tr]), ("midpoint", mid[tr]),
                              ("narrow only", nar[tr])):
                e = np.asarray(est, float)[support] - truth_p[tr][support]
                e = e[np.isfinite(e)]
                if len(e):
                    err[name][tr].append(float(np.sqrt((e ** 2).mean())))
        print(f"  rep {rep+1}/{args.reps}", flush=True)

    summary = {}
    for name in err:
        per = {tr: round(float(np.mean(v)), 4) for tr, v in err[name].items() if v}
        allv = [x for v in err[name].values() for x in v]
        summary[name] = {"per_tradition_rmse": per,
                         "overall_rmse": round(float(np.mean(allv)), 4)}
        print(f"{name:<14} overall RMSE {summary[name]['overall_rmse']:.4f}   {per}")

    OUT.write_text(json.dumps({"reps": args.reps, "summary": summary}, indent=1),
                   encoding="utf-8")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
