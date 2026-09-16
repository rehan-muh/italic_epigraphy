#!/usr/bin/env python3
"""
Publication figures for the latent economic field E(s,t) (v0.3), for the
adoption paper: the field's spatiotemporal evolution.

    economy_field_evolution.png    posterior mean and sd maps, 4 epochs
    economy_trajectory_hovmoller.png
        (a) national land-mean trajectory with exact 95% band via the
            posterior basis covariance; (b) latitude x time Hovmoller

    python scripts/economy_field_figures.py   (matplotlib -> py 3.14)
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from economy_field_eval import EconomyField  # noqa: E402

OUT = ROOT / "report" / "adoption"
OUT.mkdir(parents=True, exist_ok=True)
CSV = ROOT / "data" / "economy" / "field_v03" / "economic_field_25yr_v03.csv"

INK, MUTED, GRID, SURF = "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"
CMAP_E = LinearSegmentedColormap.from_list(
    "ediv", ["#104281", "#3987e5", "#b7d3f6", "#f0efec",
             "#f2b3b2", "#e34948", "#8c1d1c"])
CMAP_SD = LinearSegmentedColormap.from_list(
    "sdseq", ["#f4f7fb", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab"])


def style(ax):
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED, labelsize=7)


def map_ax(ax):
    style(ax)
    ax.set_aspect(1.34)
    ax.set_xlim(6.4, 19.1)
    ax.set_ylim(36.4, 47.2)
    ax.set_xticks([])
    ax.set_yticks([])


STREAMS = [
    ("amphora_trade_site", "Amphora stamps", "#d1495b",
     "128 sites, 5,785 stamps -- 64.8% of field weight"),
    ("maritime_shipwreck", "Shipwrecks", "#1c5cab",
     "98 wrecks -- 15.5%"),
    ("urban_city", "Cities", "#7a5195",
     "372 centres -- 12.8%"),
    ("ancient_port", "Harbours", "#2e8b6e",
     "531 harbours -- 6.4%"),
    ("mining_production", "Mines", "#b8860b",
     "4 districts -- 0.5%"),
    ("archaeological_occupation", "Radiocarbon occupation", "#898781",
     "185 sites -- 0.01%"),
]


def fig_evidence(g):
    """The individual objects behind the field: one map per stream."""
    src = pd.read_csv(ROOT / "data" / "economy" / "source_points_all.csv")
    land = g[g.bin_start_year == -300]
    fig, axes = plt.subplots(2, 3, figsize=(10.8, 7.6), facecolor=SURF)
    for ax, (key, nice, col, sub) in zip(axes.ravel(), STREAMS):
        ax.scatter(land.longitude, land.latitude, c="#eceae3", s=5.5,
                   marker="s", lw=0, zorder=0)
        d = src[src.source_type == key]
        w = d.count_or_weight.fillna(1.0)
        if key == "amphora_trade_site":
            size = 4 + 5.5 * np.sqrt(w)
        elif key == "archaeological_occupation":
            size = 6 + 2.2 * w
        elif key == "mining_production":
            size = 90.0
        else:
            size = 13.0
        ax.scatter(d.longitude, d.latitude, s=size, color=col, alpha=0.65,
                   lw=0.4, edgecolor=SURF, zorder=2)
        map_ax(ax)
        ax.set_title(f"{nice}\n{sub}".replace("--", "—"),
                     fontsize=9, color=INK)
    fig.suptitle("The evidence behind the economic field: "
                 "six dated point streams", fontsize=11.5, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "economy_evidence_streams.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def fig_sequence(g):
    """Full posterior-mean sequence of the field at century steps."""
    times = [-700, -600, -500, -400, -300, -200, -100, -25]
    fig, axes = plt.subplots(2, 4, figsize=(10.8, 6.0), facecolor=SURF,
                             constrained_layout=True)
    for ax, t in zip(axes.ravel(), times):
        m = g.bin_start_year == t
        sc = ax.scatter(g.longitude[m], g.latitude[m], c=g.E_mean[m],
                        cmap=CMAP_E, vmin=-2.2, vmax=2.2, s=5.2,
                        marker="s", lw=0)
        map_ax(ax)
        lab = "2520131 BCE" if t == -25 else f"{-t} BCE"
        ax.set_title(lab, fontsize=9.5, color=INK)
    cb = fig.colorbar(sc, ax=axes, shrink=0.72, pad=0.01)
    cb.set_label("E (sd units)", fontsize=8, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    cb.outline.set_visible(False)
    fig.suptitle("The economic field century by century: posterior mean",
                 fontsize=11.5, color=INK)
    fig.savefig(OUT / "economy_field_sequence.png", dpi=180)
    plt.close(fig)


def main():
    g = pd.read_csv(CSV)
    g = g[(g.is_land) & (g.in_support)].copy()
    fig_evidence(g)
    fig_sequence(g)

    # ---- evolution maps: mean and sd at four epochs ------------------
    times = [-700, -500, -300, -100]
    fig, axes = plt.subplots(2, len(times), figsize=(10.8, 5.6),
                             facecolor=SURF, constrained_layout=True)
    for c, t in enumerate(times):
        m = g.bin_start_year == t
        sc1 = axes[0, c].scatter(g.longitude[m], g.latitude[m],
                                 c=g.E_mean[m], cmap=CMAP_E, vmin=-2.2,
                                 vmax=2.2, s=6.5, marker="s", lw=0)
        sc2 = axes[1, c].scatter(g.longitude[m], g.latitude[m],
                                 c=g.E_sd[m], cmap=CMAP_SD, vmin=0,
                                 vmax=1.0, s=6.5, marker="s", lw=0)
        for r in (0, 1):
            map_ax(axes[r, c])
        axes[0, c].set_title(f"{-t} BCE", fontsize=10, color=INK)
    axes[0, 0].set_ylabel("posterior mean", fontsize=9, color=INK)
    axes[1, 0].set_ylabel("posterior sd", fontsize=9, color=INK)
    for sc, row, lab in [(sc1, 0, "E (sd units)"), (sc2, 1, "sd")]:
        cb = fig.colorbar(sc, ax=axes[row, :], shrink=0.85, pad=0.01)
        cb.set_label(lab, fontsize=8, color=MUTED)
        cb.ax.tick_params(labelsize=7, colors=MUTED)
        cb.outline.set_visible(False)
    fig.suptitle("The latent economic field: posterior mean and "
                 "uncertainty, 700--100 BCE", fontsize=11, color=INK)
    fig.savefig(OUT / "economy_field_evolution.png", dpi=180)
    plt.close(fig)

    # ---- national trajectory (exact posterior band) + Hovmoller ------
    f = EconomyField()
    bins = np.sort(g.bin_start_year.unique())
    cells = g[g.bin_start_year == bins[0]][["longitude", "latitude"]]
    traj_m, traj_s = [], []
    for b in bins:
        yr = float(b) + 12.5
        Phi = f.basis(cells.longitude.values, cells.latitude.values,
                      np.full(len(cells), yr))
        v = Phi.mean(axis=0)                 # national averaging functional
        traj_m.append(float(v @ f.beta - f.offset))
        traj_s.append(float(np.sqrt(max(((v @ f.L) ** 2).sum(), 0.0))))
    traj_m, traj_s = np.array(traj_m), np.array(traj_s)

    lats = np.sort(g.latitude.unique())
    H = np.full((len(lats), len(bins)), np.nan)
    for j, b in enumerate(bins):
        gb = g[g.bin_start_year == b]
        for i, la in enumerate(lats):
            v = gb.E_mean[gb.latitude == la]
            if len(v):
                H[i, j] = v.mean()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 3.9),
                                   facecolor=SURF,
                                   gridspec_kw={"width_ratios": [1, 1.15]})
    style(ax1)
    ax1.grid(True, color=GRID, lw=0.6)
    ax1.set_axisbelow(True)
    mid = bins + 12.5
    ax1.fill_between(mid, traj_m - 1.96 * traj_s, traj_m + 1.96 * traj_s,
                     color="#9ec5f4", alpha=0.5, lw=0)
    ax1.plot(mid, traj_m, color="#1c5cab", lw=2.2)
    ax1.axhline(0, color="#c3c2b7", lw=1)
    ax1.set_xticks([-600, -400, -200],
                   ["600 BCE", "400", "200"])
    ax1.set_ylabel("national mean E (sd units)", fontsize=9, color=MUTED)
    ax1.set_title("National trajectory, 95% band", fontsize=10, color=INK)

    pm = ax2.pcolormesh(mid, lats, H, cmap=CMAP_E, vmin=-2.2, vmax=2.2,
                        shading="nearest")
    style(ax2)
    ax2.set_xticks([-600, -400, -200], ["600 BCE", "400", "200"])
    ax2.set_ylabel("degrees N", fontsize=9, color=MUTED)
    ax2.set_title("Latitude x time structure", fontsize=10, color=INK)
    cb = fig.colorbar(pm, ax=ax2, shrink=0.85, pad=0.02)
    cb.set_label("E", fontsize=8, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    cb.outline.set_visible(False)
    fig.suptitle("Spatiotemporal evolution of the economic field",
                 fontsize=11, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "economy_trajectory_hovmoller.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)
    print(f"wrote 4 figures to {OUT}")


if __name__ == "__main__":
    main()
