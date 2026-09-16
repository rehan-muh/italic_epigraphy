#!/usr/bin/env python3
"""
Evaluate the fitted space-time field continuously over Italy.

The field is a low-rank Gaussian process, so it is already a continuous
surface; the published figure had only sampled it at the kernel knots. This
refits M7 (the full model) and M1b (the field alone) with the kernel the main
run selected, evaluates both fields on a fine grid, and merges the rasters
into db/direction_model.json under "field_grid".

The grid is masked to within MASK_KM of a findspot: past that the surface is
extrapolation the ridge pulls to zero, and drawing it would paint certainty
where there is no data. The mask also traces the coastline for free.

    py -3.12 scripts/direction_field_grid.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direction_model as D                                        # noqa: E402

OUT = D.ROOT / "db" / "direction_model.json"
CELL_KM = 18.0
MASK_KM = 55.0                     # half the kernel length scale
YEARS = (-700, -550, -400, -250, -100)


def field_raster(des, beta, quarters, grid, si, sw):
    """The field component at the grid points, one slice per year, log-odds."""
    KT = D.KNOTS["t"]
    s = des.slices["k"]
    b = beta[s].reshape(len(D.KNOTS["xy"]), len(KT))
    edges = quarters.values.astype(float)
    out = []
    for year in YEARS:
        o = float(np.argmin(np.abs(edges - year)))
        ti, tw = D.st_weights(np.array([o]), KT, D.ST["lt"])
        knot_val = (b[:, ti[0]] * tw[0][None, :]).sum(1)      # (n_knots,)
        v = (knot_val[si] * sw).sum(1)                         # (n_grid,)
        out.append({"year": int(year),
                    "logodds": [round(float(x), 3) for x in v]})
    return out


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
    print(f"kernel {sel['kernel']} at {sel['length_scale_km']:.0f} km, "
          f"ridge {sel['ridge']}; grid {CELL_KM:.0f} km, mask {MASK_KM:.0f} km",
          flush=True)

    # the grid, masked to data support
    pts = np.unique(df[["px", "py"]].values, axis=0)
    lo, hi = pts.min(0) - MASK_KM, pts.max(0) + MASK_KM
    gx = np.arange(lo[0], hi[0], CELL_KM)
    gy = np.arange(lo[1], hi[1], CELL_KM)
    G = np.array([(x, y) for x in gx for y in gy])
    from scipy.spatial import cKDTree
    near = cKDTree(pts).query(G, k=1)[0]
    G = G[near <= MASK_KM]
    print(f"{len(G)} grid cells inside the mask", flush=True)
    si, sw = D.st_weights(G, D.KNOTS["xy"], D.ST["ls"])

    rasters = {}
    for tag, spec in [("alone", "M1b space-time kernel"),
                      ("full", "M7 + space-time kernel")]:
        print(f"fitting {spec}…", flush=True)
        des = D.Design(df, n_ord, trads, places, D.SPECS[spec])
        beta, ll, _, _ = D.em_fit(des, lam, inner=25)
        rasters[tag] = field_raster(des, beta, quarters, G, si, sw)
        print(f"  done [{time.time()-t0:.0f}s]", flush=True)

    doc = json.loads(OUT.read_text(encoding="utf-8"))
    doc["field_grid"] = {
        "cell_km": CELL_KM, "mask_km": MASK_KM,
        "x": [round(float(v), 1) for v in G[:, 0]],
        "y": [round(float(v), 1) for v in G[:, 1]],
        "alone": rasters["alone"], "full": rasters["full"]}
    OUT.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"merged field_grid into {OUT}  [{time.time()-t0:.0f}s]")


if __name__ == "__main__":
    sys.exit(main())
