#!/usr/bin/env python3
"""
Figure for the Bayesian kernel-hyperparameter inference:
lengthscale_posterior_multinomial.png
  (a) marginal posterior over the spatial length scale, script + language
  (b) marginal posterior over the temporal length scale
  (c) sensitivity: centred beta_E across the grid nodes (script model),
      marker area ~ node posterior weight

    python scripts/adoption_lengthscale_figures.py   (matplotlib -> py 3.14)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from adoption_scripts_figures import (GRID, INK, MUTED, NICE,  # noqa: E402
                                      SCRIPT_COLOR, SURF, style)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "report" / "adoption"
JS = json.loads((ROOT / "db" / "adoption_lengthscale.json").read_text())
JL = json.loads((ROOT / "db" / "adoption_lengthscale_lang.json").read_text())

SHOW = ["latin", "greek", "etruscan", "oscan"]
DASH = (0, (4, 2.5))


def prior_pdf(x, med, sig):          # density of log-x, plotted vs x
    u = np.log(x)
    return np.exp(-0.5 * ((u - math.log(med)) / sig) ** 2) \
        / (sig * math.sqrt(2 * math.pi))


def marg_panel(ax, key, xlab, med, sig, scale=1.0):
    xs = None
    for res, ls_style, lab in [(JS, "-", "script"), (JL, DASH, "language")]:
        m = res["posterior"][key]
        xv = np.array([float(k) for k in m]) * scale
        wv = np.array(list(m.values()))
        order = np.argsort(xv)
        xv, wv = xv[order], wv[order]
        xs = xv
        ax.plot(xv, wv, color=INK, ls=ls_style, lw=2, marker="o", ms=5,
                mfc=SURF, mew=1.4, mec=INK)
    gx = np.geomspace(xs.min() / 1.4, xs.max() * 1.4, 200)
    pr = prior_pdf(gx, med, sig)
    # scale the prior curve to the same height as the posteriors
    top = max(max(JS["posterior"][key].values()),
              max(JL["posterior"][key].values()))
    ax.fill_between(gx, pr / pr.max() * top * 0.85, color="#c9c7bd",
                    alpha=0.45, lw=0, zorder=0)
    ax.set_xscale("log")
    ax.set_xticks(xs, [f"{v:g}" for v in xs])
    ax.minorticks_off()
    ax.set_xlabel(xlab, fontsize=9, color=MUTED)
    style(ax, keep_grid=True)


def main():
    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(11.6, 3.7), facecolor=SURF,
        gridspec_kw={"width_ratios": [1, 1, 1.35]})

    marg_panel(ax1, "marginal_ls_km", "spatial length scale (km)",
               70.0, 0.45)
    ax1.set_ylabel("posterior mass", fontsize=9, color=MUTED)
    ax1.set_title("Spatial length scale", fontsize=10, color=INK)
    marg_panel(ax2, "marginal_lt_yr", "temporal length scale (years)",
               100.0, 0.50)
    ax2.set_title("Temporal length scale", fontsize=10, color=INK)
    hs = [Line2D([], [], color=INK, lw=2, label="script"),
          Line2D([], [], color=INK, lw=2, ls=DASH, label="language"),
          plt.Rectangle((0, 0), 1, 1, fc="#c9c7bd", alpha=0.45,
                        label="prior")]
    ax1.legend(handles=hs, frameon=False, fontsize=8)

    # (c) sensitivity of beta_E across nodes, script model
    nodes = JS["nodes"]
    w = np.array([nd["posterior_weight"] for nd in nodes])
    ls_vals = sorted({nd["ls_km"] for nd in nodes})
    rng = np.random.default_rng(3)
    for nm in SHOW:
        col = SCRIPT_COLOR.get(nm, INK)
        xj = []
        for nd in nodes:
            i = ls_vals.index(nd["ls_km"])
            xj.append(i + 0.14 * (math.log(nd["lt_q"]) / math.log(8) - 0.5))
        yv = [nd["beta_E_centred"][nm] for nd in nodes]
        ax3.scatter(xj, yv, s=8 + 320 * w, color=col, alpha=0.75, lw=0,
                    zorder=3)
        # posterior-weighted mean as a line across ls
        for i, lsv in enumerate(ls_vals):
            sub = [(nd, wi) for nd, wi in zip(nodes, w)
                   if nd["ls_km"] == lsv]
            ww = sum(x[1] for x in sub)
            if ww > 0:
                mval = sum(x[0]["beta_E_centred"][nm] * x[1]
                           for x in sub) / ww
                ax3.plot([i - 0.22, i + 0.22], [mval, mval], color=col,
                         lw=2.4, solid_capstyle="butt", zorder=4)
    ax3.axhline(0, color="#c3c2b7", lw=1)
    ax3.set_xticks(range(len(ls_vals)), [f"{v:g}" for v in ls_vals])
    ax3.set_xlabel("spatial length scale (km)", fontsize=9, color=MUTED)
    ax3.set_ylabel(r"centred $\beta_E$", fontsize=9, color=MUTED)
    ax3.set_title("Coefficient stability across the grid",
                  fontsize=10, color=INK)
    style(ax3, keep_grid=True)
    hs3 = [Line2D([], [], color=SCRIPT_COLOR.get(nm, INK), lw=2.4,
                  label=NICE.get(nm, nm)) for nm in SHOW]
    ax3.legend(handles=hs3, frameon=False, fontsize=8, ncols=2,
               loc="lower right")

    fig.suptitle("Kernel hyperparameters: posterior and sensitivity",
                 fontsize=11, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "lengthscale_posterior_multinomial.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)
    print(f"wrote lengthscale_posterior_multinomial.png to {OUT}")


if __name__ == "__main__":
    main()
