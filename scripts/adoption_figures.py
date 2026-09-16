#!/usr/bin/env python3
"""
Figures for the adoption-vs-economy correlated probit (adoption_probit.py).

Reads db/adoption_probit.json and db/adoption_probit_grid.npz, writes PNGs
to report/adoption_probit/.

Encoding conventions (kept CVD-safe): the *tradition* carries the hue
(Greek = blue, Latin = orange); alphabet vs language is carried by fill /
line style, never by a third hue. Probability maps are single-hue
sequential ramps; the economy field is a blue-gray-red diverging ramp
centred on 0 (E is a standardised anomaly-like scale).

    python scripts/adoption_figures.py     (needs matplotlib -> py 3.14)
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "report" / "adoption_probit"
OUT.mkdir(parents=True, exist_ok=True)

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, MUTED, GRID = "#0b0b0b", "#898781", "#e1e0d9"
SURF = "#fcfcfb"
CMAP_GREEK = LinearSegmentedColormap.from_list(
    "greekseq", ["#f2f6fc", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
CMAP_LATIN = LinearSegmentedColormap.from_list(
    "latinseq", ["#fcf5f1", "#fde3d7", "#f5a983", "#eb6834", "#b84515", "#6e2a0c"])
CMAP_E = LinearSegmentedColormap.from_list(
    "ediv", ["#104281", "#3987e5", "#b7d3f6", "#f0efec",
             "#f2b3b2", "#e34948", "#8c1d1c"])

OUTCOMES = ["greek_alph", "latin_alph", "greek_lang", "latin_lang"]
NICE = {"greek_alph": "Greek alphabet", "latin_alph": "Latin alphabet",
        "greek_lang": "Greek language", "latin_lang": "Latin language"}
HUE = {"greek_alph": BLUE, "greek_lang": BLUE,
       "latin_alph": ORANGE, "latin_lang": ORANGE}


def style(ax):
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(True, color=GRID, lw=0.6, alpha=0.8)
    ax.set_axisbelow(True)


def main():
    res = json.loads((ROOT / "db" / "adoption_probit.json").read_text())
    g = np.load(ROOT / "db" / "adoption_probit_grid.npz")

    # ---- 1. maps: E field vs adoption surfaces at three moments ---------
    times = [-500, -300, -100]
    fig, axes = plt.subplots(len(times), 3, figsize=(10.5, 11.5),
                             facecolor=SURF, constrained_layout=True)
    for r, t in enumerate(times):
        m = g["bin_start"] == t
        panels = [("E_mean", CMAP_E, (-2.2, 2.2), "economic field E"),
                  ("p_greek_alph", CMAP_GREEK, (0, 0.8), "P(Greek alphabet)"),
                  ("p_latin_alph", CMAP_LATIN, (0, 1.0), "P(Latin alphabet)")]
        for c, (key, cmap, (v0, v1), label) in enumerate(panels):
            ax = axes[r, c]
            sc = ax.scatter(g["lon"][m], g["lat"][m], c=g[key][m], cmap=cmap,
                            vmin=v0, vmax=v1, s=7, marker="s", lw=0)
            ax.set_aspect(1.34)
            ax.set_xlim(6.4, 19.1); ax.set_ylim(36.4, 47.2)
            style(ax); ax.grid(False)
            if r == 0:
                ax.set_title(label, fontsize=10, color=INK)
            if c == 0:
                ax.set_ylabel(f"{-t} BCE", fontsize=11, color=INK)
            cb = fig.colorbar(sc, ax=ax, shrink=0.75, pad=0.02)
            cb.ax.tick_params(labelsize=7, colors=MUTED)
            cb.outline.set_visible(False)
    fig.suptitle("Latent economic field and model-implied adoption surfaces\n"
                 "(cells within 75 km of a findspot; adoption = share of "
                 "epigraphy in the tradition)", fontsize=11, color=INK)
    fig.savefig(OUT / "maps_adoption_vs_economy.png", dpi=180)
    plt.close(fig)

    # ---- 2. beta_E forest ------------------------------------------------
    fig, ax = plt.subplots(figsize=(7.2, 4.2), facecolor=SURF)
    style(ax)
    ypos, ylab = [], []
    for i, k in enumerate(OUTCOMES):
        for j, (tag, dy, filled) in enumerate(
                [("trend_E", 0.16, False), ("trend_E_field", -0.16, True)]):
            mrow = res["models"][k][tag]
            y = -(i + dy)
            b, (lo, hi) = mrow["beta_E"], mrow["ci95"]
            col = HUE[k]
            ax.plot([lo, hi], [y, y], color=col, lw=2, solid_capstyle="round")
            ax.plot(b, y, "o", ms=8, mfc=col if filled else SURF,
                    mec=col, mew=1.6)
        ypos.append(-i); ylab.append(NICE[k])
    ax.axvline(0, color="#c3c2b7", lw=1)
    ax.set_yticks(ypos, ylab, fontsize=10, color=INK)
    ax.set_xlabel("probit slope on E  (per 1 sd of economic field)",
                  fontsize=9, color=MUTED)
    handles = [Line2D([], [], marker="o", ls="", mfc=SURF, mec=INK,
                      label="time trend + E"),
               Line2D([], [], marker="o", ls="", mfc=INK, mec=INK,
                      label="+ spatiotemporal field (conservative)")]
    ax.set_xlim(-0.8, 2.6)
    ax.legend(handles=handles, frameon=False, fontsize=8, loc="upper right")
    ax.set_title("Economic activity and adoption: beta with 95% CI\n"
                 "(findspot-clustered SE, inflated for posterior "
                 "uncertainty in E)", fontsize=10, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "beta_forest.png", dpi=180)
    plt.close(fig)

    # ---- 3. per-century correlation of adoption surface with E ----------
    fig, ax = plt.subplots(figsize=(7.2, 4.0), facecolor=SURF)
    style(ax)
    corr = res["grid"]["adoption_vs_E_correlation"]
    for k in OUTCOMES:
        per = corr[k]["by_century"]
        cents = sorted(int(c) for c in per)
        ax.plot([c + 50 for c in cents], [per[str(c)] for c in cents],
                color=HUE[k], lw=2,
                ls="-" if k.endswith("alph") else (0, (4, 2.5)),
                marker="o", ms=4.5, mfc=SURF, mew=1.4, mec=HUE[k])
    ax.axhline(0, color="#c3c2b7", lw=1)
    ax.set_xlabel("century (BCE)", fontsize=9, color=MUTED)
    ax.set_ylabel("Pearson r, adoption surface vs E", fontsize=9, color=MUTED)
    ax.set_xticks(range(-700, 0, 100),
                  [f"{-c}s" for c in range(-700, 0, 100)])
    handles = [Line2D([], [], color=BLUE, lw=2, label="Greek"),
               Line2D([], [], color=ORANGE, lw=2, label="Latin"),
               Line2D([], [], color=INK, lw=2, label="alphabet"),
               Line2D([], [], color=INK, lw=2, ls=(0, (4, 2.5)),
                      label="language")]
    ax.legend(handles=handles, frameon=False, fontsize=8, ncols=2)
    ax.set_title("Where does adoption line up with the economy, by century?",
                 fontsize=10, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "correlation_by_century.png", dpi=180)
    plt.close(fig)
    print(f"wrote 3 figures to {OUT}")


if __name__ == "__main__":
    main()
