#!/usr/bin/env python3
"""
Figure suite for the all-scripts adoption probit (adoption_scripts.py).

Reads db/adoption_scripts.json + db/adoption_scripts_grid.npz and writes to
report/adoption_probit/:

    beta_forest_all_scripts.png   beta_E per tradition, both model variants
    surfaces_all_scripts.png      small multiples: P(script) maps, 10 x 4
    dominant_script_map.png       most-likely tradition per cell, 6 slices
    trajectories_all_scripts.png  national mean adoption curves, small mult.
    hovmoller_all_scripts.png     latitude x time Hovmoller per script
    latin_etruscan_frontier.png   probit difference maps (the moving frontier)

Encoding: probability surfaces share one sequential blue ramp (identity is
the panel label); the categorical dominant-script map uses the atlas's own
language colours (codebook.yml) so it matches the site; difference maps are
a two-pole diverging ramp with a neutral grey midpoint. One-vs-rest fits:
p_k do not sum to 1 across scripts; the dominant map takes the argmax.

    python scripts/adoption_scripts_figures.py     (matplotlib -> py 3.14)
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, ListedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "report" / "adoption_probit"
OUT.mkdir(parents=True, exist_ok=True)

BLUE, INK, MUTED, GRID, SURF = "#2a78d6", "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"
CMAP_P = LinearSegmentedColormap.from_list(
    "pseq", ["#f4f7fb", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
CMAP_DIFF = LinearSegmentedColormap.from_list(
    "diff", ["#0d366b", "#3987e5", "#b7d3f6", "#f0efec",
             "#f5a983", "#eb6834", "#6e2a0c"])

NICE = {"etruscan": "Etruscan", "latin": "Latin", "greek": "Greek",
        "oscan": "Oscan", "messapic": "Messapic", "lepontic": "Lepontic",
        "elymian": "Elymian", "faliscan": "Faliscan", "venetic": "Venetic",
        "raetic": "Raetic"}
# atlas language colours (scripts/codebook.yml); Lepontic <- Cisalpine Celtic
SCRIPT_COLOR = {"etruscan": "#c8442b", "latin": "#d9a441", "greek": "#4f8fa8",
                "oscan": "#6d8f4a", "messapic": "#9a5fa8",
                "lepontic": "#3f7a6b", "elymian": "#b4693f",
                "faliscan": "#c07a9a", "venetic": "#4a6f9a",
                "raetic": "#7b6fa8"}


def style(ax, keep_grid=False):
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED, labelsize=7)
    if keep_grid:
        ax.grid(True, color=GRID, lw=0.5, alpha=0.8)
        ax.set_axisbelow(True)


def map_ax(ax):
    style(ax)
    ax.set_aspect(1.34)
    ax.set_xlim(6.4, 19.1)
    ax.set_ylim(36.4, 47.2)
    ax.set_xticks([])
    ax.set_yticks([])


def load():
    res = json.loads((ROOT / "db" / "adoption_scripts.json").read_text())
    g = np.load(ROOT / "db" / "adoption_scripts_grid.npz")
    names = list(res["models"])
    return res, g, names


# ------------------------------------------------------------------ figures
def fig_forest(res):
    order = sorted(res["models"],
                   key=lambda k: -res["models"][k]["trend_E_field"]["beta_E"])
    fig, ax = plt.subplots(figsize=(7.6, 6.4), facecolor=SURF)
    style(ax)
    ax.grid(True, axis="x", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    npos = res["outcome_positives"]
    for i, k in enumerate(order):
        for tag, dy, filled in [("trend_E", 0.17, False),
                                ("trend_E_field", -0.17, True)]:
            m = res["models"][k][tag]
            y = -(i + dy)
            lo, hi = m["ci95"]
            ax.plot([lo, hi], [y, y], color=BLUE, lw=2,
                    solid_capstyle="round", alpha=1.0 if filled else 0.55)
            ax.plot(m["beta_E"], y, "o", ms=7.5,
                    mfc=BLUE if filled else SURF, mec=BLUE, mew=1.5)
    ax.axvline(0, color="#c3c2b7", lw=1)
    ax.set_yticks([-i for i in range(len(order))],
                  [f"{NICE[k]}  (n={npos[k]})" for k in order],
                  fontsize=9.5, color=INK)
    ax.tick_params(axis="x", labelsize=8)
    ax.set_xlabel("probit slope on E  (per 1 sd of economic field)",
                  fontsize=9, color=MUTED)
    handles = [Line2D([], [], marker="o", ls="", mfc=SURF, mec=BLUE,
                      label="time trend + E"),
               Line2D([], [], marker="o", ls="", mfc=BLUE, mec=BLUE,
                      label="+ spatiotemporal field (conservative)")]
    ax.legend(handles=handles, frameon=False, fontsize=8, loc="lower right")
    ax.set_title("Which scripts sit where the economy is?  beta_E per "
                 "tradition, 95% CI\n(one-vs-rest probits; findspot-"
                 "clustered SE + posterior-E inflation)",
                 fontsize=10, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "beta_forest_all_scripts.png", dpi=180)
    plt.close(fig)


def fig_surfaces(g, names):
    slices = [-600, -400, -250, -100]
    fig, axes = plt.subplots(len(names), len(slices),
                             figsize=(9.0, 2.05 * len(names)),
                             facecolor=SURF)
    for r, k in enumerate(names):
        for c, t in enumerate(slices):
            ax = axes[r, c]
            m = g["bin_start"] == t
            sc = ax.scatter(g["lon"][m], g["lat"][m], c=g["p_" + k][m],
                            cmap=CMAP_P, vmin=0, vmax=1, s=3.2, marker="s",
                            lw=0)
            map_ax(ax)
            if r == 0:
                ax.set_title(f"{-t} BCE", fontsize=9, color=INK)
            if c == 0:
                ax.set_ylabel(NICE[k], fontsize=9, color=INK)
    fig.subplots_adjust(left=0.06, right=0.86, top=0.955, bottom=0.005,
                        hspace=0.08, wspace=0.04)
    cax = fig.add_axes([0.89, 0.4, 0.018, 0.2])
    cb = fig.colorbar(sc, cax=cax)
    cb.set_label("P(script)", fontsize=8, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    cb.outline.set_visible(False)
    fig.suptitle("Adoption surfaces, all script families "
                 "(posterior mean, one-vs-rest probits)",
                 fontsize=11, color=INK, y=0.985)
    fig.savefig(OUT / "surfaces_all_scripts.png", dpi=160,
                bbox_inches="tight")
    plt.close(fig)


def fig_dominant(g, names):
    slices = [-650, -500, -400, -300, -200, -100]
    cmap = ListedColormap([SCRIPT_COLOR[k] for k in names])
    fig, axes = plt.subplots(2, 3, figsize=(10.2, 6.8), facecolor=SURF)
    for ax, t in zip(axes.ravel(), slices):
        m = g["bin_start"] == t
        P = np.stack([g["eta_" + k][m] for k in names])
        dom = P.argmax(axis=0)
        ax.scatter(g["lon"][m], g["lat"][m], c=dom, cmap=cmap,
                   vmin=-0.5, vmax=len(names) - 0.5, s=8.5, marker="s", lw=0)
        map_ax(ax)
        ax.set_title(f"{-t} BCE", fontsize=10, color=INK)
    present = sorted({k for t in slices for k in np.array(names)[np.unique(
        np.stack([g["eta_" + n][g["bin_start"] == t] for n in names])
        .argmax(axis=0))]})
    handles = [Patch(facecolor=SCRIPT_COLOR[k], label=NICE[k])
               for k in names if k in present]
    fig.legend(handles=handles, frameon=False, fontsize=8.5,
               loc="lower center", ncols=min(len(handles), 6),
               bbox_to_anchor=(0.5, -0.015))
    fig.suptitle("The script landscape: most likely tradition per cell\n"
                 "(argmax of the one-vs-rest adoption surfaces)",
                 fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT / "dominant_script_map.png", dpi=170,
                bbox_inches="tight")
    plt.close(fig)


def fig_trajectories(g, names):
    bins = np.unique(g["bin_start"])
    mid = bins + 12.5
    curves = {k: np.array([g["p_" + k][g["bin_start"] == b].mean()
                           for b in bins]) for k in names}
    fig, axes = plt.subplots(2, 5, figsize=(11.5, 4.6), facecolor=SURF,
                             sharex=True, sharey=True)
    for ax, k in zip(axes.ravel(), names):
        style(ax, keep_grid=True)
        for j in names:                       # context spaghetti
            if j != k:
                ax.plot(mid, curves[j], color="#c3c2b7", lw=0.8, alpha=0.6)
        ax.plot(mid, curves[k], color=SCRIPT_COLOR[k], lw=2.2)
        ax.set_title(NICE[k], fontsize=9, color=INK)
        ax.set_ylim(0, 1)
        ax.set_xticks([-600, -300, -100], ["600", "300", "100"])
    fig.supxlabel("year BCE", fontsize=9, color=MUTED)
    fig.supylabel("mean P(script) over covered cells", fontsize=9,
                  color=MUTED)
    fig.suptitle("National adoption trajectories (grid-cell mean of the "
                 "fitted surfaces; grey = the other nine)",
                 fontsize=10.5, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "trajectories_all_scripts.png", dpi=180)
    plt.close(fig)


def fig_hovmoller(g, names):
    bins = np.unique(g["bin_start"])
    lats = np.unique(g["lat"])
    fig, axes = plt.subplots(2, 5, figsize=(11.5, 5.4), facecolor=SURF,
                             sharex=True, sharey=True)
    for ax, k in zip(axes.ravel(), names):
        H = np.full((len(lats), len(bins)), np.nan)
        for j, b in enumerate(bins):
            m = g["bin_start"] == b
            for i, la in enumerate(lats):
                mm = m & (g["lat"] == la)
                if mm.any():
                    H[i, j] = g["p_" + k][mm].mean()
        pm = ax.pcolormesh(bins + 12.5, lats, H, cmap=CMAP_P, vmin=0,
                           vmax=1, shading="nearest")
        style(ax)
        ax.set_title(NICE[k], fontsize=9, color=INK)
        ax.set_xticks([-600, -300, -100], ["600", "300", "100"])
    cb = fig.colorbar(pm, ax=axes, shrink=0.6, pad=0.015)
    cb.set_label("mean P(script) along latitude band", fontsize=8,
                 color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    cb.outline.set_visible(False)
    fig.suptitle("Hovmoller diagrams: latitude x time structure of each "
                 "tradition (year BCE on x, degrees N on y)",
                 fontsize=10.5, color=INK)
    fig.savefig(OUT / "hovmoller_all_scripts.png", dpi=170,
                bbox_inches="tight")
    plt.close(fig)


def fig_frontier(g):
    slices = [-500, -350, -200, -100]
    fig, axes = plt.subplots(1, len(slices), figsize=(11.5, 3.6),
                             facecolor=SURF)
    for ax, t in zip(axes, slices):
        m = g["bin_start"] == t
        d = g["eta_latin"][m] - g["eta_etruscan"][m]
        sc = ax.scatter(g["lon"][m], g["lat"][m], c=d, cmap=CMAP_DIFF,
                        vmin=-4, vmax=4, s=5.5, marker="s", lw=0)
        map_ax(ax)
        ax.set_title(f"{-t} BCE", fontsize=10, color=INK)
    cb = fig.colorbar(sc, ax=axes, shrink=0.8, pad=0.015)
    cb.set_label("probit difference,  orange = more Latin, "
                 "blue = more Etruscan", fontsize=8, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    cb.outline.set_visible(False)
    fig.suptitle("The Latinisation frontier: eta(Latin) - eta(Etruscan)",
                 fontsize=11, color=INK)
    fig.savefig(OUT / "latin_etruscan_frontier.png", dpi=170,
                bbox_inches="tight")
    plt.close(fig)


def main():
    res, g, names = load()
    fig_forest(res)
    fig_surfaces(g, names)
    fig_dominant(g, names)
    fig_trajectories(g, names)
    fig_hovmoller(g, names)
    fig_frontier(g)
    print(f"wrote 6 figures to {OUT}")


if __name__ == "__main__":
    main()
