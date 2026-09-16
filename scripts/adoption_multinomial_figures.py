#!/usr/bin/env python3
"""
Figures for the full multinomial adoption model (adoption_multinomial.py).

Reads db/adoption_multinomial.json + db/adoption_multinomial_grid.npz and
writes to report/adoption/:

    beta_forest_multinomial.png     centred beta_E per category, 95% CI
    dominant_script_multinomial.png most probable tradition (true shares)
    shares_stacked_multinomial.png  national composition through time
    hovmoller_multinomial.png       latitude x time share structure

    python scripts/adoption_multinomial_figures.py   (matplotlib -> py 3.14)
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from adoption_scripts_figures import (BLUE, CMAP_P, GRID, INK, MUTED, NICE,
                                      SCRIPT_COLOR, SURF, map_ax,
                                      style)  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "report" / "adoption"
NICE = dict(NICE, other="Other")
SCRIPT_COLOR = dict(SCRIPT_COLOR, other="#898781")


def load():
    res = json.loads((ROOT / "db" / "adoption_multinomial.json").read_text())
    g = np.load(ROOT / "db" / "adoption_multinomial_grid.npz")
    names = [str(s) for s in g["names"]]
    return res, g, names


def fig_forest(res, names):
    order = sorted((k for k in names),
                   key=lambda k: -res["beta_E"][k]["beta_E_centred"])
    fig, ax = plt.subplots(figsize=(7.6, 6.6), facecolor=SURF)
    style(ax)
    ax.grid(True, axis="x", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for i, k in enumerate(order):
        m = res["beta_E"][k]
        lo, hi = m["ci95"]
        ax.plot([lo, hi], [-i, -i], color=BLUE, lw=2,
                solid_capstyle="round")
        ax.plot(m["beta_E_centred"], -i, "o", ms=7.5, mfc=BLUE, mec=BLUE)
    ax.axvline(0, color="#c3c2b7", lw=1)
    ax.set_yticks([-i for i in range(len(order))],
                  [NICE[k] for k in order], fontsize=9.5, color=INK)
    ax.tick_params(axis="x", labelsize=8)
    ax.set_xlabel("multinomial slope on E: change in log-share per 1 sd "
                  "of the economic field\n(relative to the all-category "
                  "geometric mean)", fontsize=9, color=MUTED)
    ax.set_title("Economy and script choice in the full multinomial "
                 "model, 95% CI\n(findspot-clustered sandwich + "
                 "posterior-E inflation)", fontsize=10, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "beta_forest_multinomial.png", dpi=180)
    plt.close(fig)


def fig_dominant(g, names):
    slices = [-650, -500, -400, -300, -200, -100]
    cmap = ListedColormap([SCRIPT_COLOR[k] for k in names])
    fig, axes = plt.subplots(2, 3, figsize=(10.2, 6.8), facecolor=SURF)
    present = set()
    for ax, t in zip(axes.ravel(), slices):
        m = g["bin_start"] == t
        P = np.stack([g["p_" + k][m] for k in names])
        dom = P.argmax(axis=0)
        present |= {names[i] for i in np.unique(dom)}
        ax.scatter(g["lon"][m], g["lat"][m], c=dom, cmap=cmap,
                   vmin=-0.5, vmax=len(names) - 0.5, s=8.5, marker="s",
                   lw=0)
        map_ax(ax)
        ax.set_title(f"{-t} BCE", fontsize=10, color=INK)
    handles = [Patch(facecolor=SCRIPT_COLOR[k], label=NICE[k])
               for k in names if k in present]
    fig.legend(handles=handles, frameon=False, fontsize=8.5,
               loc="lower center", ncols=min(len(handles), 6),
               bbox_to_anchor=(0.5, -0.015))
    fig.suptitle("The script landscape under the multinomial model: "
                 "highest-share tradition per cell\n(shares sum to 1 "
                 "across the eleven categories)", fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT / "dominant_script_multinomial.png", dpi=170,
                bbox_inches="tight")
    plt.close(fig)


def fig_stacked(g, names):
    bins = np.unique(g["bin_start"])
    mid = bins + 12.5
    shares = np.stack([[g["p_" + k][g["bin_start"] == b].mean()
                        for b in bins] for k in names])
    shares = shares / shares.sum(axis=0, keepdims=True)
    order = np.argsort(-shares[:, 0])          # stack by early share
    fig, ax = plt.subplots(figsize=(8.6, 4.4), facecolor=SURF)
    style(ax, keep_grid=False)
    ax.stackplot(mid, shares[order],
                 colors=[SCRIPT_COLOR[names[i]] for i in order],
                 labels=[NICE[names[i]] for i in order], lw=0.4,
                 edgecolor=SURF)
    ax.set_xlim(mid[0], mid[-1])
    ax.set_ylim(0, 1)
    ax.set_xticks([-600, -450, -300, -150],
                  ["600 BCE", "450", "300", "150"])
    ax.set_ylabel("share of epigraphic production", fontsize=9,
                  color=MUTED)
    ax.legend(frameon=False, fontsize=7.5, ncols=4, loc="upper center",
              bbox_to_anchor=(0.5, -0.12))
    ax.set_title("National composition of the epigraphic record "
                 "(mean multinomial shares over covered cells)",
                 fontsize=10.5, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "shares_stacked_multinomial.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def fig_hovmoller(g, names):
    bins = np.unique(g["bin_start"])
    lats = np.unique(g["lat"])
    plot_names = [k for k in names if k != "other"]
    fig, axes = plt.subplots(2, 5, figsize=(11.5, 5.4), facecolor=SURF,
                             sharex=True, sharey=True)
    for ax, k in zip(axes.ravel(), plot_names):
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
    cb.set_label("mean share along latitude band", fontsize=8, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    cb.outline.set_visible(False)
    fig.suptitle("Hovmoller diagrams of the multinomial shares "
                 "(year BCE on x, degrees N on y)", fontsize=10.5,
                 color=INK)
    fig.savefig(OUT / "hovmoller_multinomial.png", dpi=170,
                bbox_inches="tight")
    plt.close(fig)


def fig_frontier(g):
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list(
        "diff", ["#0d366b", "#3987e5", "#b7d3f6", "#f0efec",
                 "#f5a983", "#eb6834", "#6e2a0c"])
    slices = [-500, -350, -200, -100]
    fig, axes = plt.subplots(1, len(slices), figsize=(11.5, 3.6),
                             facecolor=SURF)
    for ax, t in zip(axes, slices):
        m = g["bin_start"] == t
        d = (np.log(np.clip(g["p_latin"][m], 1e-12, None))
             - np.log(np.clip(g["p_etruscan"][m], 1e-12, None)))
        sc = ax.scatter(g["lon"][m], g["lat"][m], c=d, cmap=cmap,
                        vmin=-8, vmax=8, s=5.5, marker="s", lw=0)
        map_ax(ax)
        ax.set_title(f"{-t} BCE", fontsize=10, color=INK)
    cb = fig.colorbar(sc, ax=axes, shrink=0.8, pad=0.015)
    cb.set_label("log share ratio,  orange = more Latin, "
                 "blue = more Etruscan", fontsize=8, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    cb.outline.set_visible(False)
    fig.suptitle("The Latinisation frontier: log P(Latin) / P(Etruscan), "
                 "multinomial shares", fontsize=11, color=INK)
    fig.savefig(OUT / "latin_etruscan_frontier_multinomial.png", dpi=170,
                bbox_inches="tight")
    plt.close(fig)


def fig_maps(g):
    """E field vs Latin and Greek share surfaces at three moments."""
    from matplotlib.colors import LinearSegmentedColormap
    cmap_e = LinearSegmentedColormap.from_list(
        "ediv", ["#104281", "#3987e5", "#b7d3f6", "#f0efec",
                 "#f2b3b2", "#e34948", "#8c1d1c"])
    cmap_lat = LinearSegmentedColormap.from_list(
        "latseq", ["#fcf5f1", "#fde3d7", "#f5a983", "#eb6834", "#b84515",
                   "#6e2a0c"])
    times = [-500, -300, -100]
    fig, axes = plt.subplots(len(times), 3, figsize=(10.5, 11.5),
                             facecolor=SURF, constrained_layout=True)
    for r, t in enumerate(times):
        m = g["bin_start"] == t
        panels = [("E_mean", cmap_e, (-2.2, 2.2), "economic field E"),
                  ("p_latin", cmap_lat, (0, 1.0), "Latin share"),
                  ("p_greek", CMAP_P, (0, 0.8), "Greek share")]
        for c, (key, cmap, (v0, v1), label) in enumerate(panels):
            ax = axes[r, c]
            sc = ax.scatter(g["lon"][m], g["lat"][m], c=g[key][m],
                            cmap=cmap, vmin=v0, vmax=v1, s=7, marker="s",
                            lw=0)
            map_ax(ax)
            if r == 0:
                ax.set_title(label, fontsize=10, color=INK)
            if c == 0:
                ax.set_ylabel(f"{-t} BCE", fontsize=11, color=INK)
            cb = fig.colorbar(sc, ax=ax, shrink=0.75, pad=0.02)
            cb.ax.tick_params(labelsize=7, colors=MUTED)
            cb.outline.set_visible(False)
    fig.suptitle("Latent economic field and multinomial share surfaces",
                 fontsize=11, color=INK)
    fig.savefig(OUT / "maps_adoption_vs_economy_multinomial.png", dpi=180)
    plt.close(fig)


def fig_century(res_script, res_lang):
    """Per-century correlation of Latin/Greek log-share with E, script
    vs language models."""
    from matplotlib.lines import Line2D
    ORANGE = "#eb6834"
    fig, ax = plt.subplots(figsize=(7.2, 4.0), facecolor=SURF)
    style(ax, keep_grid=True)
    for res, ls in [(res_script, "-"), (res_lang, (0, (4, 2.5)))]:
        corr = res["grid"]["logshare_vs_E_correlation"]
        for k, col in [("latin", ORANGE), ("greek", BLUE)]:
            per = corr[k]["by_century"]
            cents = sorted(int(c) for c in per)
            ax.plot([c + 50 for c in cents],
                    [per[str(c)] for c in cents], color=col, lw=2, ls=ls,
                    marker="o", ms=4.5, mfc=SURF, mew=1.4, mec=col)
    ax.axhline(0, color="#c3c2b7", lw=1)
    ax.set_xlabel("century (BCE)", fontsize=9, color=MUTED)
    ax.set_ylabel("Pearson r, centred log-share vs E", fontsize=9,
                  color=MUTED)
    ax.set_xticks(range(-700, 0, 100),
                  [f"{-c}s" for c in range(-700, 0, 100)])
    handles = [Line2D([], [], color=ORANGE, lw=2, label="Latin"),
               Line2D([], [], color=BLUE, lw=2, label="Greek"),
               Line2D([], [], color=INK, lw=2, label="script"),
               Line2D([], [], color=INK, lw=2, ls=(0, (4, 2.5)),
                      label="language")]
    ax.legend(handles=handles, frameon=False, fontsize=8, ncols=2)
    ax.set_title("Alignment of adoption with the economy through time",
                 fontsize=10, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "correlation_by_century_multinomial.png", dpi=180)
    plt.close(fig)


def main():
    res, g, names = load()
    fig_forest(res, names)
    fig_dominant(g, names)
    fig_stacked(g, names)
    fig_hovmoller(g, names)
    fig_frontier(g)
    fig_maps(g)
    lang_json = ROOT / "db" / "adoption_multinomial_lang.json"
    if lang_json.exists():
        fig_century(res, json.loads(lang_json.read_text()))
        print(f"wrote 7 figures to {OUT}")
    else:
        print(f"wrote 6 figures to {OUT} (language JSON not found)")


if __name__ == "__main__":
    main()
